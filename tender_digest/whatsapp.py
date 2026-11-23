"""A WhatsApp assistant: ask the app a question from your phone and get the answer back in the chat.

This speaks Meta's WhatsApp Business Cloud API. Meta calls the app's webhook for every message sent to your business number; the app checks the message is really from
Meta (an HMAC signature made with your app secret), checks the sender is a number you have listed, works out what was asked, and replies through Meta's API. Nothing is sent
to a number that is not on the list, and nothing is ever done because of a message: it only reads (what closes soon, your bids and their dates, new tenders, a search, the cash
locked in EMDs). Cash figures need a number listed as bidder or administrator, like the same figures in the app.

Setting it up is in the README. NOTE for whoever deploys it: this has been tested against Meta's documented message formats and signature rules with stand-ins, not against
a live WhatsApp account. Try it with your own number first.

Environment:
  WHATSAPP_VERIFY_TOKEN   a phrase you choose; Meta sends it when you register the webhook
  WHATSAPP_APP_SECRET     the app secret (App settings > Basic): messages are accepted only if signed with it
  WHATSAPP_ACCESS_TOKEN   a permanent access token for sending replies
  WHATSAPP_PHONE_NUMBER_ID  the id of the business number that receives messages
  WHATSAPP_ALLOWED        who may ask: "919876543210:bidder,919812345678:viewer" (country code first, no +). A number not listed gets no reply at all.
  WHATSAPP_API_VERSION    optional, default v21.0
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

from .accounts import RANK, ROLES

log = logging.getLogger("tender_digest.whatsapp")

POST = requests.post            # replaced in tests: nothing in tests ever reaches Meta
MAX_REPLY = 1500                # characters; WhatsApp allows more, a phone screen does not want it
RATE_LIMIT = (20, 600)          # at most 20 messages from one number in 10 minutes
SEEN_IDS = 500


@dataclass
class Config:
    verify_token: str = ""
    app_secret: str = ""
    access_token: str = ""
    phone_number_id: str = ""
    allowed: dict[str, str] = field(default_factory=dict)       # digits -> role
    version: str = "v21.0"
    problems: list[str] = field(default_factory=list)           # entries of WHATSAPP_ALLOWED that were ignored or look wrong, said without showing the whole number

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        allowed: dict[str, str] = {}
        problems: list[str] = []
        for part in re.split(r"[,;\n]+", e.get("WHATSAPP_ALLOWED", "")):
            if not part.strip():
                continue
            number, _, role = part.strip().partition(":")
            role = role.strip()
            digits = re.sub(r"\D", "", number)
            who = f"…{digits[-4:]}" if len(digits) >= 4 else "an entry with no phone number"
            if not digits:
                problems.append(f"Ignored “{part.strip()[:20]}”: it has no phone number in it")
            elif (role or "viewer") not in ROLES:
                problems.append(f"Ignored {who}: the role “{role[:20]}” is not viewer, bidder or admin")
            else:
                allowed[digits] = role or "viewer"
                if len(digits) == 10 and digits[0] in "6789":
                    problems.append(f"{who} has 10 digits: WhatsApp sends the country code too (for India, 91 first), so this number will probably never match")
                elif not 8 <= len(digits) <= 15:
                    problems.append(f"{who} does not look like a full phone number (8 to 15 digits with the country code)")
        version = e.get("WHATSAPP_API_VERSION", "").strip() or "v21.0"
        return cls(e.get("WHATSAPP_VERIFY_TOKEN", "").strip(), e.get("WHATSAPP_APP_SECRET", "").strip(), e.get("WHATSAPP_ACCESS_TOKEN", "").strip(),
                   re.sub(r"\D", "", e.get("WHATSAPP_PHONE_NUMBER_ID", "")), allowed, version if re.fullmatch(r"v\d+\.\d+", version) else "v21.0", problems)

    def missing(self) -> list[str]:
        need = {"WHATSAPP_VERIFY_TOKEN": self.verify_token, "WHATSAPP_APP_SECRET": self.app_secret, "WHATSAPP_ACCESS_TOKEN": self.access_token,
                "WHATSAPP_PHONE_NUMBER_ID": self.phone_number_id, "WHATSAPP_ALLOWED": self.allowed}
        return [k for k, v in need.items() if not v]

    @property
    def enabled(self) -> bool:
        return not self.missing()


def verify_signature(secret: str, raw: bytes, header: str | None) -> bool:
    """Is this body really from Meta? `header` is X-Hub-Signature-256: sha256=<hex HMAC of the raw body with the app secret>."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:].strip().lower())


@dataclass
class Incoming:
    id: str
    sender: str
    text: str


def parse_incoming(payload: Any, phone_number_id: str) -> list[Incoming]:
    """The text messages in a webhook call that were sent to our number. Delivery receipts, images, voice notes and everything else are ignored."""
    out: list[Incoming] = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            if str((value.get("metadata") or {}).get("phone_number_id", "")) != phone_number_id:
                continue
            for m in value.get("messages") or []:
                if isinstance(m, dict) and m.get("type") == "text" and isinstance(m.get("text"), dict) and m.get("id") and m.get("from"):
                    out.append(Incoming(str(m["id"])[:120], re.sub(r"\D", "", str(m["from"])), str(m["text"].get("body", ""))[:1000]))
    return out


class Guard:
    """Drops repeats (Meta resends a message it thinks was not delivered) and a number that is sending too fast."""

    def __init__(self, limit: tuple[int, int] = RATE_LIMIT, clock: Any = time.monotonic):
        self.limit, self.clock = limit, clock
        self.ids: deque[str] = deque(maxlen=SEEN_IDS)
        self.times: dict[str, deque[float]] = {}

    def allow(self, message_id: str, sender: str) -> bool:
        if message_id in self.ids:
            return False
        self.ids.append(message_id)
        now = self.clock()
        window = self.times.setdefault(sender, deque())
        while window and now - window[0] > self.limit[1]:
            window.popleft()
        if len(window) >= self.limit[0]:
            return False
        window.append(now)
        return True


def send_text(cfg: Config, to: str, body: str, post: Any = None) -> bool:
    """Sends one reply through Meta's API. Never raises: a failed reply is logged and the webhook still answers Meta normally."""
    url = f"https://graph.facebook.com/{cfg.version}/{cfg.phone_number_id}/messages"
    try:
        r = (post or POST)(url, json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:3500], "preview_url": False}},
                           headers={"Authorization": f"Bearer {cfg.access_token}"}, timeout=10, allow_redirects=False)
        if getattr(r, "status_code", 500) >= 300:
            log.warning("WhatsApp send refused: HTTP %s", getattr(r, "status_code", "?"))
            return False
        return True
    except Exception as e:  # network trouble, a bad token: logged, not raised
        log.warning("WhatsApp send failed: %s", type(e).__name__)
        return False


# ---- what the assistant can answer ----------------------------------------------------------------------------------------------

HELP = ("TendAI assistant. Send one of:\n"
        "closing - tenders closing in 3 days\n"
        "new - tenders found since yesterday\n"
        "bids - your bids in progress\n"
        "deadlines - what is coming up on your bids\n"
        "find <words> - search tenders, e.g. find drain nashik\n"
        "bid <tender id> - how one bid stands\n"
        "cash - money locked in EMDs (bidders and admins)")


def _day(s: Any) -> str:
    try:
        return datetime.fromisoformat(str(s)).strftime("%d %b")
    except ValueError:
        return "-"


def _clip(text: Any, n: int) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _rupees(v: Any) -> str:
    """₹2.50 crore, ₹25.00 lakh, ₹4,25,000: short enough for a phone."""
    from .clauses import _indian
    n = float(v or 0)
    return f"₹{n / 1e7:.2f} crore" if n >= 1e7 else f"₹{n / 1e5:.2f} lakh" if n >= 1e5 else f"₹{_indian(int(n))}"


def _inr(v: Any) -> str:
    return _rupees(v) if v else "value not stated"


def fit_message(lines: list[str], head: str, total: int | None = None) -> str:
    """The heading and as many lines as fit in MAX_REPLY, with a count of what did not."""
    out, size = [head], len(head)
    for i, ln in enumerate(lines):
        if size + len(ln) + 1 > MAX_REPLY - 40:
            out.append(f"…and {(total if total is not None else len(lines)) - i} more. Open the app for the rest.")
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


def tender_line(t: dict[str, Any]) -> str:
    return f"• {_clip(t.get('description') or t.get('title'), 70)}\n  ID {t.get('tender_id') or '-'} | closes {_day(t.get('closing'))} | {_inr(t.get('value'))}"


class Assistant:
    """Answers one question. `store`, `bids` and `money` are the app's own; `ai_filters(question)` (optional) turns free text into tender filters."""

    def __init__(self, store: Any, bids: Any, money: Any, now: Any, ai_filters: Any = None):
        self.store, self.bids, self.money, self.now, self.ai_filters = store, bids, money, now, ai_filters

    def reply(self, text: str, role: str) -> str:
        said = re.sub(r"\s+", " ", (text or "").strip())
        low = said.lower().strip("!.? ")
        if not said or not low:
            return HELP
        word, _, rest = low.partition(" ")
        rest = said.partition(" ")[2].strip()              # what follows the first word, in the sender's own letters (a tender ID is echoed back as written)
        if low in ("help", "hi", "hello", "menu", "start", "?") or word in ("help", "menu"):
            return HELP
        if word in ("closing", "today", "urgent", "due") or low in ("closing soon", "what closes soon", "closes soon"):
            return self.closing()
        if word == "new" or low in ("what is new", "whats new", "what's new"):
            return self.new()
        if low in ("bids", "my bids", "bid", "pipeline"):
            return self.my_bids()
        if word in ("deadlines", "alerts", "next", "calendar") or low in ("coming up", "what is coming up"):
            return self.deadlines()
        if word in ("cash", "money", "emd", "emds"):
            return self.cash(role)
        if word in ("find", "search", "tenders") and rest:
            return self.find(rest)
        if word in ("bid", "status") and rest:
            return self.bid_status(rest)
        return self.free_text(said)

    def closing(self) -> str:
        now = self.now()
        rows = sorted(self.store.closing_soon(now, 3), key=lambda t: t.closing or now)
        if not rows:
            return "Nothing is closing in the next 3 days."
        lines = [tender_line({"description": t.description, "title": t.title, "tender_id": t.tender_id, "closing": t.closing.isoformat() if t.closing else None, "value": t.value}) for t in rows]
        return fit_message(lines, f"{len(rows)} closing in 3 days:", len(rows))

    def new(self) -> str:
        now = self.now()
        rows, total = self.store.search(now, {"status": "open", "seen_from": (now - timedelta(days=1)).date().isoformat()}, "value", 8)
        if not total:
            return "No new tenders since yesterday."
        return fit_message([tender_line(t) for t in rows], f"{total} new since yesterday (largest first):", total)

    def my_bids(self) -> str:
        live = [b for b in self.bids.list() if b["stage"] in ("interested", "preparing", "submitted", "opened")]
        if not live:
            return "You have no bids in progress."
        lines = [f"• {_clip(b.get('description') or b.get('title'), 60)}\n  {b['stage']} | closes {_day(b.get('closing'))} | ID {b.get('tender_id') or '-'}" for b in live]
        return fit_message(lines, f"{len(live)} bid{'s' if len(live) != 1 else ''} in progress:", len(live))

    def deadlines(self) -> str:
        alerts = [a for a in self.bids.alerts(self.now(), 7) if a.get("urgency") != "later" or a.get("days_left", 9) <= 3]
        if not alerts:
            return "Nothing is due on your bids in the next few days."
        lines = [f"• {a['label']}{' - ' + _clip(a['title'], 50) if a.get('title') else ''}\n  {_day(a['date'])} ({'overdue' if a.get('urgency') == 'overdue' else 'today' if a.get('days_left') == 0 else 'in ' + str(a.get('days_left')) + ' d'})" for a in alerts[:10]]
        return fit_message(lines, "Coming up on your bids:", len(alerts))

    def cash(self, role: str) -> str:
        if RANK.get(role, 0) < RANK["bidder"]:
            return "Cash figures are for bidders and administrators. Ask for your number to be listed with that role."
        s = self.money.summary(self.now())
        t = s["totals"]
        parts = [f"Cash locked in EMDs and deposits: {_rupees(t.get('cash_locked'))}", f"Bank guarantees in use: {_rupees(t.get('guarantees'))}",
                 f"Refunds due in 30 days: {_rupees(s.get('refunds_due_30_days'))}"]
        if s.get("refunds_overdue"):
            parts.append(f"Refunds overdue: {_rupees(s['refunds_overdue'])}")
        return "\n".join(parts)

    def find(self, words: str) -> str:
        rows, total = self.store.search(self.now(), {"q": words[:100], "status": "open"}, "relevance", 6)
        if not total:
            return f"No open tenders match “{_clip(words, 40)}”."
        return fit_message([tender_line(t) for t in rows], f"{total} open tender{'s' if total != 1 else ''} match “{_clip(words, 40)}”:", total)

    def bid_status(self, ident: str) -> str:
        want = re.sub(r"\s+", "", ident).lower()
        bid = next((b for b in self.bids.list() if re.sub(r"\s+", "", b.get("tender_id") or "").lower() == want), None)
        if not bid:
            return f"No bid on tender ID {_clip(ident, 40)}. Send bids to see yours."
        done, total = bid.get("items_done", 0), bid.get("items_total", 0)
        lines = [_clip(bid.get("description") or bid.get("title"), 80), f"Stage: {bid['stage']}", f"Closes: {_day(bid.get('closing'))}  Opens: {_day(bid.get('opening'))}",
                 f"Value: {_inr(bid.get('value'))}", f"Checklist: {done} of {total} done"]
        if bid.get("emd_due"):
            lines.append(f"EMD due: {_day(bid['emd_due'])}")
        return "\n".join(lines)

    def free_text(self, said: str) -> str:
        if not self.ai_filters:
            return "I did not understand that. Send help to see what I can do."
        try:
            got = self.ai_filters(said)
        except Exception:
            return "I could not work that out just now. Send help to see what I can do."
        filters = got.get("filters") or {}
        if not filters:
            return "I did not understand that. Send help to see what I can do."
        filters.setdefault("status", "open")
        rows, total = self.store.search(self.now(), filters, got.get("sort") or "closing", 6)
        said_what = got.get("explanation") or "Your question"
        if not total:
            return f"{said_what}: nothing found."
        return fit_message([tender_line(t) for t in rows], f"{said_what}: {total} found.", total)
