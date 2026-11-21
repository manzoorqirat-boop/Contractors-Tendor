"""What the route modules share with web.py: the shapes of what is sent to the API (request bodies), a few limits, and small helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import letters
from .store import SORTS, SeenStore

log = logging.getLogger("tender_digest.web")


COOKIE = "td_session"


SESSION_SECONDS = 7 * 24 * 3600


MAX_EXPORT_ROWS = 20_000


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


def real_date(value: str) -> bool:
    """Looks like 2026-11-20 (optionally with a time) AND is a date that exists: 2026-13-45 is not."""
    if not DATE_RE.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


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


def storage_ok(store: SeenStore, state_path: str) -> bool:
    """Can the database be read, and is its folder writable?"""
    try:
        store.ping()
    except Exception:
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


class SourceBody(BaseModel):
    text: str = ""
    pdf_base64: str = ""


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
