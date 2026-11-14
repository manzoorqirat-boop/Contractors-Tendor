"""Settings a person can change in the web page. They are saved in the database and override the environment defaults.

Secrets (the Brevo key, the login password) are never part of this: they stay in environment variables.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

from .config import Settings

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
    "extra_keywords": [], "exclude_words": [], "company_profile": "", "ai_digest_intro": True, "ai_hide_noncivil": False,
}


def current(env: Settings, saved: dict[str, Any]) -> dict[str, Any]:
    """The values in force: saved ones win, then the environment, then the defaults."""
    return {
        "recipients": saved.get("recipients", env.recipients),
        "send_time": saved.get("send_time", DEFAULTS["send_time"]),
        "schedule_enabled": saved.get("schedule_enabled", DEFAULTS["schedule_enabled"]),
        "send_empty": saved.get("send_empty", DEFAULTS["send_empty"]),
        "portals": saved.get("portals", [p.key for p in env.portals]),
        "min_value": saved.get("min_value", env.min_value),
        "extra_keywords": saved.get("extra_keywords", DEFAULTS["extra_keywords"]),
        "exclude_words": saved.get("exclude_words", DEFAULTS["exclude_words"]),
        "company_profile": saved.get("company_profile", DEFAULTS["company_profile"]),
        "ai_digest_intro": saved.get("ai_digest_intro", DEFAULTS["ai_digest_intro"]),
        "ai_hide_noncivil": saved.get("ai_hide_noncivil", DEFAULTS["ai_hide_noncivil"]),
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
        portals=[p for p in env.portals if p.key in enabled],
        recipients=cur["recipients"], min_value=float(cur["min_value"]), civil_keywords=keywords, exclude=exclude,
    )
