"""Settings a person can change in the web page. They are saved in the database and override the environment defaults.

Secrets (the Brevo key, the login password) are never part of this: they stay in environment variables.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlparse

from .config import Portal, Settings

_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_ITEMS, MAX_WORD = 100, 60


class SettingsError(ValueError):
    pass


def _words(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise SettingsError(f"{label} must be a list")
    out = []
    for w in value:
        w = str(w).strip()
        if not w:
            continue
        if len(w) > MAX_WORD:
            raise SettingsError(f"{label}: '{w[:20]}...' is too long (max {MAX_WORD} characters)")
        if w.lower() not in (x.lower() for x in out):
            out.append(w)
    if len(out) > MAX_ITEMS:
        raise SettingsError(f"{label}: at most {MAX_ITEMS} entries")
    return out


def allowed_suffixes() -> tuple[str, ...]:
    """Portal addresses must end in one of these. The server fetches whatever is saved, so this keeps it to government sites.

    Add more with the PORTAL_HOST_SUFFIXES variable, e.g. ".gov.in,.nic.in,.nprocure.com".
    """
    raw = os.environ.get("PORTAL_HOST_SUFFIXES", ".gov.in,.nic.in")
    return tuple(x.strip().lower() if x.strip().startswith(".") else "." + x.strip().lower() for x in re.split(r"[,;\s]+", raw) if x.strip())


def clean_portal(raw: Any, taken: set[str]) -> dict[str, str]:
    """Validates a portal typed in the page and returns {key, name, state, base_url}. Raises SettingsError."""
    if not isinstance(raw, dict):
        raise SettingsError("Portal details are missing")
    name = str(raw.get("name") or "").strip()[:80]
    state = str(raw.get("state") or "").strip()[:40] or name
    url = str(raw.get("base_url") or "").strip().rstrip("/")
    if not name:
        raise SettingsError("Give the portal a name")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    suffixes = allowed_suffixes()
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443) or parsed.query or parsed.fragment or not host:
        raise SettingsError("Use the portal's https address, for example https://etenders.kerala.gov.in/nicgep")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:  # SettingsError is a ValueError, so it must be raised outside the try above
        raise SettingsError("Use the portal's web address, not an IP number")
    if not host.endswith(suffixes):
        raise SettingsError(f"Only addresses ending in {', '.join(suffixes)} can be added")
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
    if not key:
        raise SettingsError("Give the portal a name with letters or numbers")
    if key in taken:
        raise SettingsError(f"There is already a portal called '{name}'. Use a different name")
    return {"key": key, "name": name, "state": state, "base_url": url}


def all_portals(env: Settings, saved: dict[str, Any]) -> list[Portal]:
    """Built-in portals from portals.json plus the ones added in the page."""
    seen = {p.key for p in env.portals}
    extra = [Portal(**p) for p in saved.get("custom_portals", []) if isinstance(p, dict) and p.get("key") not in seen]
    return list(env.portals) + extra


def clean(payload: dict[str, Any], known_portals: list[str]) -> dict[str, Any]:
    """Validates a settings update and returns only the fields it contains, cleaned. Raises SettingsError."""
    out: dict[str, Any] = {}
    if "recipients" in payload:
        emails = _words(payload["recipients"], "Recipients")
        bad = [e for e in emails if not _EMAIL.match(e)]
        if bad:
            raise SettingsError(f"Not a valid email address: {bad[0]}")
        out["recipients"] = emails
    if "send_time" in payload:
        if not isinstance(payload["send_time"], str) or not _TIME.match(payload["send_time"]):
            raise SettingsError("Send time must look like 08:00 (24-hour)")
        out["send_time"] = payload["send_time"]
    for flag in ("schedule_enabled", "send_empty"):
        if flag in payload:
            if not isinstance(payload[flag], bool):
                raise SettingsError(f"{flag} must be true or false")
            out[flag] = payload[flag]
    if "reminders_on" in payload:
        if not isinstance(payload["reminders_on"], bool):
            raise SettingsError("reminders_on must be true or false")
        out["reminders_on"] = payload["reminders_on"]
    if "require_signoff" in payload:
        if not isinstance(payload["require_signoff"], bool):
            raise SettingsError("require_signoff must be true or false")
        out["require_signoff"] = payload["require_signoff"]
    for key, label, low, high in (("audit_days", "How long the activity log is kept", 30, 3650), ("tender_days", "How long collected tenders are kept", 30, 3650)):
        if key in payload:
            v = payload[key]
            if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
                raise SettingsError(f"{label} must be a whole number of days from {low} to {high}")
            out[key] = v
    if "webhook_url" in payload:
        from .reminders import check_webhook_url
        try:
            out["webhook_url"] = check_webhook_url(str(payload["webhook_url"] or ""))
        except ValueError as e:
            raise SettingsError(str(e)) from None
    if "portals" in payload:
        portals = _words(payload["portals"], "Portals")
        unknown = [p for p in portals if p not in known_portals]
        if unknown:
            raise SettingsError(f"Unknown portal: {unknown[0]}")
        if not portals:
            raise SettingsError("Turn on at least one portal")
        out["portals"] = portals
    if "min_value" in payload:
        try:
            v = float(payload["min_value"] or 0)
        except (TypeError, ValueError):
            raise SettingsError("Minimum value must be a number") from None
        if v < 0 or v > 1e13:
            raise SettingsError("Minimum value is out of range")
        out["min_value"] = v
    for flag in ("ai_digest_intro", "ai_hide_noncivil"):
        if flag in payload:
            if not isinstance(payload[flag], bool):
                raise SettingsError(f"{flag} must be true or false")
            out[flag] = payload[flag]
    if "logo_text" in payload:
        text = str(payload["logo_text"] or "").strip()
        if not re.fullmatch(r"[^\W_]{1,3}", text):
            raise SettingsError("Logo letters must be 1 to 3 letters or numbers")
        out["logo_text"] = text
    if "company_profile" in payload:
        text = str(payload["company_profile"] or "").strip()
        if len(text) > 2000:
            raise SettingsError("Company profile is too long (max 2000 characters)")
        out["company_profile"] = text
    for key, label in (("extra_keywords", "Extra civil keywords"), ("exclude_words", "Excluded words")):
        if key in payload:
            out[key] = _words(payload[key], label)
    return out


DEFAULTS = {
    "send_time": "08:00", "schedule_enabled": True, "send_empty": True, "min_value": 0.0,
    "reminders_on": True, "require_signoff": False, "webhook_url": "", "extra_keywords": [], "exclude_words": [], "company_profile": "", "logo_text": "QM", "ai_digest_intro": True, "ai_hide_noncivil": False,
    "audit_days": 365, "tender_days": 120,
}


def current(env: Settings, saved: dict[str, Any]) -> dict[str, Any]:
    """The values in force: saved ones win, then the environment, then the defaults."""
    return {
        "recipients": saved.get("recipients", env.recipients),
        "send_time": saved.get("send_time", DEFAULTS["send_time"]),
        "schedule_enabled": saved.get("schedule_enabled", DEFAULTS["schedule_enabled"]),
        "send_empty": saved.get("send_empty", DEFAULTS["send_empty"]),
        "portals": saved.get("portals", [p.key for p in env.portals]),
        "custom_portals": saved.get("custom_portals", []),
        "min_value": saved.get("min_value", env.min_value),
        "extra_keywords": saved.get("extra_keywords", DEFAULTS["extra_keywords"]),
        "exclude_words": saved.get("exclude_words", DEFAULTS["exclude_words"]),
        "company_profile": saved.get("company_profile", DEFAULTS["company_profile"]),
        "logo_text": saved.get("logo_text", DEFAULTS["logo_text"]),
        "ai_digest_intro": saved.get("ai_digest_intro", DEFAULTS["ai_digest_intro"]),
        "ai_hide_noncivil": saved.get("ai_hide_noncivil", DEFAULTS["ai_hide_noncivil"]),
        "reminders_on": saved.get("reminders_on", DEFAULTS["reminders_on"]),
        "require_signoff": saved.get("require_signoff", DEFAULTS["require_signoff"]),
        "webhook_url": saved.get("webhook_url", DEFAULTS["webhook_url"]),
        "audit_days": saved.get("audit_days", DEFAULTS["audit_days"]),
        "tender_days": saved.get("tender_days", DEFAULTS["tender_days"]),
    }


def _alternation(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in words)


def effective(env: Settings, saved: dict[str, Any], only_portals: list[str] | None = None) -> Settings:
    """Environment settings with the saved overrides applied. Words typed in the page are escaped, never used as a regex."""
    cur = current(env, saved)
    enabled = only_portals if only_portals is not None else cur["portals"]
    keywords, exclude = env.civil_keywords, env.exclude
    if cur["extra_keywords"]:
        keywords = f"{keywords}|{_alternation(cur['extra_keywords'])}"
    if cur["exclude_words"]:
        exclude = f"{exclude}|{_alternation(cur['exclude_words'])}" if exclude else _alternation(cur["exclude_words"])
    return dataclasses.replace(
        env,
        portals=[p for p in all_portals(env, saved) if p.key in enabled],
        recipients=cur["recipients"], min_value=float(cur["min_value"]), civil_keywords=keywords, exclude=exclude,
    )
