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
from html import escape as html_escape
from urllib.parse import urlencode
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.background import BackgroundTask
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from pydantic import BaseModel, Field

from . import access, accounts, ai, alerts, appsettings, events, letters, mailer, pack, privacy, reminders, sample, sealing, smartsearch, sso, tokens
from .accounts import AccountError, Accounts, Principal
from . import offsite
from .backup import Backups, BackupError
from .boq import advisor
from .boq.match import ClaudeVerifier
from .boq.portal import fetch_boq, fetch_risks
from .boq.service import BoqError, BoqService
from .bids import STAGE_LABELS, STAGES, VAULT_KINDS, BidBook, BidError, clean_patch, default_vault_dir, locate_on_portal, portal_facts
from .bids import _when as stored_date
from .gepnic import PortalError, Tender, parse_date, pick_nit
from .config import Portal, Settings, load_settings
from .digest import CSV_COLUMNS, format_inr, safe_cell, tenders_workbook
from .runner import Busy, NotConfigured, RunManager, Scheduler, local_now
from .store import DIMENSIONS, SORTS, SeenStore

log = logging.getLogger("tender_digest.web")
COOKIE = "td_session"
SESSION_SECONDS = 7 * 24 * 3600
MAX_EXPORT_ROWS = 20_000
CSP = "default-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
# Request size limits. Uploads travel as base64 inside JSON (a 10 MB file is about 13.4 MB), so those routes get more room; everything else is small.
MAX_UPLOAD_BODY = 16 * 1024 * 1024
MAX_JSON_BODY = 1024 * 1024
UPLOAD_PATH_RE = re.compile(r"^/api/(vault|boq/catalogue|tenders/(document|manual(/read)?)|bids/\d+/(document|boq|risks|award))$")
MAX_VIEWS = 30
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?)?$")
# The settings a saved view may hold: the page's filters and display choices, nothing else.
VIEW_KEYS = {
    "portal", "category", "department", "location", "work_type", "min_fit", "q", "status", "min_value", "max_value", "value_lt", "no_value", "no_closing", "no_published", "starred", "fits_me",
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
    ai_per_day: int = 300  # most AI requests a person can start in a day (0 = no limit); scheduled labelling has its own cap, AI_MAX_PER_RUN
    ai_per_minute: int = 20
    app_url: str = ""  # this site's public address (APP_URL), used for links in reminder emails and the calendar
    owner_totp: str = ""  # a base32 secret: when set, the owner login also needs the six-digit code from an authenticator app
    background_jobs: bool = False  # True in the real service: long actions run in the background and the page polls. Tests run them inline.
    trusted_proxy_hops: int = 1  # how many reverse proxies sit in front (Railway: 1). 0 = ignore X-Forwarded-For


def real_date(value: str) -> bool:
    """Looks like 2026-11-20 (optionally with a time) AND is a date that exists: 2026-13-45 is not."""
    if not DATE_RE.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


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

def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(secret: str, now: float | None = None, sid: str | None = None, ttl: int | None = None, extra: dict[str, Any] | None = None) -> str:
    claims: dict[str, Any] = {"exp": int((now or time.time()) + (ttl or SESSION_SECONDS)), **(extra or {})}
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
        except Exception:  # noqa: BLE001 - the person gets a plain message, the log gets the trace
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


class NewUser(BaseModel):
    username: str
    name: str = ""
    role: str = "viewer"
    password: str
    email: str = ""


class OwnEmail(BaseModel):
    email: str = ""


class AwardBody(BaseModel):
    text: str = ""
    pdf_base64: str = ""


class BiddersBody(BaseModel):
    bidders: list[dict[str, Any]] = Field(max_length=30)


class CommentBody(BaseModel):
    text: str


class SignoffBody(BaseModel):
    action: str
    note: str = ""


class UserEdit(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None
    reset_2fa: bool = False
    email: str | None = None


class PasswordChange(BaseModel):
    current: str
    new: str


class LoginBody(BaseModel):
    username: str = ""
    password: str
    code: str = ""


class EraseBody(BaseModel):
    confirm: str = Field(max_length=80)


class TokenBody(BaseModel):
    name: str = Field(max_length=80)
    role: str = "viewer"
    days: int | None = Field(default=None, ge=1, le=3650)


class HookBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = Field(default=None, max_length=20)
    active: bool | None = None


class ResetRequest(BaseModel):
    who: str = Field(max_length=120)


class ResetConfirm(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=300)


class WebhookTest(BaseModel):
    url: str = ""


class TwoStepCode(BaseModel):
    code: str


class TwoStepOff(BaseModel):
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
    alert: bool = False  # email the new matches in the daily digest


class HideBody(BaseModel):
    keys: list[str] = Field(max_length=200)
    hidden: bool = True
    mute_department: bool = False


class MuteBody(BaseModel):
    department: str = Field(max_length=200)
    muted: bool = True


class SizeBody(BaseModel):
    min: float | None = Field(default=None, ge=0, lt=1e13)
    max: float | None = Field(default=None, ge=0, lt=1e13)


class CleanupBody(BaseModel):
    days: int = Field(ge=7, le=3650)


class BulkStar(BaseModel):
    keys: list[str] = Field(max_length=200)
    starred: bool


class TemplateBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    body: str = Field(max_length=letters.MAX_BODY * 2)
    note: str = Field(default="", max_length=200)


class TemplateRestore(BaseModel):
    version: int = Field(ge=1)


class LetterFill(BaseModel):
    template_id: int


class Corrigendum(BaseModel):
    note: str = Field(max_length=600)
    closing: str | None = Field(default=None, max_length=20)


class DocChecked(BaseModel):
    checked: bool


class BulkBids(BaseModel):
    keys: list[str] = Field(max_length=50)


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


class BoqCost(BaseModel):
    cost: float | None = None  # what the line costs you in total; null removes it


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


def create_app(config: AppConfig, env: Settings, store: SeenStore, manager: RunManager | None = None, prober: Callable[[str], dict] | None = None, sso_session: Any = None) -> FastAPI:
    vault_dir = default_vault_dir(env.state_path)
    sealer = sealing.Sealer.from_env()  # raises with a clear message when the key is too short: better than quietly storing things unencrypted
    bids = BidBook(store, vault_dir, sealer)
    letter_book = letters.Letters(store)
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

    def prune_audit_daily(now: datetime) -> None:
        """Once a day, drops activity-log entries older than the retention period set in Settings."""
        day = now.date().isoformat()
        if audit_pruned["day"] == day:
            return
        audit_pruned["day"] = day
        people.prune_audit(now, appsettings.current(env, store.get_settings())["audit_days"])

    def housekeeping(now: datetime) -> None:
        """Run by the scheduler every half minute: the daily backup, then any deadline reminders that are due. One failing never stops the other."""
        for job in (lambda: backups.housekeeping(now), lambda: manager.send_reminders(now, config.app_url), lambda: hooks.deliver(now), lambda: prune_audit_daily(now)):
            try:
                job()
            except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001 - the log must never break a request
            log.exception("activity log write failed")

    auth = [Depends(require_login)]

    def now() -> datetime:
        return local_now(config.tz_name)

    # ---- auth ------------------------------------------------------------------------------------------------

    @app.get("/api/jobs/{job_id}", dependencies=auth)
    def job_state(job_id: str) -> dict:
        found = jobs.get(job_id)
        if not found:
            raise HTTPException(404, "That action is no longer being tracked: the server restarted, or it finished long ago. Check the page, and run it again if needed")
        return found

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
        typed = body.username.strip().lower()
        owner_name_ok = not config.username or hmac.compare_digest(typed.encode(), config.username.lower().encode())
        owner_pass_ok = hmac.compare_digest(body.password.encode(), config.password.encode())
        who = owner if (owner_name_ok and owner_pass_ok) else people.authenticate(typed, body.password)
        if not who:
            limiter.failed(client)
            people.log(now(), typed[:80] or "?", None, "Failed sign-in", client=client)
            raise HTTPException(401, "Wrong username or password")
        if (who.owner and config.owner_totp) or (not who.owner and who.two_step):
            code = body.code.strip()
            if not code:  # the password was right: ask for the code. Nothing is signed in yet.
                return JSONResponse({"detail": "Enter the 6-digit code from your authenticator app.", "need_code": True}, status_code=401)
            if who.owner:
                step = accounts.totp_match(config.owner_totp, code, time.time(), int(store.get_settings().get("owner_totp_last", 0)))
                if step is not None:
                    store.put_settings({"owner_totp_last": step})
                good = step is not None
            else:
                good = people.totp_check(who.id, code)
            if not good:
                limiter.failed(client)
                people.log(now(), who.username, who.role, "Failed sign-in (wrong two-step code)", client=client)
                raise HTTPException(401, "That code is not right. Wait for a new one from the app and try again.")
        limiter.succeeded(client)
        sid = secrets.token_urlsafe(24)
        store.session_start(sid, time.time(), SESSION_SECONDS, who.id, client, accounts.describe_agent(request.headers.get("user-agent", "")))
        if not who.owner:
            people.record_login(who.id, now())
        people.log(now(), who.username, who.role, "Signed in", client=client)
        response.set_cookie(COOKIE, make_token(signing_key, sid=sid), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        return {"ok": True}

    # ---- single sign-on (OpenID Connect): the provider signs people in, this app starts their session ------------------------------

    sso_provider = None
    sso_problem = ""
    try:
        sso_cfg = sso.config_from_env()
        sso_provider = sso.Provider(sso_cfg, sso_session) if (sso_cfg and sso_session is not None) else (sso.Provider(sso_cfg) if sso_cfg else None)
    except sso.SSOError as e:
        sso_problem = e.message
        log.warning("single sign-on is not active: %s", e.message)

    SSO_COOKIE = "td_sso"

    def sso_redirect_uri(request: Request) -> str:
        return f"{public_base(request)}/api/sso/callback"

    def sso_fail(request: Request, err: sso.SSOError | str, code: str = "failed") -> RedirectResponse:
        message = err.message if isinstance(err, sso.SSOError) else err
        code = err.code if isinstance(err, sso.SSOError) else code
        people.log(now(), "?", None, f"Failed SSO sign-in ({code})", client=client_of(request))
        resp = RedirectResponse(f"/?sso_error={urlencode({'m': message})[2:]}", status_code=303)
        resp.delete_cookie(SSO_COOKIE, path="/")
        return resp

    @app.get("/api/sso/status")
    def sso_status() -> dict:
        return {"enabled": sso_provider is not None, "button_text": sso_provider.cfg.button_text if sso_provider else ""}

    @app.get("/api/sso/login")
    def sso_login(request: Request):
        if sso_provider is None:
            raise HTTPException(404, "Single sign-on is not set up")
        client = client_of(request)
        if limiter.blocked(client):
            raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
        try:
            url, remembered = sso_provider.start(sso_redirect_uri(request))
        except sso.SSOError as e:
            return sso_fail(request, e)
        resp = RedirectResponse(url, status_code=303)
        # Lax, not Strict: the provider sends the browser back here from another site, and a Strict cookie would not come along
        resp.set_cookie(SSO_COOKIE, make_token(signing_key, ttl=600, extra={"st": remembered["state"], "nn": remembered["nonce"], "vf": remembered["verifier"]}),
                        max_age=600, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")
        return resp

    @app.get("/api/sso/callback")
    def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if sso_provider is None:
            raise HTTPException(404, "Single sign-on is not set up")
        client = client_of(request)
        claims = token_claims(signing_key, request.cookies.get(SSO_COOKIE))
        if error:
            return sso_fail(request, "The provider did not sign you in" + (": access was denied" if error == "access_denied" else ""), error[:30])
        if not claims or not code or not hmac.compare_digest(str(claims.get("st", "")).encode(), state.encode()):
            limiter.failed(client)
            return sso_fail(request, sso.SSOError("state", "That sign-in link is not valid or has expired. Start again"))
        try:
            who_claims = sso_provider.finish(code, sso_redirect_uri(request), {"nonce": claims["nn"], "verifier": claims["vf"]})
            who = people.sso_login(who_claims, sso_provider.cfg.default_role, now())
        except (sso.SSOError, AccountError) as e:
            return sso_fail(request, e if isinstance(e, sso.SSOError) else sso.SSOError("account", str(e)))
        sid = secrets.token_urlsafe(24)
        store.session_start(sid, time.time(), SESSION_SECONDS, who.id, client, accounts.describe_agent(request.headers.get("user-agent", "")))
        people.record_login(who.id, now())
        people.log(now(), who.username, who.role, "Signed in with SSO", client=client)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, make_token(signing_key, sid=sid), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        resp.delete_cookie(SSO_COOKIE, path="/")
        return resp

    @app.get("/api/sso/info", dependencies=auth)
    def sso_info(request: Request) -> dict:
        """What an administrator needs to set SSO up or check it: the address to register with the provider, and who is allowed."""
        c = sso_provider.cfg if sso_provider else None
        return {"enabled": c is not None, "problem": sso_problem, "issuer": c.issuer if c else "", "client_id": c.client_id if c else "", "redirect_uri": sso_redirect_uri(request),
                "allowed_domains": c.allowed_domains if c else [], "default_role": c.default_role if c else "viewer"}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        who = current_user(request)
        if who:
            people.log(now(), who.username, who.role, "Signed out", client=client_of(request))
        sid = session_id(request)
        if sid:
            store.session_end(sid)  # the cookie stops working for good, even if someone copied it
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/branding")
    def branding() -> dict:
        # Public on purpose: the login screen shows the logo before anyone has signed in. Nothing secret is in it.
        cur = appsettings.current(env, store.get_settings())
        return {"logo_text": cur["logo_text"], "accent": cur["accent"]}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        who = current_user(request)
        return {"authenticated": who is not None, "user": who.public() if who else None}

    # ---- data ------------------------------------------------------------------------------------------------

    def size_limits() -> dict:
        """The size of work this company takes, as filter values: nothing when no range has been set."""
        saved = store.get_settings()
        out = {}
        for key, name in (("size_min", "size_min"), ("size_max", "size_max")):
            v = saved.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                out[key] = float(v)
        return out

    def filter_params(
        portal: list[str] | None = Query(None), category: list[str] | None = Query(None), department: list[str] | None = Query(None),
        location: list[str] | None = Query(None), work_type: list[str] | None = Query(None), min_fit: int | None = Query(None, ge=1, le=5), q: str | None = Query(None, max_length=100), status: str = "open",
        min_value: float | None = Query(None, ge=0), max_value: float | None = Query(None, ge=0), value_lt: float | None = Query(None, ge=0),
        no_value: bool = False, no_closing: bool = False, no_published: bool = False, starred: bool = False,
        closing_from: str | None = None, closing_to: str | None = None, seen_from: str | None = None, seen_to: str | None = None,
        published_from: str | None = None, published_to: str | None = None,
        closing_within: int | None = Query(None, ge=1, le=365), since_days: int | None = Query(None, ge=1, le=365),
        hidden: str = "exclude", fits_me: bool = False,
    ) -> dict:
        if status not in ("open", "closing_soon", "closed", "all"):
            raise HTTPException(422, "Unknown status")
        if hidden not in ("exclude", "only"):
            raise HTTPException(422, "Unknown value for hidden")
        dates = dict(closing_from=closing_from, closing_to=closing_to, seen_from=seen_from, seen_to=seen_to, published_from=published_from, published_to=published_to)
        for name, value in dates.items():
            if value and not real_date(value):
                raise HTTPException(422, f"{name} must be a real date, like 2026-11-20")
        lists = {"portal": portal, "category": category, "department": department, "location": location, "work_type": work_type}
        for name, values in lists.items():
            if values and (len(values) > 50 or any(len(v) > 200 for v in values)):
                raise HTTPException(422, f"Too many or too long values for {name}")
        return {
            **{k: v for k, v in lists.items() if v}, "q": q, "status": status, "min_value": min_value, "max_value": max_value, "value_lt": value_lt,
            "no_value": no_value, "no_closing": no_closing, "no_published": no_published, "starred": starred, "closing_within": closing_within, "since_days": since_days, "min_fit": min_fit,
            "soon_days": env.closing_soon_days, **{k: v for k, v in dates.items() if v}, "hidden": hidden, **(size_limits() if fits_me else {}),
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
            "soon_days": env.closing_soon_days, "ai_enabled": ai.configured(), "storage_warning": storage_note, "require_signoff": cur["require_signoff"],
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
        out = {"items": [shape(r) for r in rows], "total": total, "page": page, "page_size": page_size, "categories": store.categories()}
        if f.get("q"):
            parsed = smartsearch.parse(str(f["q"]))
            out["understood"] = parsed.describe() if (parsed.has_text or parsed.min_value is not None or parsed.max_value is not None) else ""
            if total == 0 and parsed.has_text:
                out["suggest"] = smartsearch.suggest(str(f["q"]), store.vocabulary())
        return out

    @app.get("/api/tenders/export", dependencies=auth)
    def export(f: dict = fparams, sort: str = "closing", format: str = Query("csv", pattern="^(csv|xlsx)$"), keys: list[str] | None = Query(None, max_length=300)) -> Response:
        """The tenders the filters select (or only the `keys` a person ticked) as a CSV or an Excel workbook."""
        if sort not in SORTS:
            raise HTTPException(422, "Unknown sort")
        if keys:
            if len(keys) > 300 or any(len(k) > 300 for k in keys):
                raise HTTPException(422, "Too many tenders selected")
            f = {**f, "keys": keys, "status": "all"}
        rows, _ = store.search(now(), f, sort, MAX_EXPORT_ROWS, 0)
        table = [[
            "Open" if not r["closing"] or r["closing"] >= now().isoformat() else "Closed", portal_names.get(r["portal"], r["portal"]), r["tender_id"],
            r["description"] or r["title"], (r["organisation"] or "").replace("||", " > "), r["category"], r["location"],
            "" if r["value"] is None else int(r["value"]), "" if r["emd"] is None else int(r["emd"]), r["published"] or "", r["closing"] or "",
        ] for r in rows]
        stamp = now().strftime("%Y-%m-%d")
        if format == "xlsx":
            return Response(tenders_workbook(table), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="tenders-{stamp}.xlsx"'})
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for r in table:
            writer.writerow([safe_cell(c) for c in r])
        return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="tenders-{stamp}.csv"'})

    @app.post("/api/tenders/star-bulk", dependencies=auth)
    def star_bulk(body: BulkStar) -> dict:
        """Stars or unstars every ticked tender at once."""
        done = sum(1 for k in dict.fromkeys(body.keys) if store.set_starred(k, body.starred, now()))
        return {"changed": done, "starred": body.starred}

    # ---- tenders you are not interested in, the size of work you take, and clean-up ---------------------------------------------

    @app.post("/api/tenders/hide", dependencies=auth)
    def hide_tenders(body: HideBody, request: Request) -> dict:
        """Marks tenders as not interesting (or brings them back). With mute_department, everything from their departments is hidden too."""
        user: Principal = request.state.user
        if not body.hidden:
            return {"changed": store.unhide(body.keys), "hidden": False, "muted": []}
        muted = []
        if body.mute_department:
            for d in dict.fromkeys(store.department_of(k) for k in body.keys):
                if d and store.mute_department(d, now(), user.username):
                    muted.append(d)
        return {"changed": store.hide(body.keys, now(), user.username), "hidden": True, "muted": muted}

    @app.get("/api/mutes", dependencies=auth)
    def list_mutes() -> dict:
        return {"departments": store.muted_departments(), "hidden_tenders": store.search(now(), {"hidden": "only", "status": "all"}, "closing", 1, 0)[1]}

    @app.post("/api/mutes", dependencies=auth)
    def set_mute(body: MuteBody, request: Request) -> dict:
        ok = store.mute_department(body.department, now(), request.state.user.username) if body.muted else store.unmute_department(body.department)
        return {"changed": ok, "departments": store.muted_departments()}

    @app.get("/api/company-size", dependencies=auth)
    def get_company_size() -> dict:
        lim = size_limits()
        return {"min": lim.get("size_min"), "max": lim.get("size_max")}

    @app.put("/api/company-size", dependencies=auth)
    def put_company_size(body: SizeBody) -> dict:
        lo, hi = body.min or None, body.max or None
        if lo and hi and lo > hi:
            raise HTTPException(422, "The smallest size cannot be bigger than the largest")
        store.put_settings({"size_min": lo, "size_max": hi})
        return {"min": lo, "max": hi}

    @app.get("/api/cleanup/preview", dependencies=auth)
    def cleanup_preview(days: int = Query(30, ge=7, le=3650)) -> dict:
        return store.cleanup_preview(now(), days)

    @app.post("/api/cleanup", dependencies=auth)
    def cleanup_now(body: CleanupBody) -> dict:
        """Removes closed tenders older than `days` that nobody starred and no bid uses, and the placeholders for tenders judged not civil."""
        return store.cleanup(now(), body.days)

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

    @app.post("/api/tenders/summary", dependencies=auth)
    def tender_summary(body: SummaryBody) -> dict:
        row = tender_for_ai(body.key)
        if row.get("summary") and not body.refresh:
            return {"summary": row["summary"], "cached": True}
        text = ai_call(ai.summarise, row)
        store.set_ai_text(body.key, "summary", text)
        return {"summary": text, "cached": False}

    @app.post("/api/tenders/document", dependencies=auth)
    def tender_document(body: DocBody):
        return run_job("tender-document", body.key, lambda: do_tender_document(body))

    def do_tender_document(body: DocBody) -> dict:
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
    def manual_read(body: ListingBody):
        return run_job("manual-read", None, lambda: do_manual_read(body))

    def do_manual_read(body: ListingBody) -> dict:
        """Reads the listing details out of a notice, for the person to check and correct. Saves nothing."""
        return {"listing": ai_call(ai.read_listing, body.text, body.pdf_base64)}

    @app.get("/api/tenders/manual/check", dependencies=auth)
    def manual_check(tender_id: str = Query(..., max_length=80)) -> dict:
        """Is this Tender ID already held, from a portal or added earlier?"""
        found = store.find_by_tender_id(tender_id.strip())
        return {"existing": [{"key": f["key"], "portal_name": portal_names.get(f["portal"], f["portal"]), "organisation": f["organisation"], "manual": f["portal"] == "manual"} for f in found]}

    @app.post("/api/tenders/manual", dependencies=auth)
    def manual_create(body: ManualBody):
        return run_job("manual-create", body.tender_id.strip(), lambda: do_manual_create(body))

    def do_manual_create(body: ManualBody) -> dict:
        with manual_lock:  # the "already there?" check and the save are one step, so two people cannot both add the same Tender ID
            return _manual_create(body)

    manual_lock = threading.Lock()

    def _manual_create(body: ManualBody) -> dict:
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
                    budget.spend()
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
        return {"configured": ai.configured(), "model": ai.model(), "fast_model": ai.fast_model(), "profile_set": bool(profile().strip()), **store.ai_counts(now()),
                "calls_today": budget.used_today(), "daily_limit": config.ai_per_day}

    @app.post("/api/ai/test", dependencies=auth)
    def ai_test() -> dict:
        ai_call(ai.ping)
        return {"ok": True, "model": ai.model(), "fast_model": ai.fast_model()}

    @app.post("/api/ai/score", dependencies=auth)
    def ai_score():
        return run_job("ai-score", "", lambda: do_ai_score())

    def do_ai_score() -> dict:
        if not ai.configured():
            raise HTTPException(503, "AI features are off. Set ANTHROPIC_API_KEY on the server to turn them on.")
        budget.spend(-(-45 // ai.BATCH))  # one request per batch of tenders
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
            views.append({"id": v.id, "name": name, "filters": v.filters, "alert": v.alert})
        if sum(1 for v in views if v["alert"]) > alerts.MAX_ALERT_VIEWS:
            raise HTTPException(422, f"At most {alerts.MAX_ALERT_VIEWS} saved searches can email you. Turn one off first")
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
            "has_source": bids.has_source(bid["key"]), "manual": bid["portal"] == "manual", "comment_count": bids.comment_count(bid["id"]),
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

    @app.post("/api/bids/bulk", dependencies=auth)
    def create_bids_bulk(body: BulkBids) -> dict:
        """Starts a bid on every ticked tender that has none yet."""
        created, existing, missing = [], [], []
        for key in dict.fromkeys(body.keys):
            try:
                bid, made = bids.create_from_tender(key, now())
            except BidError:
                missing.append(key)
                continue
            if made:
                bids.generate_items(bid["id"], now())
                created.append(bid["id"])
            else:
                existing.append(bid["id"])
        return {"created": created, "existing": existing, "missing": missing}

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
    def patch_bid(bid_id: int, body: dict[str, Any], request: Request) -> dict:
        current = bid_or_404(bid_id)
        user: Principal = request.state.user
        new_stage = body.get("stage")
        if (appsettings.current(env, store.get_settings())["require_signoff"] and current["stage"] == "interested" and new_stage in ("preparing", "submitted")
                and not user.can("admin") and (current.get("signoff") or {}).get("state") != "approved"):
            raise HTTPException(422, "This bid needs an administrator's approval before it moves on. Use 'Ask for approval' on the Team tab.")
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
    def patch_bid_item(bid_id: int, item_id: int, body: dict[str, Any], request: Request) -> dict:
        bid = bid_or_404(bid_id)
        before = bids.get_item(bid_id, item_id)
        try:
            item = bids.update_item(bid_id, item_id, body)
        except BidError as e:
            raise bid_error(e) from None
        who = (item.get("owner") or "").strip().lower()
        if "owner" in body and who and before and (before.get("owner") or "").strip().lower() != who and who != request.state.user.username.lower():
            notify(people.emails_for([who]), f"{request.state.user.name} gave you a task on a bid",
                   f"{request.state.user.name} assigned you: {item['text']}\n\nBid: {(bid['description'] or bid['title'])[:120]} (ID {bid['tender_id']})\nCloses: {bid.get('closing') or 'not set'}{bid_link(bid_id)}")
        return item

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
    def bid_document(bid_id: int, body: DocBody):
        return run_job("bid-document", str(bid_id), lambda: do_bid_document(bid_id, body))

    def do_bid_document(bid_id: int, body: DocBody) -> dict:
        bid = bid_or_404(bid_id)
        doc = ai_call(ai.read_document, bid_as_tender(bid), body.text, body.pdf_base64)
        keep_source(bid["key"], body.text, body.pdf_base64)
        bids.set_json(bid_id, "doc", doc, now())
        if store.get_tender(bid["key"]):
            store.set_ai_text(bid["key"], "doc", json.dumps(doc))
        return {"doc": doc, "added": bids.generate_items(bid_id, now())}

    fetch_lock = threading.Lock()

    @app.post("/api/bids/{bid_id}/fetch", dependencies=auth)
    def bid_fetch(bid_id: int):
        return run_job("bid-fetch", str(bid_id), lambda: do_bid_fetch(bid_id))

    def do_bid_fetch(bid_id: int) -> dict:
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
                            budget.spend()
                            doc = ai.read_document(bid_as_tender(bid), "", base64.b64encode(data).decode())
                            bids.set_json(bid_id, "doc", doc, now())
                            if store.get_tender(bid["key"]):
                                store.set_ai_text(bid["key"], "doc", json.dumps(doc))
                            state, message = "read", "Read the tender notice from the portal."
                        except ai.AIError as e:
                            state, message = "ai_failed", str(e)
            added = bids.generate_items(bid_id, now())
            boq_out = boq_call(fetch_boq, client, boq, bid["key"], links, nit["href"] if nit else None, uses_ai=True)
            risks_out = boq_call(fetch_risks, boq, bid["key"], bid_as_tender(bid), nit_pdf, uses_ai=True)
            return {"facts": facts, "added": added, "nit": state, "message": message, "corrigenda": corrigenda, "boq": boq_out, "risks": risks_out}
        finally:
            fetch_lock.release()

    @app.post("/api/bids/{bid_id}/doc-checked", dependencies=auth)
    def bid_doc_checked(bid_id: int, body: DocChecked, request: Request) -> dict:
        """Records that a person compared what the AI read from the tender notice with the notice itself (or takes that back)."""
        bid = bid_or_404(bid_id)
        if not bid.get("doc"):
            raise HTTPException(422, "No tender document has been read for this bid yet")
        mark = {"by": request.state.user.username, "at": now().isoformat(timespec="seconds")} if body.checked else None
        bids.set_json(bid_id, "doc_checked", mark, now())
        # set_json("doc_checked") must not clear itself: only a new "doc" does that
        return {"doc_checked": mark}

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

    def boq_call(fn, *args, uses_ai: bool = False, **kwargs):
        """Runs a BOQ step. Steps that may ask Claude (reading the BOQ, risks) count against the AI limits; the rest are free."""
        try:
            return ai_call(lambda: fn(*args, **kwargs), spend=uses_ai)
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
    def bid_boq_upload(bid_id: int, body: BoqUpload):
        return run_job("boq-upload", str(bid_id), lambda: do_bid_boq_upload(bid_id, body))

    def do_bid_boq_upload(bid_id: int, body: BoqUpload) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.analyse, bid["key"], body.filename, body.file_base64, pdf_reader=ai.read_boq_pdf if ai.configured() else None, uses_ai=True)

    @app.post("/api/bids/{bid_id}/boq/lines/{line_no}", dependencies=auth)
    def bid_boq_confirm(bid_id: int, line_no: int, body: BoqConfirm, request: Request) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.confirm, bid["key"], line_no, body.item_code, request.state.user.username)

    @app.post("/api/bids/{bid_id}/boq/lines/{line_no}/cost", dependencies=auth)
    def bid_boq_cost(bid_id: int, line_no: int, body: BoqCost) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.set_cost, bid["key"], line_no, body.cost)

    @app.get("/api/bids/{bid_id}/risks", dependencies=auth)
    def bid_risks(bid_id: int) -> dict:
        bid = bid_or_404(bid_id)
        return boq.risks(bid["key"])

    @app.post("/api/bids/{bid_id}/risks", dependencies=auth)
    def bid_risks_read(bid_id: int, body: RiskBody):
        return run_job("risks-read", str(bid_id), lambda: do_bid_risks_read(bid_id, body))

    def do_bid_risks_read(bid_id: int, body: RiskBody) -> dict:
        bid = bid_or_404(bid_id)
        text, pdf = body.text, body.pdf_base64
        if not text.strip() and not pdf:
            text, pdf = bids.load_source(bid["key"])  # the notice kept when the tender was added or its document read
            if not text and not pdf:
                raise HTTPException(422, "No notice is saved for this tender yet. Upload it or paste its text.")
        out = boq_call(boq.read_risks, bid["key"], bid_as_tender(bid), text, pdf, uses_ai=True)
        if body.text.strip() or body.pdf_base64:
            keep_source(bid["key"], body.text, body.pdf_base64)
        return out

    @app.get("/api/bids/{bid_id}/advice", dependencies=auth)
    def bid_advice(bid_id: int, min_margin: float = Query(3.0, ge=0, lt=50), target_margin: float = Query(8.0, gt=0, lt=50), include_review: bool = False) -> dict:
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
        try:
            found = bids.vault_bytes(doc_id)
        except sealing.SealError as e:
            raise HTTPException(500, str(e)) from None
        if not found:
            raise HTTPException(404, "No file is stored for that document")
        content, name = found
        return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="' + re.sub(r'[^\w.\- ()]', "_", name) + '"'})

    @app.get("/api/company-profile", dependencies=auth)
    def get_company_profile() -> dict:
        """The company description the AI uses for fit scores and drafts. Its own route, so bidders can keep it without access to the rest of Settings."""
        return {"company_profile": profile()}

    @app.put("/api/company-profile", dependencies=auth)
    def put_company_profile(body: dict[str, Any]) -> dict:
        try:
            cleaned = appsettings.clean({"company_profile": body.get("company_profile")}, list(portal_names))
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        store.put_settings({"company_profile": cleaned["company_profile"]})
        return {"company_profile": profile()}

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

    # ---- the bid pack, letter templates, comparing tenders, hand-recorded corrigenda ---------------------------------------

    @app.get("/api/bids/{bid_id}/pack", dependencies=auth)
    def bid_pack(bid_id: int):
        """A zip of the papers this bid needs, in checklist order, with an index of what is in it and what is missing."""
        bid = bid_or_404(bid_id)
        path, summary = pack.build(bid, bids.with_matches(bid, now()), bids, now())
        stem = pack.safe_name(bid.get("tender_id") or f"bid-{bid_id}", 40)
        return FileResponse(path, media_type="application/zip", filename=f"bid-pack-{stem}.zip", background=BackgroundTask(path.unlink, missing_ok=True),
                            headers={"X-Pack-Files": str(summary["files"]), "X-Pack-Attention": str(summary["needs_attention"])})

    def template_error(e: letters.LetterError) -> HTTPException:
        return HTTPException(422, str(e))

    @app.get("/api/letter-templates", dependencies=auth)
    def letter_templates() -> dict:
        facts = [{"key": f"company_{letters.slug(f['label'])}", "label": f["label"]} for f in company_facts() if letters.slug(f.get("label", ""))]
        fields = [{"key": k, "label": v} for k, v in letters.BID_FIELDS.items()]
        return {"templates": letter_book.list(), "fields": fields + facts}

    @app.post("/api/letter-templates", dependencies=auth)
    def letter_template_create(body: TemplateBody, request: Request) -> dict:
        try:
            return letter_book.create(body.name, body.body, request.state.user.username, now(), body.note)
        except letters.LetterError as e:
            raise template_error(e) from None

    @app.put("/api/letter-templates/{template_id}", dependencies=auth)
    def letter_template_save(template_id: int, body: TemplateBody, request: Request) -> dict:
        try:
            return letter_book.save(template_id, body.body, request.state.user.username, now(), body.name, body.note)
        except letters.LetterError as e:
            raise (HTTPException(404, str(e)) if str(e) == "No such template" else template_error(e)) from None

    @app.get("/api/letter-templates/{template_id}/versions", dependencies=auth)
    def letter_template_versions(template_id: int) -> dict:
        if not letter_book.get(template_id):
            raise HTTPException(404, "No such template")
        return {"versions": letter_book.versions(template_id)}

    @app.post("/api/letter-templates/{template_id}/restore", dependencies=auth)
    def letter_template_restore(template_id: int, body: TemplateRestore, request: Request) -> dict:
        if not letter_book.get(template_id):
            raise HTTPException(404, "No such template")
        try:
            return letter_book.restore(template_id, body.version, request.state.user.username, now())
        except letters.LetterError as e:
            raise template_error(e) from None

    @app.delete("/api/letter-templates/{template_id}", dependencies=auth)
    def letter_template_delete(template_id: int) -> dict:
        if not letter_book.delete(template_id):
            raise HTTPException(404, "No such template")
        return {"removed": template_id}

    @app.post("/api/bids/{bid_id}/letter", dependencies=auth)
    def bid_letter(bid_id: int, body: LetterFill) -> dict:
        """A template filled in from this bid and the company facts. Placeholders with no value stay in the text and are listed."""
        bid = bid_or_404(bid_id)
        tpl = letter_book.get(body.template_id)
        if not tpl:
            raise HTTPException(404, "No such template")
        today = now()

        def day(v: str | None) -> str:
            d = stored_date(v, end_of_day=False) if v else None
            return d.strftime("%d %b %Y" + (", %I:%M %p" if v and len(v) > 10 else "")).replace(" 0", " ") if d else ""

        values = {"tender_id": bid.get("tender_id") or "", "title": bid.get("description") or bid.get("title") or "", "organisation": bid.get("organisation") or "",
                  "portal": portal_names.get(bid.get("portal") or "", bid.get("portal") or ""), "location": bid.get("location") or "", "closing": day(bid.get("closing")),
                  "opening": day(bid.get("opening")), "prebid": day(bid.get("prebid_date")), "emd": format_inr(bid.get("emd")) if bid.get("emd") else "",
                  "value": format_inr(bid.get("value")) if bid.get("value") else "", "today": today.strftime("%d %b %Y").lstrip("0")}
        for f in company_facts():
            values[f"company_{letters.slug(f.get('label', ''))}"] = str(f.get("value", ""))
        text, missing = letters.render(tpl["body"], values)
        return {"text": text, "missing": missing, "template": tpl["name"], "version": tpl["version"]}

    @app.get("/api/tenders/compare", dependencies=auth)
    def tenders_compare(keys: list[str] = Query(..., max_length=4)) -> dict:
        """Two to four tenders side by side, with the derived figures a bidder weighs (EMD as a share of value, days left)."""
        keys = list(dict.fromkeys(keys))
        if not 2 <= len(keys) <= 4:
            raise HTTPException(422, "Choose two to four tenders to compare")
        out = []
        for k in keys:
            row = store.get_tender(k)
            if not row:
                raise HTTPException(404, "One of those tenders is no longer in the list")
            row = shape(row)
            closing = stored_date(row.get("closing"), end_of_day=False) if row.get("closing") else None
            value, emd = row.get("value"), row.get("emd")
            bid_id = bids.id_for_key(k)
            out.append({**{f: row.get(f) for f in ("key", "tender_id", "title", "description", "organisation", "portal_name", "category", "location", "work_type", "value", "value_text",
                                                   "emd", "published", "closing", "opening", "fit", "fit_reason", "starred", "manual")},
                        "emd_pct": round(emd / value * 100, 2) if value and emd else None,
                        "days_left": round((closing - now()).total_seconds() / 86400, 1) if closing else None, "bid_id": bid_id})
        return {"tenders": out}

    @app.post("/api/bids/{bid_id}/corrigenda", dependencies=auth)
    def bid_corrigendum(bid_id: int, body: Corrigendum, request: Request) -> dict:
        """Records a corrigendum (and the closing date it set) read on the portal by hand."""
        bid_or_404(bid_id)
        try:
            bids.record_corrigendum(bid_id, body.note, body.closing, now(), request.state.user.username)
        except BidError as e:
            raise bid_error(e) from None
        return bid_full(bid_or_404(bid_id))

    # ---- teamwork: people, comments, approval -------------------------------------------------------------------

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
            except Exception:  # noqa: BLE001
                log.exception("notification email failed")

        if manager.background:
            threading.Thread(target=send, daemon=True, name="notify").start()
        else:
            send()

    @app.get("/api/people", dependencies=auth)
    def people_directory() -> dict:
        """Names to pick from when assigning a task. No contact details."""
        found = people.directory()
        if not any(p["username"] == owner.username for p in found):
            found.append({"username": owner.username, "name": "Owner"})
        return {"people": found}

    @app.post("/api/account/email", dependencies=auth)
    def set_own_email(body: OwnEmail, request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login has no stored email. Add yourself as a person in Settings > People to get notifications")
        try:
            return {"email": people.set_own_email(user.id, body.email)}
        except AccountError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/my-work", dependencies=auth)
    def my_work(request: Request) -> dict:
        """Open checklist items assigned to the signed-in person, on bids still in progress."""
        return {"items": bids.items_for_owner(request.state.user.username)}

    # ---- who bid, and who the competitors are ---------------------------------------------------------------------------

    @app.post("/api/bids/{bid_id}/award", dependencies=auth)
    def bid_award(bid_id: int, body: AwardBody):
        return run_job("award", str(bid_id), lambda: do_bid_award(bid_id, body))

    def do_bid_award(bid_id: int, body: AwardBody) -> dict:
        """Reads an award notice or comparative statement into a list of bidders for the person to check. Saves nothing."""
        bid = bid_or_404(bid_id)
        return {"award": ai_call(ai.read_award, bid_as_tender(bid), body.text, body.pdf_base64)}

    @app.get("/api/bids/{bid_id}/bidders", dependencies=auth)
    def get_bidders(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return {"bidders": bids.bidders(bid_id)}

    @app.put("/api/bids/{bid_id}/bidders", dependencies=auth)
    def put_bidders(bid_id: int, body: BiddersBody) -> dict:
        bid_or_404(bid_id)
        try:
            patch = bids.set_bidders(bid_id, body.bidders, now())
        except BidError as e:
            raise bid_error(e) from None
        return {"bidders": bids.bidders(bid_id), "updated": patch}

    @app.get("/api/competitors", dependencies=auth)
    def competitors() -> dict:
        return {"competitors": bids.competitors()}

    @app.get("/api/bids/{bid_id}/comments", dependencies=auth)
    def list_comments(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return {"comments": bids.comments(bid_id)}

    @app.post("/api/bids/{bid_id}/comments", dependencies=auth)
    def add_comment(bid_id: int, body: CommentBody, request: Request) -> dict:
        bid = bid_or_404(bid_id)
        user: Principal = request.state.user
        try:
            c = bids.add_comment(bid_id, user.username, body.text, now())
        except BidError as e:
            raise bid_error(e) from None
        mentioned = {m.lower() for m in re.findall(r"@([A-Za-z0-9._-]{3,40})", body.text)}
        tell = (mentioned | {(bid.get("owner") or "").strip().lower()}) - {user.username.lower(), ""}
        notify(people.emails_for(sorted(tell)), f"{user.name} commented on a bid",
               f"{user.name} wrote on {(bid['description'] or bid['title'])[:100]} (ID {bid['tender_id']}):\n\n{body.text.strip()[:600]}{bid_link(bid_id)}")
        return c

    @app.delete("/api/bids/{bid_id}/comments/{comment_id}", dependencies=auth)
    def delete_comment(bid_id: int, comment_id: int, request: Request) -> dict:
        user: Principal = request.state.user
        if not bids.delete_comment(bid_id, comment_id, user.username, user.can("admin")):
            raise HTTPException(404, "No such comment, or it is not yours to remove")
        return {"removed": comment_id}

    @app.post("/api/bids/{bid_id}/signoff", dependencies=auth)
    def bid_signoff(bid_id: int, body: SignoffBody, request: Request) -> dict:
        """Asking for, giving, refusing or withdrawing approval to bid. Only administrators approve or decline."""
        bid = bid_or_404(bid_id)
        user: Principal = request.state.user
        cur = bid.get("signoff") or {}
        note = body.note.strip()[:500]
        title = (bid["description"] or bid["title"])[:100]
        stamp = now().isoformat(timespec="seconds")
        if body.action == "request":
            if bid["stage"] != "interested":
                raise HTTPException(422, "Approval is asked for while a bid is still at Interested")
            mark = {"state": "requested", "requested_by": user.username, "by": user.username, "at": stamp, "note": note}
            notify(people.admin_emails(), f"{user.name} asks approval to bid", f"{user.name} asks for approval to bid on {title} (ID {bid['tender_id']}).\n\n{note}{bid_link(bid_id)}")
        elif body.action == "withdraw":
            if not cur or (cur.get("requested_by") != user.username and not user.can("admin")):
                raise HTTPException(403, "Only the person who asked, or an administrator, can withdraw the request")
            mark = None
        elif body.action in ("approve", "decline"):
            if not user.can("admin"):
                raise HTTPException(403, "Only an administrator can approve or decline a bid")
            mark = {"state": "approved" if body.action == "approve" else "declined", "requested_by": cur.get("requested_by") or user.username, "by": user.username, "at": stamp, "note": note}
            if cur.get("requested_by") and cur["requested_by"] != user.username:
                notify(people.emails_for([cur["requested_by"]]), f"Bid {'approved' if body.action == 'approve' else 'declined'}: {title[:60]}",
                       f"{user.name} {'approved' if body.action == 'approve' else 'declined'} the bid on {title} (ID {bid['tender_id']}).\n\n{note}{bid_link(bid_id)}")
        else:
            raise HTTPException(422, "Unknown action")
        bids.set_json(bid_id, "signoff", mark, now())
        return {"signoff": mark}

    # ---- people: accounts, passwords, activity ------------------------------------------------------------------

    @app.post("/api/account/password", dependencies=auth)
    def change_password(body: PasswordChange, request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login is set by the server's APP_PASSWORD variable. Change it there")
        try:
            people.change_own_password(user.id, body.current, body.new)
        except AccountError as e:
            raise HTTPException(422, str(e)) from None
        store.sessions_end_for(user.id, except_sid=session_id(request))  # every other device is signed out
        return {"ok": True}

    @app.post("/api/account/2fa/start", dependencies=auth)
    def two_step_start(request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login's two-step code is set on the server with OWNER_TOTP_SECRET")
        secret = people.totp_start(user.id)
        return {"secret": secret, "uri": accounts.totp_uri(secret, user.username)}

    @app.post("/api/account/2fa/enable", dependencies=auth)
    def two_step_enable(body: TwoStepCode, request: Request) -> dict:
        try:
            people.totp_enable(request.state.user.id, body.code)
        except AccountError as e:
            raise HTTPException(422, str(e)) from None
        return {"ok": True}

    @app.post("/api/account/2fa/disable", dependencies=auth)
    def two_step_disable(body: TwoStepOff, request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner or people.authenticate(user.username, body.password) is None:
            raise HTTPException(422, "The password is not right")
        people.totp_off(user.id)
        return {"ok": True}

    @app.post("/api/account/sessions/end-others", dependencies=auth)
    def end_other_sessions(request: Request) -> dict:
        store.sessions_end_for(request.state.user.id, except_sid=session_id(request))
        return {"ok": True}

    @app.get("/api/account/sessions", dependencies=auth)
    def my_sessions(request: Request) -> dict:
        """The devices this person is signed in on."""
        mine = session_id(request)
        rows = store.sessions_of(request.state.user.id, time.time(), config.idle_seconds)
        return {"sessions": [{"ref": r["ref"], "device": r["agent"] or "Unknown device", "client": r["client"], "created": r["created"], "last_seen": r["last_seen"],
                              "current": r["sid"] == mine} for r in rows]}

    @app.delete("/api/account/sessions/{ref}", dependencies=auth)
    def end_session(ref: str, request: Request) -> dict:
        if not store.session_end_ref(request.state.user.id, ref):
            raise HTTPException(404, "That sign-in is already over")
        return {"ok": True}

    # ---- forgotten password: public, so each step is throttled and says the same thing whoever asks ---------------------

    reset_hits: dict[str, list[float]] = {}

    def reset_throttle(client: str, kind: str, limit: int) -> None:
        t = time.time()
        hits = [x for x in reset_hits.get(f"{kind}:{client}", []) if t - x < 900]
        if len(hits) >= limit:
            reset_hits[f"{kind}:{client}"] = hits
            raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")
        hits.append(t)
        reset_hits[f"{kind}:{client}"] = hits
        if len(reset_hits) > 5000:  # never grows without bound
            for k in [k for k, v in reset_hits.items() if not v or t - v[-1] > 900]:
                reset_hits.pop(k, None)

    @app.post("/api/password-reset/request")
    def password_reset_request(body: ResetRequest, request: Request) -> dict:
        reset_throttle(client_of(request), "ask", 6)
        if not manager.mail_configured:
            raise HTTPException(503, "Email is not set up on this server, so a reset link cannot be sent. Ask an administrator to set a new password for you.")
        found = people.reset_request(body.who, time.time())
        if found:
            user, token = found
            link = f"{public_base(request)}/#/reset/{token}"
            notify([user.email], "Reset your password",
                   f"Someone asked to reset the password for {user.username}.\n\nOpen this link to choose a new one (it works once and stops working in {people.RESET_MINUTES} minutes):\n{link}\n\n"
                   "If you did not ask for this, ignore this email: your password has not changed.")
            people.log(now(), user.username, user.role, "Asked for a password reset link", client=client_of(request))
        return {"ok": True, "message": "If that matches an account with an email address, a link to choose a new password is on its way."}

    @app.post("/api/password-reset/confirm")
    def password_reset_confirm(body: ResetConfirm, request: Request) -> dict:
        reset_throttle(client_of(request), "use", 10)
        try:
            user = people.reset_confirm(body.token, body.password, time.time())
        except AccountError as e:
            raise HTTPException(422, str(e)) from None
        store.sessions_end_for(user.id)  # whoever had the old password is signed out
        people.log(now(), user.username, user.role, "Chose a new password from an emailed link", client=client_of(request))
        return {"ok": True}

    # ---- first run: what is set up, and sample data to try the app with ------------------------------------------------------

    @app.get("/api/setup", dependencies=auth)
    def setup_status() -> dict:
        """A short list of what a new installation still needs, worked out from what is stored. Nothing here is a setting: it only reads."""
        cur = appsettings.current(env, store.get_settings())
        runs = store.list_runs(1)
        sample_rows = store.count_portal("sample")
        steps = [
            {"key": "profile", "title": "Describe your company", "why": "The AI assistant uses it to score tenders for fit and to write letters.", "done": len(profile().strip()) >= 20, "href": "#/vault", "where": "Vault > Company"},
            {"key": "facts", "title": "Add your company facts", "why": "Turnover, registration class and similar figures are checked against each tender's eligibility.", "done": len(company_facts()) >= 3, "href": "#/vault", "where": "Vault > Company"},
            {"key": "vault", "title": "Add your standard papers to the Vault", "why": "GST, PAN, registration, balance sheets: bids match their checklist to these.", "done": len(bids.vault_list()) >= 3, "href": "#/vault", "where": "Vault > Documents"},
            {"key": "email", "title": "Choose who gets the daily email", "why": "New civil tenders arrive in the inbox each morning.", "done": bool(manager.mail_configured and cur["recipients"]), "href": "#/settings", "where": "Settings > Daily email", "admin": True},
            {"key": "run", "title": "Collect tenders", "why": "Press Run now on the Runs page, or wait for the daily run.", "done": store.count() - sample_rows > 0 or bool(runs and runs[0]["status"] == "ok"), "href": "#/runs", "where": "Runs"},
            {"key": "bid", "title": "Start your first bid", "why": "Open a tender and press Start bid to get the checklist, price tools and team tools.", "done": len([b for b in bids.list() if b["portal"] != "sample"]) > 0, "href": "#/tenders", "where": "Tenders"},
            {"key": "catalogue", "title": "Load your item catalogue (optional)", "why": "Lets a BOQ be costed from your own rates.", "done": (boq.catalogue(False)["counts"].get("item_master", 0) or 0) > 0, "href": "#/vault", "where": "Vault > Item catalogue", "optional": True},
        ]
        return {"steps": steps, "done": sum(1 for s in steps if s["done"]), "total": len(steps), "sample": {"loaded": sample_rows > 0, "tenders": sample_rows}}

    @app.post("/api/sample-data", dependencies=auth)
    def load_sample_data() -> dict:
        """Adds a few fake tenders so every page has something to show. Never emailed, never sent to the AI."""
        added = 0
        for t in sample.tenders(now()):
            if not store.get_tender(t.key):
                store.keep(t, now())
                store.mark_emailed([t.key])
                added += 1
        return {"added": added, "tenders": store.count_portal("sample")}

    @app.delete("/api/sample-data", dependencies=auth)
    def remove_sample_data() -> dict:
        """Removes the sample tenders and any bids (with their checklists, comments and BOQs) started on them."""
        removed_bids = 0
        for b in bids.list():
            if b["portal"] == "sample":
                boq.store.delete_boqs(b["key"])
                bids.delete_source(b["key"])
                removed_bids += bool(bids.delete(b["id"]))
        keys = store.delete_portal("sample")
        return {"tenders": len(keys), "bids": removed_bids}

    # ---- what is kept about a person: a copy, and erasure ---------------------------------------------------------------------

    def download(data: dict, name: str) -> Response:
        return Response(json.dumps(data, indent=2, ensure_ascii=False, default=str), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}.json"'})

    @app.get("/api/account/export", dependencies=auth)
    def export_own_data(request: Request):
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login is set by the server's variables and has no stored account")
        return download(privacy.export_person(people, bids, user.id), f"my-tendai-data-{user.username}")

    @app.get("/api/users/{user_id}/export", dependencies=auth)
    def export_person_data(user_id: int):
        if user_id == accounts.OWNER_ID:
            raise HTTPException(422, "The owner login is set by the server's variables and has no stored account")
        try:
            data = privacy.export_person(people, bids, user_id)
        except privacy.PrivacyError as e:
            raise HTTPException(404, str(e)) from None
        return download(data, f"tendai-data-{data['account']['username']}")

    @app.post("/api/users/{user_id}/erase", dependencies=auth)
    def erase_person_data(user_id: int, body: EraseBody, request: Request) -> dict:
        """Removes who a person was (name, email, password, sign-ins, and their name on comments and the activity log). The person must be deactivated first."""
        me: Principal = request.state.user
        if user_id == accounts.OWNER_ID or user_id == me.id:
            raise HTTPException(422, "You cannot erase your own account or the owner login")
        with people.lock:
            row = people.db.execute("SELECT username, active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No such person")
        if row["active"]:
            raise HTTPException(422, "Turn the account off first (Active off), then erase it")
        if body.confirm.strip().lower() != row["username"]:
            raise HTTPException(422, "Type the person's username exactly to confirm")
        try:
            out = privacy.erase_person(people, bids, store, user_id, now())
        except privacy.PrivacyError as e:
            raise HTTPException(422, str(e)) from None
        return out

    # ---- integrations: API tokens and outgoing webhooks (administrators) -----------------------------------------------------

    @app.get("/api/tokens", dependencies=auth)
    def list_tokens() -> dict:
        return {"tokens": api_tokens.list(), "roles": list(tokens.ROLES)}

    @app.post("/api/tokens", dependencies=auth)
    def create_token(body: TokenBody, request: Request) -> dict:
        try:
            record, raw = api_tokens.create(body.name, body.role, body.days, request.state.user.username, now())
        except tokens.TokenError as e:
            raise HTTPException(422, str(e)) from None
        return {"token": record, "secret": raw}

    @app.delete("/api/tokens/{token_id}", dependencies=auth)
    def revoke_token(token_id: int) -> dict:
        if not api_tokens.revoke(token_id):
            raise HTTPException(404, "No such active token")
        return {"revoked": token_id}

    def hook_error(e: events.HookError) -> HTTPException:
        return HTTPException(404 if "No such" in str(e) else 422, str(e))

    @app.get("/api/webhooks", dependencies=auth)
    def list_webhooks() -> dict:
        return {"webhooks": hooks.list(), "events": [{"key": k, "label": v} for k, v in events.EVENTS.items() if k != "test"]}

    @app.post("/api/webhooks", dependencies=auth)
    def create_webhook(body: HookBody) -> dict:
        try:
            hook, secret = hooks.create(body.name or "", body.url or "", body.events or [], now())
        except events.HookError as e:
            raise hook_error(e) from None
        return {"webhook": hook, "secret": secret}

    @app.patch("/api/webhooks/{hook_id}", dependencies=auth)
    def edit_webhook(hook_id: int, body: HookBody) -> dict:
        try:
            return hooks.update(hook_id, name=body.name, url=body.url, events=body.events, active=body.active)
        except events.HookError as e:
            raise hook_error(e) from None

    @app.post("/api/webhooks/{hook_id}/secret", dependencies=auth)
    def new_webhook_secret(hook_id: int) -> dict:
        try:
            return {"secret": hooks.new_secret(hook_id)}
        except events.HookError as e:
            raise hook_error(e) from None

    @app.post("/api/webhooks/{hook_id}/test", dependencies=auth)
    def test_webhook_hook(hook_id: int) -> dict:
        """Sends a test message to this webhook right now and says how it went."""
        if not hooks.get(hook_id):
            raise HTTPException(404, "No such webhook")
        hooks.emit("test", {"message": "This is a test from TendAI."}, now(), only_hook=hook_id)
        hooks.deliver(now(), timeout=8)
        state = hooks.get(hook_id) or {}
        if state.get("last_status") != "ok":
            raise HTTPException(502, state.get("last_error") or "The test message could not be delivered")
        return {"ok": True, "webhook": state}

    @app.delete("/api/webhooks/{hook_id}", dependencies=auth)
    def delete_webhook(hook_id: int) -> dict:
        if not hooks.delete(hook_id):
            raise HTTPException(404, "No such webhook")
        return {"removed": hook_id}

    @app.get("/api/users", dependencies=auth)
    def list_users() -> dict:
        owner_row = {"id": accounts.OWNER_ID, "username": owner.username, "name": "Owner (server login)", "role": "admin", "active": True, "must_change": False,
                     "created": None, "last_login": None, "owner": True}
        return {"users": [owner_row, *({**u, "owner": False} for u in people.list())],
                "roles": [{"key": r, "label": r.capitalize(), "text": accounts.ROLE_TEXT[r]} for r in accounts.ROLES], "min_password": accounts.MIN_PASSWORD}

    @app.post("/api/users", dependencies=auth)
    def create_user(body: NewUser) -> dict:
        try:
            return people.create(body.username, body.name, body.role, body.password, now(), reserved=config.username, email=body.email)
        except AccountError as e:
            raise HTTPException(422, str(e)) from None

    @app.patch("/api/users/{user_id}", dependencies=auth)
    def edit_user(user_id: int, body: UserEdit, request: Request) -> dict:
        me: Principal = request.state.user
        if user_id == accounts.OWNER_ID:
            raise HTTPException(422, "The owner login is set by the server's variables and cannot be changed here")
        if user_id == me.id and (body.active is False or (body.role is not None and body.role != me.role)):
            raise HTTPException(422, "You cannot remove your own access or change your own role. Ask another administrator")
        try:
            out = people.update(user_id, name=body.name, role=body.role, active=body.active, password=body.password, reset_two_step=body.reset_2fa, email=body.email)
        except AccountError as e:
            raise HTTPException(404 if "No such" in str(e) else 422, str(e)) from None
        if body.active is False or body.password is not None:
            store.sessions_end_for(user_id)  # signed out everywhere, now
        return out

    @app.get("/api/audit", dependencies=auth)
    def activity_log(limit: int = Query(100, ge=1, le=500), user: str = Query("", max_length=80), before: int | None = Query(None, ge=1)) -> dict:
        rows = people.activity(limit, user, before)
        return {"entries": rows, "next": rows[-1]["id"] if len(rows) == limit else None}

    # ---- backup ----------------------------------------------------------------------------------------------

    @app.get("/api/backup/status", dependencies=auth)
    def backup_status() -> dict:
        return {**backups.status(), "storage_warning": storage_note,
                "encryption": sealing.describe(sealer, encryption_problem, bids.plain_files() if sealer.enabled else 0, people.plain_secrets() if sealer.enabled else 0)}

    @app.post("/api/backup/offsite-now", dependencies=auth)
    def backup_offsite_now():
        """Sends a full backup to the off-site bucket now, instead of waiting for tonight."""
        if backups.remote is None:
            raise HTTPException(422, backups.remote_problem or "Off-site backup is not set up. See the README (BACKUP_S3_BUCKET and the other BACKUP_S3_ variables)")
        def work() -> dict:
            state = backups.upload_offsite(now(), force=True)
            if state.get("last_error"):
                raise HTTPException(502, state["last_error"])
            return {"ok": True, **backups.status()["offsite"]}
        return run_job("offsite", "now", work)

    @app.get("/api/backup", dependencies=auth)
    def backup_download():
        """Everything the app keeps (both databases and the vault files) as one zip, to save somewhere safe."""
        try:
            path = backups.build_zip(now())
        except BackupError as e:
            raise HTTPException(409, str(e)) from None
        return FileResponse(path, media_type="application/zip", filename=f"tendai-backup-{now().strftime('%Y-%m-%d')}.zip", background=BackgroundTask(path.unlink, missing_ok=True))

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

    @app.get("/api/portals/{key}/sample", dependencies=auth)
    def portal_sample(key: str):
        """What the portal actually returns to this server, saved as a file. For fixing or writing a reader for a portal the app cannot read yet."""
        portal = next((p for p in portals_now() if p.key == key), None)
        if not portal:
            raise HTTPException(404, "No such portal")
        client = manager.client_factory(portal, env)
        fetch = getattr(client, "sample", None)
        if fetch is None:
            raise HTTPException(422, "This portal's reader cannot make a sample")
        try:
            ctype, body = fetch()
        except PortalError as e:
            raise HTTPException(502, f"The portal could not be read: {e}") from None
        ext = "json" if "json" in ctype else "html"
        return Response(body, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="sample-{re.sub(r"[^a-z0-9-]", "", key)}.{ext}"'})

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

    @app.post("/api/settings/test-webhook", dependencies=auth)
    def test_webhook(body: WebhookTest) -> dict:
        url = (body.url or "").strip() or appsettings.current(env, store.get_settings())["webhook_url"]
        if not url:
            raise HTTPException(422, "Type the webhook address first")
        try:
            reminders.post_webhook(url, "TendAI test: deadline reminders will arrive here.")
        except reminders.WebhookError as e:
            raise HTTPException(502, str(e)) from None
        return {"ok": True}

    # ---- calendar feed: a private address that calendar apps subscribe to --------------------------------------------

    def public_base(request: Request) -> str:
        if config.app_url:
            return config.app_url
        proto = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip() or request.url.scheme
        return f"{proto}://{request.headers.get('host', 'localhost')}"

    def calendar_state(request: Request) -> dict:
        token = store.get_settings().get("calendar_token")
        return {"enabled": bool(token), "url": f"{public_base(request)}/api/calendar/{token}.ics" if token else ""}

    @app.get("/api/calendar", dependencies=auth)
    def get_calendar(request: Request) -> dict:
        return calendar_state(request)

    @app.post("/api/calendar/token", dependencies=auth)
    def new_calendar_token(request: Request) -> dict:
        """Makes (or replaces) the secret address. Replacing it stops every calendar that used the old one."""
        store.put_settings({"calendar_token": secrets.token_urlsafe(24)})
        return calendar_state(request)

    @app.delete("/api/calendar", dependencies=auth)
    def stop_calendar(request: Request) -> dict:
        store.put_settings({"calendar_token": None})
        return calendar_state(request)

    @app.get("/api/calendar/{token}.ics")
    def calendar_feed(token: str, request: Request) -> Response:
        # No login: calendar apps cannot sign in. The long random address is the secret, and the feed holds deadlines only.
        have = store.get_settings().get("calendar_token")
        if not have or not hmac.compare_digest(token.encode(), str(have).encode()):
            raise HTTPException(404, "Not found")
        return Response(reminders.build_ics(bids.list(), bids.vault_list(), now(), config.tz_name, config.app_url or public_base(request)),
                        media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": 'inline; filename="tendai-deadlines.ics"'})

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
