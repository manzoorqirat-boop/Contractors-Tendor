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
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import access, ai, appsettings, mailer
from .bids import STAGE_LABELS, STAGES, VAULT_KINDS, BidBook, BidError, clean_patch, default_vault_dir
from .config import Portal, Settings, load_settings
from .digest import CSV_COLUMNS, format_inr, safe_cell
from .runner import Busy, NotConfigured, RunManager, Scheduler, local_now
from .store import DIMENSIONS, SORTS, SeenStore

log = logging.getLogger("tender_digest.web")
COOKIE = "td_session"
SESSION_SECONDS = 7 * 24 * 3600
MAX_EXPORT_ROWS = 20_000
MAX_VIEWS = 30
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?)?$")
# The settings a saved view may hold: the page's filters and display choices, nothing else.
VIEW_KEYS = {
    "portal", "category", "department", "location", "work_type", "min_fit", "q", "status", "min_value", "max_value", "value_lt", "no_value", "no_closing", "no_published", "starred",
    "closing_from", "closing_to", "seen_from", "seen_to", "published_from", "published_to", "closing_within", "since_days", "sort",
    "group_by", "split_by", "measure", "chart", "page",
}


def clean_ask(raw: dict, portal_keys: set[str]) -> dict:
    """Claude's guess at filters, reduced to values the filters accept. Anything else is dropped."""
    f: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return {"filters": f, "sort": None, "explanation": ""}
    if isinstance(raw.get("portal"), list):
        f["portal"] = [p for p in raw["portal"] if p in portal_keys]
    for k in ("category", "department", "location", "work_type"):
        if isinstance(raw.get(k), list):
            f[k] = [str(v)[:200] for v in raw[k]][:20]
    if isinstance(raw.get("q"), str) and raw["q"].strip():
        f["q"] = raw["q"].strip()[:100]
    if raw.get("status") in ("open", "closing_soon", "closed", "all"):
        f["status"] = raw["status"]
    for k in ("min_value", "max_value"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e13:
            f[k] = v
    for k in ("closing_within", "since_days"):
        v = raw.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 365:
            f[k] = v
    if raw.get("starred") is True:
        f["starred"] = True
    f = {k: v for k, v in f.items() if v not in ([], "", None)}
    sort = raw.get("sort") if raw.get("sort") in SORTS else None
    return {"filters": f, "sort": sort, "explanation": str(raw.get("explanation") or "")[:200]}


@dataclass
class AppConfig:
    password: str
    secret_key: str
    cookie_secure: bool = True
    tz_name: str = "Asia/Kolkata"
    static_dir: Path | None = None
    run_scheduler: bool = True
    username: str = ""  # empty = any username accepted (tests); config_from_env defaults it to "admin"


def config_from_env() -> AppConfig:
    password = os.environ.get("APP_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("APP_PASSWORD must be set to a password of at least 8 characters. It protects the dashboard and the Run now button.")
    key = os.environ.get("SECRET_KEY", "")
    if not key:
        key = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set: a temporary one is used, so everyone is logged out whenever the service restarts. Set SECRET_KEY.")
    return AppConfig(
        password=password, secret_key=key, username=os.environ.get("APP_USERNAME", "admin").strip(), cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() not in ("0", "false", "no"),
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
    username: str = ""
    password: str


class SummaryBody(BaseModel):
    key: str
    refresh: bool = False


class AskBody(BaseModel):
    question: str


class DocBody(BaseModel):
    key: str
    text: str = ""
    pdf_base64: str = ""


class StarBody(BaseModel):
    key: str
    starred: bool


class ViewItem(BaseModel):
    id: str
    name: str
    filters: dict[str, Any]


class ViewsBody(BaseModel):
    views: list[ViewItem]


class BidCreate(BaseModel):
    key: str


class ItemBody(BaseModel):
    text: str
    kind: str = "task"


class DraftBody(BaseModel):
    kind: str
    instructions: str = ""
    refresh: bool = False


class VaultBody(BaseModel):
    name: str = ""
    kind: str = "other"
    issued: str = ""
    expiry: str = ""
    notes: str = ""
    filename: str = ""
    file_base64: str = ""


class RunBody(BaseModel):
    portals: list[str] | None = None
    send_email: bool = False


def create_app(config: AppConfig, env: Settings, store: SeenStore, manager: RunManager | None = None, prober: Callable[[str], dict] | None = None) -> FastAPI:
    vault_dir = default_vault_dir(env.state_path)
    bids = BidBook(store, vault_dir)
    manager = manager or RunManager(env, store, tz_name=config.tz_name, bids=bids)
    scheduler = Scheduler(manager) if config.run_scheduler else None
    limiter = LoginLimiter()
    probe = prober or access.probe
    builtin_keys = {p.key for p in env.portals}
    # Portals typed into the page are saved in the database, so these lookups are refilled (in place) whenever they change.
    portal_names: dict[str, str] = {}
    portal_states: dict[str, str] = {}
    portal_search: dict[str, str] = {}

    def portals_now() -> list[Portal]:
        return appsettings.all_portals(env, store.get_settings())

    def refresh_portals() -> None:
        ps = portals_now()
        for d, value in ((portal_names, lambda p: p.name), (portal_states, lambda p: p.state),
                         (portal_search, lambda p: p.base_url.rstrip("/") + "/app?page=FrontEndAdvancedSearch&service=page")):
            d.clear()
            d.update({p.key: value(p) for p in ps})

    refresh_portals()

    def portal_entry(p: Portal, saved: dict[str, Any], statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
        st = statuses.get(p.key)
        return {
            "key": p.key, "name": p.name, "state": p.state, "base_url": p.base_url, "custom": p.key not in builtin_keys,
            "enabled": p.key in appsettings.current(env, saved)["portals"], **access.describe(st["kind"] if st else "unknown"),
            "detail": st["detail"] if st else "", "checked": st["checked"] if st else None,
            "departments": st["departments"] if st else 0, "tenders": st["tenders"] if st else 0,
        }

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

    app = FastAPI(title="TendAI by QMSofts", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

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
        user_ok = not config.username or hmac.compare_digest(body.username.strip().lower().encode(), config.username.lower().encode())
        pass_ok = hmac.compare_digest(body.password.encode(), config.password.encode())
        if not (user_ok and pass_ok):
            limiter.failed(client)
            raise HTTPException(401, "Wrong username or password")
        limiter.succeeded(client)
        response.set_cookie(COOKIE, make_token(config.secret_key), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/branding")
    def branding() -> dict:
        # Public on purpose: the login screen shows the logo before anyone has signed in. Nothing secret is in it.
        return {"logo_text": appsettings.current(env, store.get_settings())["logo_text"]}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        return {"authenticated": valid_token(config.secret_key, request.cookies.get(COOKIE))}

    # ---- data ------------------------------------------------------------------------------------------------

    def filter_params(
        portal: list[str] | None = Query(None), category: list[str] | None = Query(None), department: list[str] | None = Query(None),
        location: list[str] | None = Query(None), work_type: list[str] | None = Query(None), min_fit: int | None = Query(None, ge=1, le=5), q: str | None = Query(None, max_length=100), status: str = "open",
        min_value: float | None = Query(None, ge=0), max_value: float | None = Query(None, ge=0), value_lt: float | None = Query(None, ge=0),
        no_value: bool = False, no_closing: bool = False, no_published: bool = False, starred: bool = False,
        closing_from: str | None = None, closing_to: str | None = None, seen_from: str | None = None, seen_to: str | None = None,
        published_from: str | None = None, published_to: str | None = None,
        closing_within: int | None = Query(None, ge=1, le=365), since_days: int | None = Query(None, ge=1, le=365),
    ) -> dict:
        if status not in ("open", "closing_soon", "closed", "all"):
            raise HTTPException(422, "Unknown status")
        dates = dict(closing_from=closing_from, closing_to=closing_to, seen_from=seen_from, seen_to=seen_to, published_from=published_from, published_to=published_to)
        for name, value in dates.items():
            if value and not DATE_RE.match(value):
                raise HTTPException(422, f"{name} must look like 2026-11-20")
        lists = {"portal": portal, "category": category, "department": department, "location": location, "work_type": work_type}
        for name, values in lists.items():
            if values and (len(values) > 50 or any(len(v) > 200 for v in values)):
                raise HTTPException(422, f"Too many or too long values for {name}")
        return {
            **{k: v for k, v in lists.items() if v}, "q": q, "status": status, "min_value": min_value, "max_value": max_value, "value_lt": value_lt,
            "no_value": no_value, "no_closing": no_closing, "no_published": no_published, "starred": starred, "closing_within": closing_within, "since_days": since_days, "min_fit": min_fit,
            "soon_days": env.closing_soon_days, **{k: v for k, v in dates.items() if v},
        }

    fparams = Depends(filter_params)

    @app.get("/api/config", dependencies=auth)
    def get_config() -> dict:
        cur = appsettings.current(env, store.get_settings())
        return {
            "portals": [{"key": p.key, "name": p.name, "state": p.state, "search_url": portal_search[p.key]} for p in portals_now()],
            "mail_configured": manager.mail_configured, "recipient_count": len(cur["recipients"]),
            "now": now().isoformat(timespec="seconds"), "timezone": config.tz_name,
            "dimensions": [{"key": k, "label": d["label"], "time": isinstance(d["order"], str) and d["order"] in ("first", "last")} for k, d in DIMENSIONS.items()],
            "soon_days": env.closing_soon_days, "ai_enabled": ai.configured(),
        }

    @app.get("/api/stats", dependencies=auth)
    def stats(f: dict = fparams) -> dict:
        data = store.stats(now(), env.closing_soon_days, f)
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

    @app.get("/api/analytics", dependencies=auth)
    def analytics(group_by: str, split_by: str | None = None, top: int = Query(12, ge=3, le=40), f: dict = fparams) -> dict:
        if group_by not in DIMENSIONS or (split_by and split_by not in DIMENSIONS):
            raise HTTPException(422, "Unknown dimension")
        if split_by == group_by:
            split_by = None
        result = store.pivot(now(), f, group_by, split_by, top)
        result["labels"] = {k: portal_states.get(k, k) for k in portal_names} if "portal" in (group_by, split_by) else {}
        return result

    @app.get("/api/facets", dependencies=auth)
    def facets() -> dict:
        data = store.facets(now())
        for row in data["portal"]:
            row["label"] = portal_names.get(row["value"], row["value"])
        return data

    def shape(row: dict) -> dict:
        row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        row["search_url"] = portal_search.get(row["portal"], "")
        row["value_text"] = format_inr(row["value"])
        return row

    @app.get("/api/tenders", dependencies=auth)
    def tenders(f: dict = fparams, sort: str = "closing", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
        if sort not in SORTS:
            raise HTTPException(422, "Unknown sort")
        rows, total = store.search(now(), f, sort, page_size, (page - 1) * page_size)
        return {"items": [shape(r) for r in rows], "total": total, "page": page, "page_size": page_size, "categories": store.categories()}

    @app.get("/api/tenders/export", dependencies=auth)
    def export(f: dict = fparams, sort: str = "closing") -> Response:
        if sort not in SORTS:
            raise HTTPException(422, "Unknown sort")
        rows, _ = store.search(now(), f, sort, MAX_EXPORT_ROWS, 0)
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

    @app.get("/api/tenders/detail", dependencies=auth)
    def tender_detail(key: str = Query(..., max_length=300)) -> dict:
        row = store.get_tender(key)
        if not row:
            raise HTTPException(404, "No such tender")
        for field in ("brief", "doc"):
            row[field] = json.loads(row[field]) if row.get(field) else None
        row["related"] = store.related(key)
        row["bid_id"] = bids.id_for_key(key)
        row["history"] = bids.history_notes(row.get("work_type"), row.get("organisation"), row.get("value"))
        return shape(row)

    def ai_call(fn, *args):
        try:
            return fn(*args)
        except ai.NotConfigured as e:
            raise HTTPException(503, str(e)) from None
        except ai.AIError as e:
            raise HTTPException(502, str(e)) from None

    def profile() -> str:
        return str(store.get_settings().get("company_profile", "") or "")

    def tender_for_ai(key: str) -> dict:
        row = store.get_tender(key)
        if not row:
            raise HTTPException(404, "No such tender")
        return shape(row)

    @app.post("/api/tenders/summary", dependencies=auth)
    def tender_summary(body: SummaryBody) -> dict:
        row = tender_for_ai(body.key)
        if row.get("summary") and not body.refresh:
            return {"summary": row["summary"], "cached": True}
        text = ai_call(ai.summarise, row)
        store.set_ai_text(body.key, "summary", text)
        return {"summary": text, "cached": False}

    @app.post("/api/tenders/document", dependencies=auth)
    def tender_document(body: DocBody) -> dict:
        row = tender_for_ai(body.key)
        doc = ai_call(ai.read_document, row, body.text, body.pdf_base64)
        store.set_ai_text(body.key, "doc", json.dumps(doc))
        return {"doc": doc}

    @app.post("/api/tenders/brief", dependencies=auth)
    def tender_brief(body: SummaryBody) -> dict:
        row = tender_for_ai(body.key)
        if row.get("brief") and not body.refresh:
            return {"brief": json.loads(row["brief"]), "cached": True}
        doc = json.loads(row["doc"]) if row.get("doc") else None
        hist = bids.history_notes(row.get("work_type"), row.get("organisation"), row.get("value"))
        out = ai_call(ai.brief, row, profile(), doc, *([hist] if hist else []))  # history is passed only when there is some
        store.set_ai_text(body.key, "brief", json.dumps(out))
        return {"brief": out, "cached": False}

    @app.post("/api/ask", dependencies=auth)
    def ask(body: AskBody) -> dict:
        question = body.question.strip()
        if not question:
            raise HTTPException(422, "Type a question first")
        fac = store.facets(now())
        raw = ai_call(ai.ask, question, [{"key": k, "name": n} for k, n in portal_names.items()],
                      {k: [f["value"] for f in fac.get(k, [])] for k in ("category", "department", "location")}, now().date().isoformat())
        return clean_ask(raw, set(portal_names))

    @app.get("/api/ai/status", dependencies=auth)
    def ai_status() -> dict:
        return {"configured": ai.configured(), "model": ai.model(), "fast_model": ai.fast_model(), "profile_set": bool(profile().strip()), **store.ai_counts(now())}

    @app.post("/api/ai/test", dependencies=auth)
    def ai_test() -> dict:
        ai_call(ai.ping)
        return {"ok": True, "model": ai.model(), "fast_model": ai.fast_model()}

    @app.post("/api/ai/score", dependencies=auth)
    def ai_score() -> dict:
        if not ai.configured():
            raise HTTPException(503, "AI features are off. Set ANTHROPIC_API_KEY on the server to turn them on.")
        saved = store.get_settings()
        res = ai.enrich(store, now(), profile(), bool(saved.get("ai_hide_noncivil", False)), 45)
        if res["error"] and not res["scored"]:
            raise HTTPException(502, res["error"])
        return {**res, **store.ai_counts(now())}

    @app.post("/api/tenders/star", dependencies=auth)
    def star(body: StarBody) -> dict:
        if not store.set_starred(body.key, body.starred, now()):
            raise HTTPException(404, "No such tender")
        return {"key": body.key, "starred": body.starred}

    @app.get("/api/views", dependencies=auth)
    def get_views() -> dict:
        return {"views": store.get_settings().get("views", [])}

    @app.put("/api/views", dependencies=auth)
    def put_views(body: ViewsBody) -> dict:
        views = []
        for v in body.views[:MAX_VIEWS]:
            name = v.name.strip()
            if not name or len(name) > 40 or len(v.id) > 40:
                raise HTTPException(422, "A view needs a name of up to 40 characters")
            if any(k not in VIEW_KEYS for k in v.filters):
                raise HTTPException(422, "A view contains a setting that is not known")
            views.append({"id": v.id, "name": name, "filters": v.filters})
        if len(json.dumps(views)) > 30_000:
            raise HTTPException(422, "Saved views are too large")
        store.put_settings({"views": views})
        return {"views": views}

    # ---- bid workspace: pipeline, checklist, eligibility, drafts, changes, results ---------------------------------

    def company_facts() -> list[dict]:
        raw = store.get_settings().get("company_facts", [])
        return [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []

    def bid_or_404(bid_id: int) -> dict:
        bid = bids.get(bid_id)
        if not bid:
            raise HTTPException(404, "No such bid")
        return bid

    def bid_as_tender(bid: dict) -> dict:
        return {k: bid.get(k) for k in ("title", "description", "organisation", "category", "location", "value", "emd", "published", "closing", "opening", "tender_id", "portal")} | {
            "portal_name": portal_names.get(bid.get("portal") or "", bid.get("portal"))}

    def bid_error(e: BidError) -> HTTPException:
        return HTTPException(422, str(e))

    def bid_full(bid: dict) -> dict:
        items = bids.with_matches(bid, now())
        changes = bids.changes(bid["id"])
        return {
            **bid, "portal_name": portal_names.get(bid["portal"] or "", bid["portal"]), "search_url": portal_search.get(bid["portal"] or "", ""),
            "value_text": format_inr(bid["value"]), "items": items, "changes": changes, "unseen": sum(1 for c in changes if not c["seen"]),
            "history": bids.history_notes(bid.get("work_type"), bid.get("organisation"), bid.get("value")),
        }

    @app.get("/api/bids", dependencies=auth)
    def list_bids(stage: str | None = None) -> dict:
        if stage and stage not in STAGES:
            raise HTTPException(422, "Unknown stage")
        rows = bids.list(stage)
        for r in rows:
            r["portal_name"] = portal_names.get(r["portal"] or "", r["portal"])
            r["value_text"] = format_inr(r["value"])
        counts = {s: 0 for s in STAGES}
        for r in bids.list():
            counts[r["stage"]] += 1
        return {"bids": rows, "counts": counts, "stages": [{"key": k, "label": STAGE_LABELS[k]} for k in STAGES], "unseen": bids.unseen_total()}

    @app.post("/api/bids", dependencies=auth)
    def create_bid(body: BidCreate) -> dict:
        try:
            bid, created = bids.create_from_tender(body.key, now())
        except BidError as e:
            raise HTTPException(404, str(e)) from None
        if created:
            bids.generate_items(bid["id"], now())
        return {"id": bid["id"], "created": created}

    @app.get("/api/bids/alerts", dependencies=auth)
    def bid_alerts(days: int = Query(7, ge=1, le=60)) -> dict:
        return {"alerts": bids.alerts(now(), days), "unseen": bids.unseen_total()}

    @app.get("/api/bids/stats", dependencies=auth)
    def bid_stats() -> dict:
        return bids.stats()

    @app.get("/api/bids/{bid_id}", dependencies=auth)
    def get_bid(bid_id: int) -> dict:
        return bid_full(bid_or_404(bid_id))

    @app.patch("/api/bids/{bid_id}", dependencies=auth)
    def patch_bid(bid_id: int, body: dict[str, Any]) -> dict:
        bid_or_404(bid_id)
        try:
            bids.update(bid_id, clean_patch(body), now())
        except BidError as e:
            raise bid_error(e) from None
        return bid_full(bid_or_404(bid_id))

    @app.delete("/api/bids/{bid_id}", dependencies=auth)
    def delete_bid(bid_id: int) -> dict:
        if not bids.delete(bid_id):
            raise HTTPException(404, "No such bid")
        return {"removed": bid_id}

    @app.post("/api/bids/{bid_id}/items", dependencies=auth)
    def add_bid_item(bid_id: int, body: ItemBody) -> dict:
        bid_or_404(bid_id)
        try:
            return bids.add_item(bid_id, body.text, body.kind, now())
        except BidError as e:
            raise bid_error(e) from None

    @app.post("/api/bids/{bid_id}/items/generate", dependencies=auth)
    def generate_bid_items(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return {"added": bids.generate_items(bid_id, now())}

    @app.patch("/api/bids/{bid_id}/items/{item_id}", dependencies=auth)
    def patch_bid_item(bid_id: int, item_id: int, body: dict[str, Any]) -> dict:
        bid_or_404(bid_id)
        try:
            return bids.update_item(bid_id, item_id, body)
        except BidError as e:
            raise bid_error(e) from None

    @app.delete("/api/bids/{bid_id}/items/{item_id}", dependencies=auth)
    def delete_bid_item(bid_id: int, item_id: int) -> dict:
        if not bids.delete_item(bid_id, item_id):
            raise HTTPException(404, "No such item")
        return {"removed": item_id}

    @app.post("/api/bids/{bid_id}/changes/seen", dependencies=auth)
    def bid_changes_seen(bid_id: int) -> dict:
        bid_or_404(bid_id)
        bids.mark_seen(bid_id)
        return {"ok": True}

    @app.post("/api/bids/{bid_id}/document", dependencies=auth)
    def bid_document(bid_id: int, body: DocBody) -> dict:
        bid = bid_or_404(bid_id)
        doc = ai_call(ai.read_document, bid_as_tender(bid), body.text, body.pdf_base64)
        bids.set_json(bid_id, "doc", doc, now())
        if store.get_tender(bid["key"]):
            store.set_ai_text(bid["key"], "doc", json.dumps(doc))
        return {"doc": doc, "added": bids.generate_items(bid_id, now())}

    @app.post("/api/bids/{bid_id}/brief", dependencies=auth)
    def bid_brief(bid_id: int, body: SummaryBody) -> dict:
        bid = bid_or_404(bid_id)
        if bid.get("brief") and not body.refresh:
            return {"brief": bid["brief"], "cached": True}
        hist = bids.history_notes(bid.get("work_type"), bid.get("organisation"), bid.get("value"))
        out = ai_call(ai.brief, bid_as_tender(bid), profile(), bid.get("doc"), *([hist] if hist else []))
        bids.set_json(bid_id, "brief", out, now())
        return {"brief": out, "cached": False}

    @app.post("/api/bids/{bid_id}/eligibility", dependencies=auth)
    def bid_eligibility(bid_id: int) -> dict:
        bid = bid_or_404(bid_id)
        if not ai.configured():
            raise HTTPException(503, "AI features are off. Set ANTHROPIC_API_KEY on the server to turn them on.")
        doc = bid.get("doc") or {}
        if not (doc.get("requirements") or doc.get("financial")):
            raise HTTPException(422, "The tender document has no eligibility conditions yet. Read the tender document first.")
        vault = [{**d, "kind": VAULT_KINDS.get(d["kind"], ("Other",))[0]} for d in bids.vault_list()]
        out = ai_call(ai.eligibility, bid_as_tender(bid), profile(), company_facts(), vault, bid.get("doc") or {})
        out["checked"] = now().isoformat(timespec="seconds")
        bids.set_json(bid_id, "eligibility", out, now())
        return out

    @app.post("/api/bids/{bid_id}/draft", dependencies=auth)
    def bid_draft(bid_id: int, body: DraftBody) -> dict:
        bid = bid_or_404(bid_id)
        if body.kind not in ai.DRAFT_KINDS:
            raise HTTPException(422, "Unknown kind of draft")
        if body.kind == "custom" and not body.instructions.strip():
            raise HTTPException(422, "Say what to write")
        drafts = bid.get("drafts") or {}
        if drafts.get(body.kind) and not body.refresh and body.kind != "custom":
            return {"kind": body.kind, "text": drafts[body.kind], "cached": True}
        text = ai_call(ai.draft, body.kind, bid_as_tender(bid), profile(), company_facts(), bid.get("doc"), body.instructions)
        drafts[body.kind] = text
        bids.set_json(bid_id, "drafts", drafts, now())
        return {"kind": body.kind, "text": text, "cached": False}

    # ---- vault: reusable company papers and the facts about your company ---------------------------------------------

    @app.get("/api/vault", dependencies=auth)
    def vault_list() -> dict:
        today = now()
        docs = [{**d, "status": bids.doc_status(d, today)} for d in bids.vault_list()]
        return {"documents": docs, "kinds": [{"key": k, "label": v[0]} for k, v in VAULT_KINDS.items()], "facts": company_facts()}

    @app.post("/api/vault", dependencies=auth)
    def vault_add(body: VaultBody) -> dict:
        data = None
        if body.file_base64:
            try:
                data = base64.b64decode(body.file_base64, validate=True)
            except ValueError:
                raise HTTPException(422, "That file could not be read") from None
        try:
            return bids.vault_add(body.model_dump(), data, body.filename, now())
        except BidError as e:
            raise bid_error(e) from None

    @app.patch("/api/vault/{doc_id}", dependencies=auth)
    def vault_update(doc_id: int, body: dict[str, Any]) -> dict:
        try:
            return bids.vault_update(doc_id, body)
        except BidError as e:
            raise HTTPException(404 if "No such" in str(e) else 422, str(e)) from None

    @app.delete("/api/vault/{doc_id}", dependencies=auth)
    def vault_delete(doc_id: int) -> dict:
        if not bids.vault_delete(doc_id):
            raise HTTPException(404, "No such document")
        return {"removed": doc_id}

    @app.get("/api/vault/{doc_id}/file", dependencies=auth)
    def vault_file(doc_id: int):
        found = bids.vault_file(doc_id)
        if not found:
            raise HTTPException(404, "No file is stored for that document")
        path, name = found
        return FileResponse(path, media_type="application/octet-stream", filename=name)

    @app.put("/api/company-facts", dependencies=auth)
    def put_company_facts(body: dict[str, Any]) -> dict:
        raw = body.get("facts")
        if not isinstance(raw, list) or len(raw) > 40:
            raise HTTPException(422, "Facts must be a list of up to 40 items")
        facts = []
        for f in raw:
            label, value = str((f or {}).get("label", "")).strip()[:60], str((f or {}).get("value", "")).strip()[:160]
            if label and value:
                facts.append({"label": label, "value": value})
        store.put_settings({"company_facts": facts})
        return {"facts": facts}

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
                "available_portals": [{"key": p.key, "name": p.name} for p in portals_now()]}

    @app.put("/api/settings", dependencies=auth)
    def put_settings(body: dict[str, Any]) -> dict:
        try:
            store.put_settings(appsettings.clean(body, list(portal_names)))
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        return get_settings()

    # ---- portals: add your own, and see which ones the app cannot read ---------------------------------------

    @app.get("/api/portals", dependencies=auth)
    def list_portals() -> dict:
        saved, statuses = store.get_settings(), store.portal_statuses()
        entries = [portal_entry(p, saved, statuses) for p in portals_now()]
        return {"portals": entries, "manual": [e for e in entries if e["manual"]]}

    @app.post("/api/portals", dependencies=auth)
    def add_portal(body: dict[str, Any]) -> dict:
        saved = store.get_settings()
        existing = appsettings.all_portals(env, saved)
        try:
            new = appsettings.clean_portal(body, {p.key for p in existing})
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        same = next((p for p in existing if p.base_url.rstrip("/").lower() == new["base_url"].lower()), None)
        if same:
            raise HTTPException(422, f"That address is already in the list as '{same.name}'")
        result = probe(new["base_url"])
        # Added even when the app cannot read it: it then appears on the "check manually" list with the reason.
        store.put_settings({"custom_portals": [*saved.get("custom_portals", []), new]})
        store.put_portal_status(new["key"], result["kind"], result["detail"], now(), result["departments"], result["tenders"])
        if result["kind"] == "ok":
            store.put_settings({"portals": [*appsettings.current(env, saved)["portals"], new["key"]]})
        refresh_portals()
        return portal_entry(Portal(**new), store.get_settings(), store.portal_statuses())

    @app.post("/api/portals/{key}/check", dependencies=auth)
    def check_portal(key: str) -> dict:
        portal = next((p for p in portals_now() if p.key == key), None)
        if not portal:
            raise HTTPException(404, "No such portal")
        result = probe(portal.base_url)
        store.put_portal_status(key, result["kind"], result["detail"], now(), result["departments"], result["tenders"])
        return portal_entry(portal, store.get_settings(), store.portal_statuses())

    @app.delete("/api/portals/{key}", dependencies=auth)
    def remove_portal(key: str) -> dict:
        if key in builtin_keys:
            raise HTTPException(422, "Built-in portals cannot be removed. Switch them off in Settings instead")
        saved = store.get_settings()
        customs = saved.get("custom_portals", [])
        if key not in {p.get("key") for p in customs}:
            raise HTTPException(404, "No such portal")
        update: dict[str, Any] = {"custom_portals": [p for p in customs if p.get("key") != key]}
        if "portals" in saved:
            update["portals"] = [k for k in saved["portals"] if k != key]
        store.put_settings(update)
        store.delete_portal_status(key)
        refresh_portals()
        return {"removed": key}

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
