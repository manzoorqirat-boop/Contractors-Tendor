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
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from pydantic import BaseModel

from . import access, ai, appsettings, mailer
from .boq import advisor
from .boq.match import ClaudeVerifier
from .boq.portal import fetch_boq, fetch_risks
from .boq.service import BoqError, BoqService
from .bids import STAGE_LABELS, STAGES, VAULT_KINDS, BidBook, BidError, clean_patch, default_vault_dir, locate_on_portal, portal_facts
from .gepnic import PortalError, Tender, parse_date, pick_nit
from .config import Portal, Settings, load_settings
from .digest import CSV_COLUMNS, format_inr, safe_cell
from .runner import Busy, NotConfigured, RunManager, Scheduler, local_now
from .store import DIMENSIONS, SORTS, SeenStore

log = logging.getLogger("tender_digest.web")
COOKIE = "td_session"
SESSION_SECONDS = 7 * 24 * 3600
MAX_EXPORT_ROWS = 20_000
# Request size limits. Uploads travel as base64 inside JSON (a 10 MB file is about 13.4 MB), so those routes get more room; everything else is small.
MAX_UPLOAD_BODY = 16 * 1024 * 1024
MAX_JSON_BODY = 1024 * 1024
UPLOAD_PATH_RE = re.compile(r"^/api/(vault|boq/catalogue|tenders/(document|manual(/read)?)|bids/\d+/(document|boq|risks))$")
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
    idle_seconds: int = 24 * 3600  # a session unused for this long ends; it also ends after SESSION_SECONDS whatever happens
    trusted_proxy_hops: int = 1  # how many reverse proxies sit in front (Railway: 1). 0 = ignore X-Forwarded-For


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def config_from_env() -> AppConfig:
    password = os.environ.get("APP_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("APP_PASSWORD must be set to a password of at least 8 characters. It protects the dashboard and the Run now button.")
    if len(password) < 12:
        log.warning("APP_PASSWORD is shorter than 12 characters. Use a longer one: it is the only thing between the internet and your bids.")
    key = os.environ.get("SECRET_KEY", "")
    if not key:
        key = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set: a temporary one is used, so everyone is logged out whenever the service restarts. Set SECRET_KEY.")
    return AppConfig(
        password=password, secret_key=key, username=os.environ.get("APP_USERNAME", "admin").strip(), cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() not in ("0", "false", "no"),
        tz_name=os.environ.get("TZ_NAME", "Asia/Kolkata"),
        idle_seconds=max(1, _int_env("SESSION_IDLE_HOURS", 24)) * 3600, trusted_proxy_hops=max(0, _int_env("TRUSTED_PROXY_HOPS", 1)),
        static_dir=Path(os.environ.get("STATIC_DIR") or Path(__file__).resolve().parent.parent / "web" / "dist"),
    )


# ---- session cookie: expiry plus an HMAC, nothing else to look up --------------------------------------------

def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(secret: str, now: float | None = None, sid: str | None = None) -> str:
    claims: dict[str, Any] = {"exp": int((now or time.time()) + SESSION_SECONDS)}
    if sid:
        claims["sid"] = sid
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"{payload}.{_sign(secret, payload)}"


def token_claims(secret: str, token: str | None, now: float | None = None) -> dict[str, Any] | None:
    """The token's contents when its signature is right and it has not expired, else None."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(secret, payload)):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return claims if claims["exp"] > (now or time.time()) else None
    except (ValueError, KeyError, TypeError):
        return None


def valid_token(secret: str, token: str | None, now: float | None = None) -> bool:
    return token_claims(secret, token, now) is not None


class LoginLimiter:
    """Slows password guessing. One address that fails too often is blocked for a while.

    Failures across all addresses only add a delay (`slow_seconds`) and never block: a block that anyone on the internet can
    trigger would also lock the owner out.
    """

    def __init__(self, per_client: int = 8, overall: int = 40, window: int = 600, slow_seconds: float = 1.5):
        self.per_client, self.overall, self.window, self.slow_seconds = per_client, overall, window, slow_seconds
        self.fails: list[tuple[float, str]] = []

    def _recent(self, now: float) -> list[tuple[float, str]]:
        self.fails = [(t, c) for t, c in self.fails if now - t < self.window]
        return self.fails

    def blocked(self, client: str, now: float | None = None) -> bool:
        return sum(1 for _, c in self._recent(now or time.time()) if c == client) >= self.per_client

    def under_attack(self, now: float | None = None) -> bool:
        return len(self._recent(now or time.time())) >= self.overall

    def failed(self, client: str, now: float | None = None) -> None:
        self.fails.append((now or time.time(), client))

    def succeeded(self, client: str) -> None:
        self.fails = [(t, c) for t, c in self.fails if c != client]


class BodyLimit:
    """Refuses oversized request bodies before they are read into memory (by Content-Length, and by counting when there is none)."""

    class TooLarge(BaseException):  # not an Exception: FastAPI turns those, raised while it reads a body, into a vague 400
        pass

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD_BODY if UPLOAD_PATH_RE.match(scope["path"]) else MAX_JSON_BODY
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                return await self._refuse(send, 400, "Bad Content-Length")
            if too_big:
                return await self._refuse(send, 413, f"That request is too large (the limit here is {limit // (1024 * 1024) or 1} MB)")
        seen, started = 0, False

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise BodyLimit.TooLarge
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except BaseException as e:  # noqa: BLE001 - also reached through an ExceptionGroup from the framework's task group
            if not _holds(e, BodyLimit.TooLarge):
                raise
            if not started:
                await self._refuse(send, 413, "That request is too large")

    @staticmethod
    async def _refuse(send: Send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})


def _holds(exc: BaseException, kind: type[BaseException]) -> bool:
    if isinstance(exc, kind):
        return True
    return any(_holds(e, kind) for e in getattr(exc, "exceptions", ()))


def storage_warning(state_path: str, environ: dict[str, str] | None = None) -> str:
    """On Railway, says so when the database is not on a mounted volume: it would be wiped by every redeploy. Empty when all is well."""
    environ = os.environ if environ is None else environ
    if state_path == ":memory:" or environ.get("ALLOW_EPHEMERAL_STORAGE", "").lower() in ("1", "true", "yes"):
        return ""
    if not any(k.startswith("RAILWAY_") for k in environ):
        return ""
    mount = environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not mount:
        return "No volume is attached to this service, so tenders, bids and vault files are lost at every redeploy. Add a Railway Volume mounted at /data."
    try:
        Path(state_path).expanduser().resolve().relative_to(Path(mount).resolve())
    except ValueError:
        return f"The database is not stored on the volume mounted at {mount}, so it is lost at every redeploy. Set STATE_PATH to a file under {mount}."
    return ""


def storage_ok(store: SeenStore, state_path: str) -> bool:
    """Can the database be read, and is its folder writable?"""
    try:
        store.ping()
    except Exception:  # noqa: BLE001
        return False
    return state_path == ":memory:" or os.access(Path(state_path).expanduser().resolve().parent, os.W_OK)


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


class ListingBody(BaseModel):
    text: str = ""
    pdf_base64: str = ""


class ManualBody(BaseModel):
    """A tender the app could not collect, typed or read in from its notice."""
    tender_id: str = ""
    title: str = ""
    description: str = ""
    reference: str = ""
    organisation: str = ""
    location: str = ""
    value: float | None = None
    emd: float | None = None
    published: str = ""
    closing: str = ""
    opening: str = ""
    portal_name: str = ""
    text: str = ""
    pdf_base64: str = ""
    read_document: bool = True


class ManualEdit(BaseModel):
    tender_id: str | None = None
    title: str | None = None
    description: str | None = None
    reference: str | None = None
    organisation: str | None = None
    location: str | None = None
    value: float | None = None
    emd: float | None = None
    published: str | None = None
    closing: str | None = None
    opening: str | None = None
    clear_value: bool = False
    clear_emd: bool = False


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


class BoqUpload(BaseModel):
    filename: str = ""
    file_base64: str = ""


class BoqConfirm(BaseModel):
    item_code: str | None = None


class CatalogueBody(BaseModel):
    kind: str = "items"
    csv_text: str = ""


class RiskBody(BaseModel):
    text: str = ""
    pdf_base64: str = ""


class SimBody(BaseModel):
    estimate: float | None = None
    exposure: dict[str, float] = {}
    l1_price: float | None = None
    quoted_pct: float | None = None
    include_review: bool = False


class RunBody(BaseModel):
    portals: list[str] | None = None
    send_email: bool = False


def create_app(config: AppConfig, env: Settings, store: SeenStore, manager: RunManager | None = None, prober: Callable[[str], dict] | None = None) -> FastAPI:
    vault_dir = default_vault_dir(env.state_path)
    bids = BidBook(store, vault_dir)
    boq = BoqService.open(env.state_path, lambda: ClaudeVerifier() if ai.configured() else None)
    manager = manager or RunManager(env, store, tz_name=config.tz_name, bids=bids)
    scheduler = Scheduler(manager) if config.run_scheduler else None
    limiter = LoginLimiter()
    # Cookies are signed with the secret AND the password, so changing APP_PASSWORD signs everyone out.
    signing_key = hashlib.sha256(f"{config.secret_key}\0{config.password}".encode()).hexdigest()
    storage_note = storage_warning(env.state_path)
    if storage_note:
        log.warning(storage_note)
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
        portal_names["manual"], portal_states["manual"] = "Added by hand", "Added by hand"

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

    app.add_middleware(BodyLimit)  # added last, so it is the outermost layer and refuses a big body before anything reads it

    def client_of(request: Request) -> str:
        """The caller's address. Each proxy in front appends the address it saw, so the entry to trust is counted from the END of
        X-Forwarded-For; anything a caller puts at the start of the header is ignored."""
        hops = config.trusted_proxy_hops
        chain = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if hops and len(chain) >= hops:
            return chain[-hops]
        return (request.client.host if request.client else "") or "?"

    def session_ok(request: Request) -> bool:
        claims = token_claims(signing_key, request.cookies.get(COOKIE))
        sid = claims.get("sid") if claims else None
        return bool(isinstance(sid, str) and store.session_touch(sid, time.time(), config.idle_seconds))

    def require_login(request: Request) -> None:
        if not session_ok(request):
            raise HTTPException(401, "Please log in")

    auth = [Depends(require_login)]

    def now() -> datetime:
        return local_now(config.tz_name)

    # ---- auth ------------------------------------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        # Public, so it says only yes or no. Railway restarts the service when this fails.
        if not storage_ok(store, env.state_path):
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict:
        client = client_of(request)
        if limiter.blocked(client):
            raise HTTPException(429, "Too many wrong attempts. Wait a few minutes and try again.")
        if limiter.under_attack():
            time.sleep(limiter.slow_seconds)  # many wrong guesses from many addresses: slow everyone down, lock nobody out
        user_ok = not config.username or hmac.compare_digest(body.username.strip().lower().encode(), config.username.lower().encode())
        pass_ok = hmac.compare_digest(body.password.encode(), config.password.encode())
        if not (user_ok and pass_ok):
            limiter.failed(client)
            raise HTTPException(401, "Wrong username or password")
        limiter.succeeded(client)
        sid = secrets.token_urlsafe(24)
        store.session_start(sid, time.time(), SESSION_SECONDS)
        response.set_cookie(COOKIE, make_token(signing_key, sid=sid), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        claims = token_claims(signing_key, request.cookies.get(COOKIE))
        if claims and isinstance(claims.get("sid"), str):
            store.session_end(claims["sid"])  # the cookie stops working for good, even if someone copied it
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/branding")
    def branding() -> dict:
        # Public on purpose: the login screen shows the logo before anyone has signed in. Nothing secret is in it.
        return {"logo_text": appsettings.current(env, store.get_settings())["logo_text"]}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        return {"authenticated": session_ok(request)}

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
            "soon_days": env.closing_soon_days, "ai_enabled": ai.configured(), "storage_warning": storage_note,
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
        row["manual"] = row["portal"] == "manual"
        row["has_source"] = bids.has_source(key)
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

    def keep_source(key: str, text: str, pdf_base64: str) -> None:
        """Keeps the notice just read, so risk and BOQ steps can reuse it. Never fails the request it rides on."""
        try:
            if pdf_base64:
                bids.save_source(key, base64.b64decode(pdf_base64, validate=True))
            elif text.strip():
                bids.save_source(key, text[: ai.MAX_TEXT])
        except (ValueError, OSError):
            log.warning("could not keep the notice for %s", key)

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
        keep_source(body.key, body.text, body.pdf_base64)
        return {"doc": doc}

    # ---- a tender the app could not collect: added by hand from its notice ----------------------------------------

    def when_or_422(v: str | None, label: str) -> datetime | None:
        v = (v or "").strip()
        if not v:
            return None
        try:
            d = datetime.fromisoformat(v)
        except ValueError:
            raise HTTPException(422, f"{label} should look like 2026-12-02 15:00") from None
        if not 2000 <= d.year <= 2100:
            raise HTTPException(422, f"{label} is not a believable date")
        if d.tzinfo is not None:  # "...Z" or "+00:00": convert to local time, do not just drop the offset
            d = d.astimezone(ZoneInfo(config.tz_name))
        return d.replace(tzinfo=None)

    def check_listing(published: datetime | None, closing: datetime | None, opening: datetime | None, value: float | None, emd: float | None) -> None:
        """Rules between the fields of a hand-added tender. Each message says what to change."""
        if published and closing and published > closing:
            raise HTTPException(422, "The publish date is after the closing date. Check both dates")
        if opening and closing and opening < closing:
            raise HTTPException(422, "Bids cannot open before the tender closes. Check the opening and closing dates")
        if value is not None and emd is not None and emd > value:
            raise HTTPException(422, "The EMD is larger than the tender value. Check both amounts")

    def check_tender_id(tid: str) -> None:
        if re.search(r"[<>\x00-\x1f\x7f]", tid):
            raise HTTPException(422, "The Tender ID cannot contain < or > or control characters. Copy it exactly as the portal shows it")

    def clean_money(v: float | None, label: str) -> float | None:
        if v is None:
            return None
        if not 0 < v < 1e13:
            raise HTTPException(422, f"{label} must be a positive amount in rupees")
        return float(v)

    @app.post("/api/tenders/manual/read", dependencies=auth)
    def manual_read(body: ListingBody) -> dict:
        """Reads the listing details out of a notice, for the person to check and correct. Saves nothing."""
        return {"listing": ai_call(ai.read_listing, body.text, body.pdf_base64)}

    @app.get("/api/tenders/manual/check", dependencies=auth)
    def manual_check(tender_id: str = Query(..., max_length=80)) -> dict:
        """Is this Tender ID already held, from a portal or added earlier?"""
        found = store.find_by_tender_id(tender_id.strip())
        return {"existing": [{"key": f["key"], "portal_name": portal_names.get(f["portal"], f["portal"]), "organisation": f["organisation"], "manual": f["portal"] == "manual"} for f in found]}

    @app.post("/api/tenders/manual", dependencies=auth)
    def manual_create(body: ManualBody) -> dict:
        tid = body.tender_id.strip()[:80]
        if not tid:
            raise HTTPException(422, "The Tender ID is needed: it is how you find the tender on its portal")
        if not (body.title.strip() or body.description.strip()):
            raise HTTPException(422, "Give the work a name or description")
        closing = when_or_422(body.closing, "The closing date")
        if closing is None:
            raise HTTPException(422, "The closing date is needed, so deadlines and reminders work")
        check_tender_id(tid)
        if closing <= now():
            raise HTTPException(422, "The closing date has already passed. Bids can no longer be filed, so check the date")
        if store.find_by_tender_id(tid):
            raise HTTPException(409, "A tender with this ID is already in the app. Open it from the Tenders page instead of adding it again")
        desc = body.description.strip()[:500]
        published, opening = when_or_422(body.published, "The publish date"), when_or_422(body.opening, "The opening date")
        value, emd = clean_money(body.value, "The value"), clean_money(body.emd, "The EMD")
        check_listing(published, closing, opening, value, emd)
        t = Tender(portal="manual", tender_id=tid, title=(body.title.strip() or desc)[:160], reference=body.reference.strip()[:120], organisation=body.organisation.strip()[:300],
                   published=published, closing=closing, opening=opening,
                   description=desc or body.title.strip()[:500], category="", value=value, location=body.location.strip()[:120],
                   emd=emd, extra={"portal_name": body.portal_name.strip()[:80]})
        store.keep(t, now())
        store.mark_emailed([t.key])  # you added it yourself: it does not belong in "new since the last digest"
        keep_source(t.key, body.text, body.pdf_base64)
        out: dict[str, Any] = {"key": t.key, "doc_read": False, "doc_error": "", "source_kept": bids.has_source(t.key)}
        if body.read_document and (body.text.strip() or body.pdf_base64):
            if not ai.configured():
                out["doc_error"] = "The AI assistant is off, so the notice was saved but not read."
            else:
                try:
                    doc = ai.read_document(tender_for_ai(t.key), body.text, body.pdf_base64)
                    store.set_ai_text(t.key, "doc", json.dumps(doc))
                    out["doc_read"] = True
                except ai.AIError as e:
                    out["doc_error"] = str(e)
        return out

    def manual_or_404(key: str) -> Tender:
        t = store.tender_object(key)
        if not t or t.portal != "manual":
            raise HTTPException(404, "That tender was not added by hand")
        return t

    @app.patch("/api/tenders/manual", dependencies=auth)
    def manual_edit(body: ManualEdit, key: str = Query(..., max_length=300)) -> dict:
        t = manual_or_404(key)
        old_closing = t.closing
        changed: dict[str, Any] = {}
        for f in ("title", "description", "reference", "organisation", "location"):
            v = getattr(body, f)
            if v is not None:
                setattr(t, f, v.strip()[:500 if f == "description" else 300])
                changed[f] = getattr(t, f)
        for f, label in (("published", "The publish date"), ("closing", "The closing date"), ("opening", "The opening date")):
            v = getattr(body, f)
            if v is not None:
                d = when_or_422(v, label)
                if f == "closing" and d is None:
                    raise HTTPException(422, "The closing date cannot be empty")
                setattr(t, f, d)
                changed[f] = d.isoformat(timespec="seconds") if d else None
        for f, label, clear in (("value", "The value", body.clear_value), ("emd", "The EMD", body.clear_emd)):
            if clear:
                setattr(t, f, None)
                changed[f] = None
            elif getattr(body, f) is not None:
                setattr(t, f, clean_money(getattr(body, f), label))
                changed[f] = getattr(t, f)
        if not (t.title or t.description):
            raise HTTPException(422, "Give the work a name or description")
        if t.closing != old_closing and t.closing and t.closing <= now():
            raise HTTPException(422, "The closing date has already passed. Bids can no longer be filed, so check the date")
        check_listing(t.published, t.closing, t.opening, t.value, t.emd)
        store.keep(t, now())
        bids.sync_listing(key, changed)
        return {"key": key}

    @app.delete("/api/tenders/manual", dependencies=auth)
    def manual_delete(key: str = Query(..., max_length=300)) -> dict:
        manual_or_404(key)
        if bids.id_for_key(key):
            raise HTTPException(409, "This tender has a bid. Remove the bid first")
        store.delete_tender(key)
        bids.delete_source(key)
        return {"removed": key}

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
            "has_source": bids.has_source(bid["key"]), "manual": bid["portal"] == "manual",
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
        keep_source(bid["key"], body.text, body.pdf_base64)
        bids.set_json(bid_id, "doc", doc, now())
        if store.get_tender(bid["key"]):
            store.set_ai_text(bid["key"], "doc", json.dumps(doc))
        return {"doc": doc, "added": bids.generate_items(bid_id, now())}

    fetch_lock = threading.Lock()

    @app.post("/api/bids/{bid_id}/fetch", dependencies=auth)
    def bid_fetch(bid_id: int) -> dict:
        """Looks the tender up again on its portal: the public fee/class/date fields, and the tender notice (NIT) when the portal gives it without a login."""
        bid = bid_or_404(bid_id)
        if bid["portal"] == "manual":
            raise HTTPException(422, "This tender was added by hand, so there is nothing to fetch from a portal. Upload its notice instead.")
        portal = next((p for p in portals_now() if p.key == bid["portal"]), None)
        if not portal:
            raise HTTPException(404, "That portal is no longer in your list")
        if not fetch_lock.acquire(blocking=False):
            raise HTTPException(409, "Another fetch is in progress. Try again in a moment")
        try:
            client = manager.client_factory(portal, env)
            try:
                t = locate_on_portal(client, portal.key, bid["organisation"] or "", bid["tender_id"])
                if t is None:
                    raise HTTPException(404, "The tender is no longer listed on the portal. It may have closed or been withdrawn")
                full = getattr(client, "detail_full", None)
                pairs, corrigenda, links = full(t) if full else (client.detail(t), [], [])
            except PortalError as e:
                raise HTTPException(502, f"The portal could not be read: {e}") from None
            facts = portal_facts(pairs)
            bids.set_json(bid_id, "portal_facts", facts, now())
            patch = {}
            for label, field in (("Clarification End Date", "query_deadline"), ("Pre Bid Meeting Date", "prebid_date")):
                d = parse_date(pairs.get(label, ""))
                if d and not bid.get(field):
                    patch[field] = d.isoformat(timespec="seconds")
            if patch:
                bids.update(bid_id, patch, now())
            state, message = "no_link", "The portal lists no downloadable notice for this tender."
            nit_pdf: bytes | None = None
            nit = pick_nit(links)
            if nit and hasattr(client, "download"):
                try:
                    data, _ctype = client.download(nit["href"])
                except PortalError as e:
                    state, message = "failed", f"The portal would not give the notice: {e}"
                else:
                    nit_pdf = data if data.startswith(b"%PDF") else None
                    if nit_pdf:
                        try:
                            bids.save_source(bid["key"], nit_pdf)   # so the risk step can read it again without an upload
                        except OSError:
                            log.warning("could not keep the notice for %s", bid["key"])
                    if not data.startswith(b"%PDF"):
                        state, message = "login", "The portal asks for a login or captcha before it gives documents, which this app never gets past. Download the notice yourself and upload it here."
                    elif not ai.configured():
                        state, message = "ai_off", "The notice was available, but the AI assistant is off, so it was not read."
                    else:
                        try:
                            doc = ai.read_document(bid_as_tender(bid), "", base64.b64encode(data).decode())
                            bids.set_json(bid_id, "doc", doc, now())
                            if store.get_tender(bid["key"]):
                                store.set_ai_text(bid["key"], "doc", json.dumps(doc))
                            state, message = "read", "Read the tender notice from the portal."
                        except ai.AIError as e:
                            state, message = "ai_failed", str(e)
            added = bids.generate_items(bid_id, now())
            boq_out = boq_call(fetch_boq, client, boq, bid["key"], links, nit["href"] if nit else None)
            risks_out = boq_call(fetch_risks, boq, bid["key"], bid_as_tender(bid), nit_pdf)
            return {"facts": facts, "added": added, "nit": state, "message": message, "corrigenda": corrigenda, "boq": boq_out, "risks": risks_out}
        finally:
            fetch_lock.release()

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

    # ---- BOQ: catalogue, upload and match, costing, risk -------------------------------------------------------------

    def boq_call(fn, *args, **kwargs):
        try:
            return ai_call(lambda: fn(*args, **kwargs))
        except BoqError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/boq/catalogue", dependencies=auth)
    def boq_catalogue() -> dict:
        return boq.catalogue()

    @app.post("/api/boq/catalogue", dependencies=auth)
    def boq_catalogue_import(body: CatalogueBody) -> dict:
        return boq_call(boq.import_catalogue, body.kind, body.csv_text)

    @app.get("/api/bids/{bid_id}/boq", dependencies=auth)
    def bid_boq(bid_id: int, include_review: bool = False) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.view, bid["key"], include_review)

    @app.post("/api/bids/{bid_id}/boq", dependencies=auth)
    def bid_boq_upload(bid_id: int, body: BoqUpload) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.analyse, bid["key"], body.filename, body.file_base64)

    @app.post("/api/bids/{bid_id}/boq/lines/{line_no}", dependencies=auth)
    def bid_boq_confirm(bid_id: int, line_no: int, body: BoqConfirm) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.confirm, bid["key"], line_no, body.item_code, "you")

    @app.get("/api/bids/{bid_id}/risks", dependencies=auth)
    def bid_risks(bid_id: int) -> dict:
        bid = bid_or_404(bid_id)
        return boq.risks(bid["key"])

    @app.post("/api/bids/{bid_id}/risks", dependencies=auth)
    def bid_risks_read(bid_id: int, body: RiskBody) -> dict:
        bid = bid_or_404(bid_id)
        text, pdf = body.text, body.pdf_base64
        if not text.strip() and not pdf:
            text, pdf = bids.load_source(bid["key"])  # the notice kept when the tender was added or its document read
            if not text and not pdf:
                raise HTTPException(422, "No notice is saved for this tender yet. Upload it or paste its text.")
        out = boq_call(boq.read_risks, bid["key"], bid_as_tender(bid), text, pdf)
        if body.text.strip() or body.pdf_base64:
            keep_source(bid["key"], body.text, body.pdf_base64)
        return out

    @app.get("/api/bids/{bid_id}/advice", dependencies=auth)
    def bid_advice(bid_id: int, min_margin: float = 3.0, target_margin: float = 8.0, include_review: bool = False) -> dict:
        """Quote advice: your cost range (with risk allowances when they have been read) against how past L1 prices compared with their estimates."""
        bid = bid_or_404(bid_id)
        mode, notes = "with_risks", []
        try:
            sim = boq.simulate(bid["key"], None, None, None, None, include_review)
            low, high, est, coverage = sim["cost_low"], sim["cost_high"], sim.get("estimate"), sim["coverage_pct"]
        except BoqError as e:
            if "risks" not in str(e).lower():
                raise HTTPException(422, str(e)) from None
            pricing = boq.price(bid["key"], include_review, "")
            if pricing is None:
                raise HTTPException(422, "Upload the BOQ first: the advisor starts from your costed BOQ.") from None
            mode, low, high, est, coverage = "boq_only", pricing["projected_cost"], pricing["projected_cost"], pricing["estimate_total"], pricing["coverage_pct"]
            notes.append("Risk allowances are not included yet. Read the tender document for risks to add them to your cost.")
        if not est:
            raise HTTPException(422, "The BOQ has no estimate to compare your quote with.")
        if coverage < 50:
            notes.append(f"Only {coverage:.0f}% of the BOQ value is priced from your catalogue; the rest is carried at the buyer's estimate.")
        outcomes, basis = bids.l1_outcomes(bid.get("work_type"), bid.get("value"))
        try:
            out = advisor.advise(low, high, est, outcomes, min_margin, target_margin, basis)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        out.update({"mode": mode, "coverage_pct": coverage, "my_value": bid.get("my_value")})
        out["notes"] = notes + out["notes"]
        return out

    @app.post("/api/bids/{bid_id}/risks/simulate", dependencies=auth)
    def bid_risks_simulate(bid_id: int, body: SimBody) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.simulate, bid["key"], body.estimate, body.exposure, body.l1_price, body.quoted_pct, body.include_review)

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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=False, log_level="info")  # X-Forwarded-For is read (from the end) by client_of


if __name__ == "__main__":
    main()
