"""The web service: login, dashboard data, tender browsing, manual runs and settings, plus the built React page."""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import appsettings, mailer
from .config import Settings, load_settings
from .digest import CSV_COLUMNS, format_inr, safe_cell
from .runner import Busy, NotConfigured, RunManager, Scheduler, local_now
from .store import SORTS, SeenStore

log = logging.getLogger("tender_digest.web")
COOKIE = "td_session"
SESSION_SECONDS = 7 * 24 * 3600
MAX_EXPORT_ROWS = 20_000


@dataclass
class AppConfig:
    password: str
    secret_key: str
    cookie_secure: bool = True
    tz_name: str = "Asia/Kolkata"
    static_dir: Path | None = None
    run_scheduler: bool = True


def config_from_env() -> AppConfig:
    password = os.environ.get("APP_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("APP_PASSWORD must be set to a password of at least 8 characters. It protects the dashboard and the Run now button.")
    key = os.environ.get("SECRET_KEY", "")
    if not key:
        key = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set: a temporary one is used, so everyone is logged out whenever the service restarts. Set SECRET_KEY.")
    return AppConfig(
        password=password, secret_key=key, cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() not in ("0", "false", "no"),
        tz_name=os.environ.get("TZ_NAME", "Asia/Kolkata"),
        static_dir=Path(os.environ.get("STATIC_DIR") or Path(__file__).resolve().parent.parent / "web" / "dist"),
    )


# ---- session cookie: expiry plus an HMAC, nothing else to look up --------------------------------------------

def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(secret: str, now: float | None = None) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int((now or time.time()) + SESSION_SECONDS)}).encode()).decode()
    return f"{payload}.{_sign(secret, payload)}"


def valid_token(secret: str, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(secret, payload)):
        return False
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode()))["exp"] > (now or time.time())
    except (ValueError, KeyError, TypeError):
        return False


class LoginLimiter:
    """Slows password guessing: per client address, and overall, since an address header can be faked."""

    def __init__(self, per_client: int = 8, overall: int = 40, window: int = 600):
        self.per_client, self.overall, self.window = per_client, overall, window
        self.fails: list[tuple[float, str]] = []

    def _recent(self, now: float) -> list[tuple[float, str]]:
        self.fails = [(t, c) for t, c in self.fails if now - t < self.window]
        return self.fails

    def blocked(self, client: str, now: float | None = None) -> bool:
        now = now or time.time()
        recent = self._recent(now)
        return len(recent) >= self.overall or sum(1 for _, c in recent if c == client) >= self.per_client

    def failed(self, client: str, now: float | None = None) -> None:
        self.fails.append((now or time.time(), client))

    def succeeded(self, client: str) -> None:
        self.fails = [(t, c) for t, c in self.fails if c != client]


class LoginBody(BaseModel):
    password: str


class RunBody(BaseModel):
    portals: list[str] | None = None
    send_email: bool = False


def create_app(config: AppConfig, env: Settings, store: SeenStore, manager: RunManager | None = None) -> FastAPI:
    manager = manager or RunManager(env, store, tz_name=config.tz_name)
    scheduler = Scheduler(manager) if config.run_scheduler else None
    limiter = LoginLimiter()
    portal_names = {p.key: p.name for p in env.portals}
    portal_states = {p.key: p.state for p in env.portals}
    portal_search = {p.key: p.base_url.rstrip("/") + "/app?page=FrontEndAdvancedSearch&service=page" for p in env.portals}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        n = store.fail_stale_runs(local_now(config.tz_name))
        if n:
            log.warning("marked %d interrupted run(s) as failed", n)
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop()

    app = FastAPI(title="Tender digest", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # A browser sends Origin on cross-site writes; refuse any that is not this site.
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def client_of(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")) or "?"

    def require_login(request: Request) -> None:
        if not valid_token(config.secret_key, request.cookies.get(COOKIE)):
            raise HTTPException(401, "Please log in")

    auth = [Depends(require_login)]

    def now() -> datetime:
        return local_now(config.tz_name)

    # ---- auth ------------------------------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict:
        client = client_of(request)
        if limiter.blocked(client):
            raise HTTPException(429, "Too many wrong attempts. Wait a few minutes and try again.")
        if not hmac.compare_digest(body.password.encode(), config.password.encode()):
            limiter.failed(client)
            raise HTTPException(401, "Wrong password")
        limiter.succeeded(client)
        response.set_cookie(COOKIE, make_token(config.secret_key), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        return {"authenticated": valid_token(config.secret_key, request.cookies.get(COOKIE))}

    # ---- data ------------------------------------------------------------------------------------------------

    @app.get("/api/config", dependencies=auth)
    def get_config() -> dict:
        cur = appsettings.current(env, store.get_settings())
        return {
            "portals": [{"key": p.key, "name": p.name, "state": p.state, "search_url": portal_search[p.key]} for p in env.portals],
            "mail_configured": manager.mail_configured, "recipient_count": len(cur["recipients"]),
            "now": now().isoformat(timespec="seconds"), "timezone": config.tz_name,
        }

    @app.get("/api/stats", dependencies=auth)
    def stats() -> dict:
        data = store.stats(now(), env.closing_soon_days)
        for row in data["by_portal"]:
            row["name"] = portal_names.get(row["portal"], row["portal"])
            row["short"] = portal_states.get(row["portal"], row["portal"])
        for row in data["departments"]:
            row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        for row in data["biggest"]:
            row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        last = store.list_runs(1)
        data["last_run"] = last[0] if last else None
        data["soon_days"] = env.closing_soon_days
        return data

    def shape(row: dict) -> dict:
        row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        row["search_url"] = portal_search.get(row["portal"], "")
        row["value_text"] = format_inr(row["value"])
        return row

    def filters(portal: str | None, q: str | None, category: str | None, status: str, min_value: float | None, max_value: float | None, since_days: int | None) -> dict:
        if status not in ("open", "closing_soon", "closed", "all"):
            raise HTTPException(422, "Unknown status")
        return {"portal": portal, "q": q, "category": category, "status": status, "min_value": min_value, "max_value": max_value,
                "since_days": since_days, "soon_days": env.closing_soon_days}

    @app.get("/api/tenders", dependencies=auth)
    def tenders(
        portal: str | None = None, q: str | None = Query(None, max_length=100), category: str | None = None, status: str = "open",
        min_value: float | None = None, max_value: float | None = None, since_days: int | None = Query(None, ge=1, le=365),
        sort: str = "closing", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    ) -> dict:
        if sort not in SORTS:
            raise HTTPException(422, "Unknown sort")
        rows, total = store.search(now(), filters(portal, q, category, status, min_value, max_value, since_days), sort, page_size, (page - 1) * page_size)
        return {"items": [shape(r) for r in rows], "total": total, "page": page, "page_size": page_size, "categories": store.categories()}

    @app.get("/api/tenders/export", dependencies=auth)
    def export(
        portal: str | None = None, q: str | None = Query(None, max_length=100), category: str | None = None, status: str = "open",
        min_value: float | None = None, max_value: float | None = None, since_days: int | None = Query(None, ge=1, le=365), sort: str = "closing",
    ) -> Response:
        if sort not in SORTS:
            raise HTTPException(422, "Unknown sort")
        rows, _ = store.search(now(), filters(portal, q, category, status, min_value, max_value, since_days), sort, MAX_EXPORT_ROWS, 0)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow([safe_cell(c) for c in [
                "Open" if not r["closing"] or r["closing"] >= now().isoformat() else "Closed", portal_names.get(r["portal"], r["portal"]), r["tender_id"],
                r["description"] or r["title"], (r["organisation"] or "").replace("||", " > "), r["category"], r["location"],
                "" if r["value"] is None else int(r["value"]), "" if r["emd"] is None else int(r["emd"]), r["published"] or "", r["closing"] or "",
            ]])
        return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="tenders-{now().strftime("%Y-%m-%d")}.csv"'})

    # ---- runs ------------------------------------------------------------------------------------------------

    @app.get("/api/runs", dependencies=auth)
    def runs() -> dict:
        current = store.running_run()
        return {"runs": store.list_runs(30), "running": current["id"] if current else None}

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def run_detail(run_id: int) -> dict:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "No such run")
        return run

    @app.post("/api/runs", status_code=202, dependencies=auth)
    def start_run(body: RunBody) -> dict:
        if body.portals is not None:
            unknown = [p for p in body.portals if p not in portal_names]
            if unknown or not body.portals:
                raise HTTPException(422, "Choose at least one known portal")
        try:
            return {"id": manager.start("manual", body.portals, body.send_email)}
        except Busy as e:
            raise HTTPException(409, str(e)) from None
        except NotConfigured as e:
            raise HTTPException(422, str(e)) from None

    # ---- settings --------------------------------------------------------------------------------------------

    @app.get("/api/settings", dependencies=auth)
    def get_settings() -> dict:
        return {**appsettings.current(env, store.get_settings()), "mail_configured": manager.mail_configured, "mail_from": env.mail_from,
                "available_portals": [{"key": p.key, "name": p.name} for p in env.portals]}

    @app.put("/api/settings", dependencies=auth)
    def put_settings(body: dict[str, Any]) -> dict:
        try:
            store.put_settings(appsettings.clean(body, list(portal_names)))
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        return get_settings()

    @app.post("/api/settings/test-email", dependencies=auth)
    def test_email() -> dict:
        try:
            return {"sent_to": manager.send_test()}
        except NotConfigured as e:
            raise HTTPException(422, str(e)) from None
        except mailer.MailError as e:
            # Brevo's reason (unverified sender, IP restriction, bad key) is what the person needs to fix it; it never contains the key.
            raise HTTPException(502, f"Brevo refused the email: {str(e)[:300]}") from None

    # ---- the React page --------------------------------------------------------------------------------------

    static = config.static_dir
    if static and (static / "index.html").exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            target = (static / path).resolve()
            if path and target.is_file() and static.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(static / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_ui() -> PlainTextResponse:
            return PlainTextResponse("The web page has not been built. Run `npm run build` in web/, or use the Docker image.", status_code=503)

    app.state.manager, app.state.scheduler = manager, scheduler
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = config_from_env()
    env = load_settings(require_mail=False)
    store = SeenStore(env.state_path)
    app = create_app(config, env, store)
    # One worker only: the scheduler and the run lock live in this process.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
