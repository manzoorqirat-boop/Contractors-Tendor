"""Is each portal still being read properly? Tells a portal that has quietly stopped working from one that is merely having a bad day.

A portal can fail in ways nobody sees: the site moves, starts refusing this server, or changes its layout so that the reader finds nothing and reports
"0 new tenders" every day. Without this, that looks exactly like a quiet week. Each read of a portal (from a daily run or a manual check) is kept in a short
history, and this module reads it:

  broken     it worked before, and the last BROKEN_AFTER reads in a row failed (not "needs a captcha from the start": that is the check-by-hand list)
  dried_up   it answers, but the last DRIED_UP_AFTER reads found (almost) nothing, though it usually lists plenty: the site's layout has probably changed
  stale      no successful read for STALE_HOURS: the daily run is not reaching it at all
  ok         reading properly (one failed read so far is noted, but it is not yet an alarm)
  manual     has never worked and needs a person (captcha, login, OTP): shown on the check-by-hand list, not here
  unknown    not read yet
  off        switched off
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from . import access

BROKEN_AFTER = 2          # failed reads in a row, after it had worked, before it is called broken
DRIED_UP_AFTER = 2        # reads in a row that found (almost) nothing, before it is called dried up
USUAL_MIN = 10            # a portal usually lists at least this many tenders before an empty read is suspicious
DRIED_SHARE = 0.2         # "almost nothing" is under this share of what it usually lists
STALE_HOURS = 72          # no read at all for this long
HISTORY_USED = 14         # how many recent reads are looked at
PROBLEMS = ("broken", "dried_up", "stale")


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _day(value: str | None) -> str:
    return (value or "")[:10]


def assess(history: list[dict[str, Any]], now: datetime, enabled: bool = True) -> dict[str, Any]:
    """The health of one portal. `history` is its reads, newest first: {at, source, kind, departments, tenders, ...}."""
    rows = history[:HISTORY_USED]
    base: dict[str, Any] = {"state": "unknown", "text": "Not read yet.", "last_ok": None, "failures_in_a_row": 0, "usual": None}
    if not enabled:
        return {**base, "state": "off", "text": "Switched off."}
    if not rows:
        return base
    ok_rows = [r for r in rows if r.get("kind") == "ok"]
    last_ok = ok_rows[0]["at"] if ok_rows else None
    base["last_ok"] = last_ok

    failures = 0
    for r in rows:
        if r.get("kind") == "ok":
            break
        failures += 1
    base["failures_in_a_row"] = failures

    # 1. failing now
    if failures:
        kind = rows[0].get("kind", "unreachable")
        label = access.describe(kind)["label"]
        if not ok_rows:
            if kind in access.NEEDS_MANUAL and kind != "unreachable":
                return {**base, "state": "manual", "text": f"{label}. It has never been readable by the app: check it by hand."}
            return {**base, "state": "unknown" if failures < BROKEN_AFTER else "manual",
                    "text": f"Could not be read ({label}). It has not worked yet."}
        if failures >= BROKEN_AFTER:
            return {**base, "state": "broken", "text": f"Could not be read in the last {failures} reads ({label}). It last worked on {_day(last_ok)}."}
        return {**base, "state": "ok", "text": f"The last read failed ({label}); it is watched, and flagged if it fails again."}

    # 2. reading, but is it finding anything? Only the daily runs count here: a manual check reports the portal's own department counts, not what a run lists.
    runs = [r for r in rows if r.get("source", "run") == "run" and r.get("kind") == "ok"]
    quiet = 0
    for i, r in enumerate(runs):
        tenders = r.get("tenders") or 0
        baseline = [x.get("tenders") or 0 for x in runs[i + 1:i + 9]]
        usual = median(baseline) if baseline else 0
        if tenders == 0 or (usual >= USUAL_MIN and tenders < usual * DRIED_SHARE):
            quiet += 1
        else:
            break
    healthy = [x.get("tenders") or 0 for x in runs[quiet:quiet + 8]]
    usual = median(healthy) if healthy else 0
    base["usual"] = usual
    if quiet >= DRIED_UP_AFTER and usual >= USUAL_MIN:
        latest = runs[0].get("tenders") or 0
        return {**base, "state": "dried_up",
                "text": f"It answers but listed {'no tenders' if latest == 0 else f'only {latest}'} in the last {quiet} daily reads (usually about {int(usual)}). Its layout has probably changed."}

    # 3. not read lately at all
    newest = _when((runs[0] if runs else rows[0]).get("at"))
    if newest and now - newest > timedelta(hours=STALE_HOURS):
        days = (now - newest).days
        return {**base, "state": "stale", "text": f"No read for {days} days. The daily run is not reaching it."}
    return {**base, "state": "ok", "text": "Reading properly."}


def assess_all(store: Any, portals: list[Any], enabled: set[str], now: datetime) -> list[dict[str, Any]]:
    """Health of every portal: [{key, name, state, text, last_ok, ...}], the ones with a problem first."""
    out = []
    for p in portals:
        h = assess(store.portal_history(p.key, HISTORY_USED), now, p.key in enabled)
        out.append({"key": p.key, "name": p.name, "state": p.state, **{k: v for k, v in h.items() if k != "state"}, "health": h["state"]})
    out.sort(key=lambda h: (h["health"] not in PROBLEMS, h["name"]))
    return out


def problems(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for h in items if h["health"] in PROBLEMS]


def announce(store: Any, items: list[dict[str, Any]], emit: Any, at: datetime) -> list[str]:
    """Tells `emit` (event, data) when a portal turns unhealthy or recovers: once per change, not every hour while it stays that way.
    Returns the keys announced."""
    known = store.portal_health_states()
    told = []
    for h in items:
        before = known.get(h["key"], {}).get("state")
        if h["health"] == before:
            continue
        store.put_portal_health(h["key"], h["health"], at)
        data = {"portal": h["key"], "name": h["name"], "state": h["health"], "text": h["text"], "last_ok": h["last_ok"]}
        if h["health"] in PROBLEMS:
            emit("portal.unhealthy", data)
        elif before in PROBLEMS and h["health"] == "ok":
            emit("portal.recovered", data)
        else:
            continue
        told.append(h["key"])
    return told
