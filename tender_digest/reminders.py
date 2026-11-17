"""Deadline reminders, a webhook for chat apps, and a calendar feed.

A bid has dates that cost money to miss: when it closes, when the EMD is due, the pre-bid meeting, the last date for queries, when bids
open. The daily email lists them, but a deadline that falls tomorrow evening should not wait for tomorrow morning. This module decides
which reminders are due (each threshold fires once, never twice), builds the messages, posts them to a webhook (Slack, Teams, a
WhatsApp or SMS gateway: anything that accepts a JSON POST) and builds a calendar feed people subscribe to in Google Calendar,
Outlook or an iPhone.
"""
from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from .bids import PREPARING, _when
from .gepnic import is_public_host

# Hours before a deadline at which a reminder is sent. The closing date gets one more, late, nudge.
OFFSETS_HOURS: dict[str, tuple[int, ...]] = {"closing": (72, 24, 6), "emd": (72, 24), "prebid": (72, 24), "query": (72, 24), "opening": (24,)}
LABELS = {"closing": "Bid closes", "emd": "EMD due", "prebid": "Pre-bid meeting", "query": "Last date for queries", "opening": "Bids open"}
QUIET_FROM, QUIET_TO = 22, 7  # no reminders overnight, except the final one before a closing time


class WebhookError(Exception):
    pass


# ---- which reminders are due -------------------------------------------------------------------------------------------

def deadlines(bids: list[dict[str, Any]], now: datetime, future_only: bool = True) -> list[dict[str, Any]]:
    """The dates that matter on bids in progress (and the opening of submitted ones). A date without a time counts as the end of that day."""
    out = []
    for b in bids:
        if b["stage"] in PREPARING:
            fields: tuple[tuple[str, Any], ...] = (("closing", b.get("closing")), ("emd", b.get("emd_due")), ("prebid", b.get("prebid_date")), ("query", b.get("query_deadline")))
        elif b["stage"] == "submitted":
            fields = (("opening", b.get("opening")),)
        else:
            continue
        for kind, raw in fields:
            when = _when(raw)
            if when is None or (future_only and when <= now):
                continue
            out.append({"bid_id": b["id"], "kind": kind, "label": LABELS[kind], "when": when, "all_day": bool(raw) and len(str(raw)) == 10,
                        "title": (b.get("description") or b.get("title") or "")[:140], "tender_id": b.get("tender_id") or "", "stage": b["stage"]})
    return sorted(out, key=lambda d: d["when"])


def is_quiet(now: datetime) -> bool:
    return now.hour >= QUIET_FROM or now.hour < QUIET_TO


def due(items: list[dict[str, Any]], now: datetime, sent: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (reminders to send now, keys to record as handled).

    Each deadline has thresholds (72, 24, 6 hours...). When one is crossed the reminder for the tightest crossed threshold goes out once;
    larger thresholds that were passed without a reminder (a bid started 10 hours before it closes) are recorded as handled and never sent.
    Overnight, only the final reminder before a closing time is sent: the rest wait until morning."""
    fire: list[dict[str, Any]] = []
    handled: list[str] = []
    for d in items:
        hours = (d["when"] - now).total_seconds() / 3600
        if hours <= 0:
            continue
        crossed = [o for o in sorted(OFFSETS_HOURS[d["kind"]]) if hours <= o]
        if not crossed:
            continue

        def key(o: int, d: dict[str, Any] = d) -> str:
            return f"{d['bid_id']}:{d['kind']}:{d['when'].isoformat(timespec='minutes')}:{o}"

        handled += [key(o) for o in crossed[1:] if key(o) not in sent]  # passed without a reminder: never send those late
        tight = crossed[0]
        if key(tight) in sent:
            continue
        final_closing = d["kind"] == "closing" and tight == min(OFFSETS_HOURS["closing"])
        if is_quiet(now) and not final_closing:
            continue
        fire.append({**d, "threshold": tight, "hours_left": hours})
        handled.append(key(tight))
    return fire, handled


# ---- the messages ----------------------------------------------------------------------------------------------------------

def left_text(hours: float) -> str:
    if hours < 1:
        return f"{max(1, int(hours * 60))} min"
    if hours < 48:
        return f"{int(hours)} h"
    return f"{int(hours // 24)} days"


def when_text(d: dict[str, Any]) -> str:
    w = d["when"]
    return w.strftime("%a %d %b %Y") if d.get("all_day") else w.strftime("%a %d %b %Y, %I:%M %p").replace(" 0", " ")


def link(app_url: str, bid_id: int) -> str:
    return f"{app_url.rstrip('/')}/#/bids/{bid_id}" if app_url else ""


def build_message(items: list[dict[str, Any]], app_url: str = "") -> tuple[str, str, str]:
    """(subject, html, plain text) for one batch of due reminders."""
    first = items[0]
    subject = (f"Reminder: {first['label'].lower()} in {left_text(first['hours_left'])}: {first['title'][:60]}" if len(items) == 1
               else f"Reminder: {len(items)} bid deadlines coming up")
    rows, lines = [], []
    for d in items:
        url = link(app_url, d["bid_id"])
        rows.append(f"<tr><td><b>{escape(d['label'])}</b> in {escape(left_text(d['hours_left']))}<div style='color:#667085'>{escape(when_text(d))}</div></td>"
                    f"<td>{escape(d['title'])}<div style='color:#667085'>ID {escape(d['tender_id'])}</div>"
                    + (f"<div><a href='{escape(url)}'>Open the bid</a></div>" if url else "") + "</td></tr>")
        lines.append(f"- {d['label']} in {left_text(d['hours_left'])} ({when_text(d)}): {d['title']} [ID {d['tender_id']}]" + (f"\n  {url}" if url else ""))
    html = ("<html><body style='font-family:Arial,sans-serif;color:#14181f'><h2>Bid deadlines coming up</h2>"
            f"<table cellpadding='8' style='border-collapse:collapse'>{''.join(rows)}</table>"
            "<p style='color:#667085'>Confirm dates on the portal. Sent by TendAI by QMSofts.</p></body></html>")
    text = "Bid deadlines coming up\n\n" + "\n".join(lines) + "\n\nConfirm dates on the portal. Sent by TendAI by QMSofts."
    return subject, html, text


# ---- webhook -------------------------------------------------------------------------------------------------------------------

def check_webhook_url(url: str) -> str:
    """The address, cleaned, or ValueError saying what is wrong. It must be https and name a public host, never an IP number or a private network."""
    url = (url or "").strip()
    if not url:
        return ""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        raise ValueError("Use the web address of the service, not an IP number")
    if p.scheme != "https" or not host or p.username or p.password or len(url) > 500:
        raise ValueError("The webhook address must start with https:// and be a normal web address")
    return url


def post_webhook(url: str, text: str, items: list[dict[str, Any]] | None = None, session: Any = None, timeout: int = 10) -> None:
    """Posts {"text": ...} (what Slack, Teams and most gateways read) plus the structured reminders. Raises WebhookError with a reason."""
    try:
        url = check_webhook_url(url)
    except ValueError as e:
        raise WebhookError(str(e)) from None
    if not url:
        return
    if not is_public_host(urlparse(url).hostname or ""):
        raise WebhookError("That address points to a private network, so it was not contacted")
    payload = {"text": text, "source": "TendAI", "reminders": [{"bid_id": d["bid_id"], "kind": d["kind"], "label": d["label"], "when": d["when"].isoformat(timespec="minutes"),
                                                                "title": d["title"], "tender_id": d["tender_id"]} for d in (items or [])]}
    try:
        r = (session or requests).post(url, json=payload, timeout=timeout, allow_redirects=False)
    except requests.RequestException as e:
        raise WebhookError(f"The webhook could not be reached: {str(e)[:120]}") from None
    if r.status_code >= 300:
        raise WebhookError(f"The webhook answered {r.status_code}")


# ---- calendar feed -------------------------------------------------------------------------------------------------------------

def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 bytes are continued on the next line, as the calendar standard requires."""
    out: list[str] = []
    cur = ""
    for ch in line:
        if len((cur + ch).encode()) > (75 if not out else 74):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def build_ics(bids: list[dict[str, Any]], vault_docs: list[dict[str, Any]], now: datetime, tz_name: str = "Asia/Kolkata", app_url: str = "") -> str:
    """An iCalendar feed of every deadline on bids in progress (from a week ago on) and of Vault papers that expire."""
    tz = ZoneInfo(tz_name)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//TendAI by QMSofts//Deadlines//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:TendAI deadlines",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]

    def event(uid: str, when: datetime, all_day: bool, summary: str, description: str, url: str = "") -> None:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}@tendai")
        lines.append(f"DTSTAMP:{stamp}")
        if all_day:
            lines.append(f"DTSTART;VALUE=DATE:{when.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{(when + timedelta(days=1)).strftime('%Y%m%d')}")
        else:
            start = when.replace(tzinfo=tz).astimezone(UTC)
            lines.append(f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}")
            lines.append(f"DTEND:{(start + timedelta(minutes=30)).strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"SUMMARY:{_esc(summary)}")
        lines.append(f"DESCRIPTION:{_esc(description)}")
        if url:
            lines.append(f"URL:{url}")
        lines.extend(["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(summary)}", "TRIGGER:-P1D", "END:VALARM", "END:VEVENT"])

    for d in deadlines(bids, now - timedelta(days=7), future_only=True):
        event(f"bid-{d['bid_id']}-{d['kind']}", d["when"], d["all_day"], f"{d['label']}: {d['title'][:80]}", f"Tender ID {d['tender_id']}. Confirm the date on the portal.", link(app_url, d["bid_id"]))
    for doc in vault_docs:
        when = _when(doc.get("expiry"))
        if when and when >= now - timedelta(days=7):
            event(f"vault-{doc['id']}", when, True, f"Vault paper expires: {doc['name'][:80]}", "Renew it before you submit bids that need it.")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
