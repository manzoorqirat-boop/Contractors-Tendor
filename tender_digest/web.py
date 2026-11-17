"""The web service: login, dashboard data, tender browsing, manual runs and settings, plus the built React page."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import access, accounts, ai, appsettings, events, letters, offsite, sealing, tokens

# Names that moved to api_common.py, or are no longer used in this file, but that other code (and the tests) still reach as web.<name>.
# "x as x" is the explicit re-export form: it is kept by the import sorter and the unused-import check.
from . import mailer as mailer
from . import money as money_module
from .accounts import Accounts, Principal
from .api_common import COOKIE, log, token_claims
from .api_common import make_token as make_token
from .api_common import storage_ok as storage_ok
from .backup import Backups
from .bids import BidBook, BidError, default_vault_dir
from .boq.match import ClaudeVerifier
from .boq.service import BoqError, BoqService
from .config import Portal, Settings, load_settings
from .digest import format_inr
from .routes import register_all
from .routes.context import Ctx
from .runner import RunManager, Scheduler, local_now
from .store import SeenStore

CSP = "default-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
# Request size limits. Uploads travel as base64 inside JSON (a 10 MB file is about 13.4 MB), so those routes get more room; everything else is small.
MAX_UPLOAD_BODY = 16 * 1024 * 1024
MAX_JSON_BODY = 1024 * 1024
UPLOAD_PATH_RE = re.compile(r"^/api/(vault|boq/catalogue|tenders/(document|manual(/read)?)|bids/\d+/(document|boq|risks|award))$")




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
    ai_per_day: int = 300  # most AI requests a person can start in a day (0 = no limit); scheduled labelling has its own cap, AI_MAX_PER_RUN
    ai_per_minute: int = 20
    app_url: str = ""  # this site's public address (APP_URL), used for links in reminder emails and the calendar
    owner_totp: str = ""  # a base32 secret: when set, the owner login also needs the six-digit code from an authenticator app
    background_jobs: bool = False  # True in the real service: long actions run in the background and the page polls. Tests run them inline.
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
        idle_seconds=max(1, _int_env("SESSION_IDLE_HOURS", 24)) * 3600, background_jobs=True, trusted_proxy_hops=max(0, _int_env("TRUSTED_PROXY_HOPS", 1)),
        app_url=os.environ.get("APP_URL", "").strip().rstrip("/"),
        owner_totp=re.sub(r"[\s=-]", "", os.environ.get("OWNER_TOTP_SECRET", "")).upper(),
        ai_per_day=max(0, _int_env("AI_MAX_CALLS_PER_DAY", 300)), ai_per_minute=max(0, _int_env("AI_MAX_CALLS_PER_MINUTE", 20)),
        static_dir=Path(os.environ.get("STATIC_DIR") or Path(__file__).resolve().parent.parent / "web" / "dist"),
    )


# ---- session cookie: expiry plus an HMAC, nothing else to look up --------------------------------------------







def valid_token(secret: str, token: str | None, now: float | None = None) -> bool:
    return token_claims(secret, token, now) is not None


class LoginLimiter:
    """Slows password guessing. One address that fails too often is blocked for a while.

    Failures across all addresses only add a delay (`slow_seconds`) and never block: a block that anyone on the internet can
    trigger would also lock the owner out.
    """

    def __init__(self, per_client: int = 8, overall: int = 40, window: int = 600, slow_seconds: float = 1.5, store: SeenStore | None = None):
        self.per_client, self.overall, self.window, self.slow_seconds = per_client, overall, window, slow_seconds
        self.store = store  # when given, failures are kept in the database so a restart does not reset the count
        self.fails: list[tuple[float, str]] = store.login_fails_recent(time.time() - window) if store else []

    def _recent(self, now: float) -> list[tuple[float, str]]:
        self.fails = [(t, c) for t, c in self.fails if now - t < self.window]
        return self.fails

    def blocked(self, client: str, now: float | None = None) -> bool:
        return sum(1 for _, c in self._recent(now or time.time()) if c == client) >= self.per_client

    def under_attack(self, now: float | None = None) -> bool:
        return len(self._recent(now or time.time())) >= self.overall

    def failed(self, client: str, now: float | None = None) -> None:
        at = now or time.time()
        self.fails.append((at, client))
        if self.store:
            self.store.login_fail_add(client, at)

    def succeeded(self, client: str) -> None:
        self.fails = [(t, c) for t, c in self.fails if c != client]
        if self.store:
            self.store.login_fails_clear(client)


class AiBudget:
    """Caps the AI requests people start from the page, per day and per minute, so a stolen session or a stuck button cannot run up the bill."""

    def __init__(self, store: SeenStore, per_day: int, per_minute: int, today: Callable[[], str]):
        self.store, self.per_day, self.per_minute, self.today = store, per_day, per_minute, today
        self.recent: list[float] = []
        self.lock = threading.Lock()

    def spend(self, units: int = 1) -> None:
        """Counts `units` requests, or raises 429 saying which limit was reached. Nothing is counted when refused."""
        with self.lock:
            now = time.time()
            self.recent = [t for t in self.recent if now - t < 60]
            if self.per_minute and len(self.recent) + units > self.per_minute:
                raise HTTPException(429, f"That is a lot of AI requests in a minute (the limit is {self.per_minute}). Wait a moment and try again.")
            day = self.today()
            if self.per_day and self.store.ai_usage_today(day) + units > self.per_day:
                raise HTTPException(429, f"The daily limit of {self.per_day} AI requests is reached. It resets at midnight. To raise it, set AI_MAX_CALLS_PER_DAY on the server.")
            self.recent.extend([now] * units)
            self.store.ai_usage_add(day, units)

    def used_today(self) -> int:
        return self.store.ai_usage_today(self.today())


class Jobs:
    """Long actions (reading a tender notice, matching a BOQ, fetching from a portal) run in a thread and the page polls for the answer.

    Held in memory: a restart loses them, and the page then says the action was interrupted. Starting the same action again while it
    still runs hands back the running one, so a double click does not do the work twice.
    """

    MAX_RUNNING = 6
    KEEP_SECONDS = 3600

    def __init__(self):
        self.items: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, key: str | None, fn: Callable[[], Any]) -> str:
        with self.lock:
            now = time.time()
            self.items = {i: j for i, j in self.items.items() if j["state"] == "running" or now - j["finished"] < self.KEEP_SECONDS}
            if key is not None:
                for i, j in self.items.items():
                    if j["state"] == "running" and j["kind"] == kind and j["key"] == key:
                        return i
            if sum(1 for j in self.items.values() if j["state"] == "running") >= self.MAX_RUNNING:
                raise HTTPException(429, "Several long actions are already running. Wait for one to finish and try again")
            job_id = secrets.token_urlsafe(9)
            self.items[job_id] = {"kind": kind, "key": key, "state": "running", "started": now, "finished": 0.0, "result": None, "status": 0, "detail": ""}
        threading.Thread(target=self._work, args=(job_id, fn), daemon=True, name=f"job-{kind}").start()
        return job_id

    def _work(self, job_id: str, fn: Callable[[], Any]) -> None:
        outcome: dict[str, Any]
        try:
            outcome = {"state": "done", "result": fn()}
        except HTTPException as e:
            outcome = {"state": "failed", "status": e.status_code, "detail": str(e.detail)}
        except Exception:  # the person gets a plain message, the log gets the trace
            log.exception("background action failed")
            outcome = {"state": "failed", "status": 500, "detail": "Something went wrong on the server. Try again, and tell your administrator if it keeps happening."}
        with self.lock:
            self.items[job_id].update(outcome, finished=time.time())

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            j = self.items.get(job_id)
            if not j:
                return None
            out = {"state": j["state"], "kind": j["kind"], "elapsed": int((j["finished"] or time.time()) - j["started"])}
            if j["state"] == "done":
                out["result"] = j["result"]
            elif j["state"] == "failed":
                out.update(status=j["status"], detail=j["detail"])
            return out


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
        except BaseException as e:  # also reached through an ExceptionGroup from the framework's task group
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


def storage_warning(state_path: str, environ: Mapping[str, str] | None = None) -> str:
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




































































































def create_app(config: AppConfig, env: Settings, store: SeenStore, manager: RunManager | None = None, prober: Callable[[str], dict] | None = None, sso_session: Any = None) -> FastAPI:
    vault_dir = default_vault_dir(env.state_path)
    sealer = sealing.Sealer.from_env()  # raises with a clear message when the key is too short: better than quietly storing things unencrypted
    bids = BidBook(store, vault_dir, sealer)
    letter_book = letters.Letters(store)
    money = money_module.MoneyBook(bids)
    boq = BoqService.open(env.state_path, lambda: ClaudeVerifier() if ai.configured() else None)
    manager = manager or RunManager(env, store, tz_name=config.tz_name, bids=bids)
    remote_cfg, remote_problem = None, ""
    try:
        remote_cfg = offsite.config_from_env()
    except offsite.OffsiteError as e:
        remote_problem = str(e)
        log.warning("off-site backup is not active: %s", e)
    backups = Backups(store, boq.store, env.state_path, vault_dir, keep_days=max(1, _int_env("BACKUP_KEEP_DAYS", 7)), remote=remote_cfg, remote_problem=remote_problem)
    audit_pruned = {"day": ""}
    priorities_at: dict[str, datetime | None] = {"when": None}

    def refresh_priorities_hourly(at: datetime) -> None:
        """The urgency part of the recommended order changes as the hours pass, so it is worked out again every hour."""
        last = priorities_at["when"]
        if last is None or (at - last).total_seconds() >= 3600:
            priorities_at["when"] = at
            refresh_priorities()

    def prune_audit_daily(now: datetime) -> None:
        """Once a day, drops activity-log entries older than the retention period set in Settings."""
        day = now.date().isoformat()
        if audit_pruned["day"] == day:
            return
        audit_pruned["day"] = day
        people.prune_audit(now, appsettings.current(env, store.get_settings())["audit_days"])

    def housekeeping(now: datetime) -> None:
        """Run by the scheduler every half minute: the daily backup, then any deadline reminders that are due. One failing never stops the other."""
        for job in (lambda: backups.housekeeping(now), lambda: manager.send_reminders(now, config.app_url), lambda: hooks.deliver(now), lambda: prune_audit_daily(now), lambda: refresh_priorities_hourly(now)):
            try:
                job()
            except Exception:
                log.exception("housekeeping task failed")

    scheduler = Scheduler(manager, housekeeping=housekeeping) if config.run_scheduler else None
    limiter = LoginLimiter(store=store)
    jobs = Jobs()
    people = Accounts(store, sealer)
    api_tokens = tokens.ApiTokens(store)
    hooks = events.Events(store, sealer)
    encryption_problem = ""
    if sealer.enabled:  # bring everything stored before the key was set (or under an older key) up to date
        try:
            files = bids.seal_existing()
            secrets_done = people.seal_existing() + hooks.seal_existing()
            log.info("encryption at rest: %s files sealed, %s re-keyed, %s secrets written, %s failed", files["sealed"], files["rotated"], secrets_done, files["failed"])
            if files["failed"]:
                encryption_problem = f"{files['failed']} stored file(s) could not be encrypted. See the server log."
        except Exception:
            log.exception("encrypting stored data failed")
            encryption_problem = "Encrypting the stored data failed. See the server log."
    bids.on_event = lambda event, data: hooks.emit(event, data, now())

    def run_job(kind: str, key: str | None, fn: Callable[[], Any]):
        """Runs `fn` now (tests) or in the background (the service). In the background the answer is a ticket the page polls at /api/jobs/<id>."""
        if not config.background_jobs:
            return fn()
        return JSONResponse({"_job": jobs.start(kind, key, fn), "state": "running"}, status_code=202)

    budget = AiBudget(store, config.ai_per_day, config.ai_per_minute, lambda: local_now(config.tz_name).date().isoformat())
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
        portal_names["sample"], portal_states["sample"] = "Sample data", "Sample data"

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
        for name, closer in (("boq", boq.store.close), ("main", store.close)):   # a clean close also folds the write-ahead log back into the database file
            try:
                closer()
            except Exception:  # the process is ending: a failure here must not hide the real shutdown
                log.exception("closing the %s database failed", name)

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
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if config.cookie_secure:  # served over https (Railway): tell browsers never to use plain http for this site
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")  # file names carry a hash of their contents
        elif not path.startswith("/api/") and response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"  # the page itself is always checked, so a new release shows at once
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            record_activity(request, response.status_code)
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

    owner = Principal(accounts.OWNER_ID, config.username or "admin", "Owner", "admin")

    def session_id(request: Request) -> str | None:
        claims = token_claims(signing_key, request.cookies.get(COOKIE))
        sid = claims.get("sid") if claims else None
        return sid if isinstance(sid, str) else None

    def current_user(request: Request) -> Principal | None:
        """Who is signed in on this request, or None. Roles are read fresh each time, so a change or a deactivation applies at once."""
        header = request.headers.get("authorization", "")
        if header[:7].lower() == "bearer ":  # a program using an API token (negative ids are tokens)
            client = client_of(request)
            if limiter.blocked(client):
                raise HTTPException(429, "Too many wrong attempts. Wait a few minutes and try again.")
            found = api_tokens.lookup(header[7:].strip(), now())
            if not found:
                limiter.failed(client)
                return None
            return Principal(-found["id"], f"token:{found['name']}", f"API token: {found['name']}", found["role"])
        sid = session_id(request)
        uid = store.session_touch(sid, time.time(), config.idle_seconds) if sid else None
        if uid is None:
            return None
        if uid == accounts.OWNER_ID:
            return owner
        user = people.get(uid)
        if not user and sid:
            store.session_end(sid)
        return user

    def require_login(request: Request) -> None:
        user = current_user(request)
        if not user:
            raise HTTPException(401, "Please log in")
        request.state.user = user
        needed = accounts.required_role(request.method, request.url.path)
        if not user.can(needed):
            raise HTTPException(403, "Your account is view-only, so it cannot change anything. Ask an administrator for the bidder role." if needed == "bidder"
                                else "Only an administrator can do that.")
        if user.id < 0 and request.url.path.startswith("/api/account"):
            raise HTTPException(403, "An API token cannot use account settings.")
        if user.must_change and request.url.path != "/api/account/password":
            raise HTTPException(403, "Choose a new password before you continue.")

    def record_activity(request: Request, status: int) -> None:
        """Writes who did what to the activity log: every change that went through, every refusal, and the downloads of private files."""
        try:
            user = getattr(request.state, "user", None)
            route = request.scope.get("route")
            if not user or route is None:
                return
            what = accounts.describe_action(request.method, route.path)
            if request.method in ("GET", "HEAD", "OPTIONS") and what.startswith(request.method):
                return  # ordinary reading is not logged; only the named downloads
            if status == 403:
                what = "Refused: " + what
            elif status >= 400:
                return
            target = ", ".join(f"{k}={v}" for k, v in {**request.path_params, **({"key": request.query_params["key"]} if "key" in request.query_params else {})}.items())
            people.log(now(), user.username, user.role, what, target, client=client_of(request))
        except Exception:  # the log must never break a request
            log.exception("activity log write failed")

    auth = [Depends(require_login)]

    def now() -> datetime:
        return local_now(config.tz_name)

    # ---- the routes, one module per area; the order is the order the routes were always added in ----------------------------

    def refresh_priorities() -> None:
        """Re-scores the recommended order. Run after anything that changes what the company likes (a star, a hidden tender, a bid), and every hour."""
        try:
            appsettings.rescore(store, now())
        except Exception:  # the order is a convenience: a failure here must never undo the action that caused it
            log.exception("recomputing the recommended order failed")

    def size_limits() -> dict:
        """The size of work this company takes, as filter values: nothing when no range has been set."""
        return appsettings.size_limits(store.get_settings())

    def shape(row: dict) -> dict:
        row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        row["search_url"] = portal_search.get(row["portal"], "")
        row["value_text"] = format_inr(row["value"])
        return row

    def ai_call(fn, *args, spend: bool = True):
        if spend and ai.configured():
            budget.spend()
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
            "has_source": bids.has_source(bid["key"]), "manual": bid["portal"] == "manual", "comment_count": bids.comment_count(bid["id"]),
        }

    def boq_call(fn, *args, uses_ai: bool = False, **kwargs):
        """Runs a BOQ step. Steps that may ask Claude (reading the BOQ, risks) count against the AI limits; the rest are free."""
        try:
            return ai_call(lambda: fn(*args, **kwargs), spend=uses_ai)
        except BoqError as e:
            raise HTTPException(422, str(e)) from None

    def bid_link(bid_id: int) -> str:
        return f"\n\nOpen it: {config.app_url}/#/bids/{bid_id}" if config.app_url else ""

    def notify(emails: list[str], subject: str, text: str) -> None:
        """Tells people by email, without making the person who acted wait for it. Never raises: a mail problem must not undo their change."""
        if not emails or not manager.mail_configured:
            return

        def send() -> None:
            try:
                body = "<p>" + re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', html_escape(text)).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
                manager.sender(env.brevo_api_key, env.mail_from, env.mail_from_name, emails, f"TendAI: {subject}", body, text)
            except Exception:
                log.exception("notification email failed")

        if manager.background:
            threading.Thread(target=send, daemon=True, name="notify").start()
        else:
            send()

    def public_base(request: Request) -> str:
        if config.app_url:
            return config.app_url
        proto = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip() or request.url.scheme
        return f"{proto}://{request.headers.get('host', 'localhost')}"

    ctx = Ctx(
        ai_call=ai_call,
        api_tokens=api_tokens,
        auth=auth,
        backups=backups,
        bid_as_tender=bid_as_tender,
        bid_error=bid_error,
        bid_full=bid_full,
        bid_link=bid_link,
        bid_or_404=bid_or_404,
        bids=bids,
        boq=boq,
        boq_call=boq_call,
        budget=budget,
        builtin_keys=builtin_keys,
        client_of=client_of,
        company_facts=company_facts,
        config=config,
        current_user=current_user,
        encryption_problem=encryption_problem,
        env=env,
        hooks=hooks,
        jobs=jobs,
        keep_source=keep_source,
        letter_book=letter_book,
        limiter=limiter,
        manager=manager,
        money=money,
        notify=notify,
        now=now,
        owner=owner,
        people=people,
        portal_entry=portal_entry,
        portal_names=portal_names,
        portal_search=portal_search,
        portal_states=portal_states,
        portals_now=portals_now,
        probe=probe,
        profile=profile,
        public_base=public_base,
        refresh_portals=refresh_portals,
        refresh_priorities=refresh_priorities,
        run_job=run_job,
        sealer=sealer,
        session_id=session_id,
        shape=shape,
        signing_key=signing_key,
        size_limits=size_limits,
        sso_session=sso_session,
        storage_note=storage_note,
        store=store,
        tender_for_ai=tender_for_ai,
    )
    register_all(app, ctx)


    # ---- the React page --------------------------------------------------------------------------------------

    if store.priorities_missing(now()):  # tenders collected before the recommended order existed
        refresh_priorities()

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
