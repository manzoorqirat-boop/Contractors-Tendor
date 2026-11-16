"""Saved searches that email you: a saved view marked "Email me new matches" adds a section to the daily digest listing the tenders
found since the last email that match its filters."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

LIST_KEYS = ("portal", "category", "department", "location", "work_type")
FLAG_KEYS = ("no_value", "no_closing", "no_published", "starred", "fits_me")
FLOAT_KEYS = ("min_value", "max_value", "value_lt")
INT_KEYS = ("min_fit", "closing_within", "since_days")
DATE_KEYS = ("closing_from", "closing_to", "seen_from", "seen_to", "published_from", "published_to")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")
MAX_ALERT_VIEWS = 10


def view_filters(raw: dict[str, Any], soon_days: int = 3) -> dict[str, Any]:
    """A saved view's filters as the store understands them. Anything unrecognised or malformed is dropped, never trusted."""
    f: dict[str, Any] = {"status": "open", "soon_days": soon_days}
    if not isinstance(raw, dict):
        return f
    for k in LIST_KEYS:
        v = raw.get(k)
        vals = [v] if isinstance(v, str) else v if isinstance(v, list) else []
        vals = [str(x)[:200] for x in vals if isinstance(x, (str, int))][:50]
        if vals:
            f[k] = vals
    if isinstance(raw.get("q"), str) and raw["q"].strip():
        f["q"] = raw["q"].strip()[:100]
    if raw.get("status") in ("open", "closing_soon"):
        f["status"] = raw["status"]
    for k in FLOAT_KEYS:
        try:
            n = float(raw[k]) if raw.get(k) not in (None, "") else None
        except (TypeError, ValueError):
            n = None
        if n is not None and 0 <= n < 1e13:
            f[k] = n
    for k in INT_KEYS:
        try:
            n = int(raw[k]) if raw.get(k) not in (None, "") else None
        except (TypeError, ValueError):
            n = None
        if n is not None and 1 <= n <= 365:
            f[k] = n
    for k in FLAG_KEYS:
        if raw.get(k) in (True, "true", "True", 1):
            f[k] = True
    for k in DATE_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and _DATE.match(v):
            try:
                datetime.fromisoformat(v)
                f[k] = v
            except ValueError:
                pass
    return f


def matches(store: Any, now: datetime, views: list[dict[str, Any]], new_keys: list[str], soon_days: int = 3, per_view: int = 8, size: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """For each alert view, the new tenders (`new_keys`) that match it. Views with nothing new are left out."""
    if not new_keys:
        return []
    out = []
    for v in [v for v in views if isinstance(v, dict) and v.get("alert")][:MAX_ALERT_VIEWS]:
        f = {**view_filters(v.get("filters") or {}, soon_days), "keys": list(new_keys)}
        if f.pop("fits_me", False) and size:
            f.update(size)
        rows, total = store.search(now, f, "value", per_view, 0)
        if total:
            out.append({"name": str(v.get("name") or "Saved search")[:60], "count": total,
                        "items": [{"tender_id": r["tender_id"], "title": r["description"] or r["title"], "organisation": (r["organisation"] or "").replace("||", " > "),
                                   "value": r["value"], "closing": r["closing"]} for r in rows]})
    return out
