"""What to expect: how many new civil tenders are likely in the next four weeks, and which tenders come round again about this time each year.

Two things, both from the app's own long memory (`flow`: how many new tenders were found each day per department; `memory`: a short record of every tender, kept about
two years although the tenders themselves are pruned after weeks):

  * **Volume.** Next four weeks = the recent weekly level (the last eight observed weeks, an exponentially weighted average) x four, adjusted for the season when a full year
    of history exists (this time last year against that year's average, pulled halfway to 1 and capped, because one year is thin evidence of a pattern). The range comes
    from how this same method missed in the past, not from a formula. It is tested on its own history: each window predicted from the weeks before it, against simply repeating
    the last four weeks, and shown with that result.
  * **Coming round again.** Tenders published about this time last year (the next ~75 days of last year's calendar) that have no look-alike this season: annual repair
    and maintenance works, yearly contracts, and the like. Matched by department and by the words of the title.

Weeks the app was not collecting (no successful run on at least four of the seven days) are left out, never counted as "no tenders": a server that was off must not look like a quiet
market. A new installation has little or no history, so there is little or nothing to forecast until it has been running a while, and the answer says exactly that.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

WEEK_DAYS_NEEDED = 4        # days with a successful run for a week to count as observed
LEVEL_WEEKS = 8
ALPHA = 0.3                 # weight of the newest week in the level (older weeks fade)
HORIZON = 4                 # weeks ahead
MIN_WEEKS = 6               # observed weeks needed before any forecast
SEASON_WEEKS = 56           # observed weeks of history before the season is used
SEASON_CAP = (0.5, 2.0)
MIN_BACKTEST = 8            # windows tested before the backtest can vouch for the method
RECUR_WINDOW_DAYS = 75
RECUR_SIMILARITY = 0.55
_STOP = {"the", "of", "at", "for", "and", "in", "to", "on", "with", "from", "by", "tender", "work", "works", "construction", "providing", "supply", "job", "name", "under", "near", "road"}


def _day(s: str) -> date:
    return date.fromisoformat(s[:10])


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_counts(flow: list[dict[str, Any]], run_days: set[str], first_day: date, last_complete: date) -> list[tuple[date, int]]:
    """[(week start, new tenders)] for each observed week from the first day the app collected up to the last complete week."""
    by_week: dict[date, int] = {}
    for r in flow:
        by_week[week_start(_day(r["day"]))] = by_week.get(week_start(_day(r["day"])), 0) + int(r["n"])
    out = []
    w = week_start(first_day)
    if w < first_day:
        w += timedelta(days=7)                                   # a partial first week is not a full week of collecting
    while w + timedelta(days=6) <= last_complete:
        days = sum(1 for i in range(7) if (w + timedelta(days=i)).isoformat() in run_days)
        if days >= WEEK_DAYS_NEEDED:
            out.append((w, by_week.get(w, 0)))
        w += timedelta(days=7)
    return out


def level(counts: list[int]) -> float:
    """The recent weekly level: the last LEVEL_WEEKS weeks, newest weighted most."""
    recent = counts[-LEVEL_WEEKS:]
    lv = float(recent[0])
    for c in recent[1:]:
        lv = ALPHA * c + (1 - ALPHA) * lv
    return lv


def season_factor(series: list[tuple[date, int]], target_week: date) -> float:
    """How the four weeks starting a year before `target_week` compared with that year's typical four weeks; 1.0 without a full year of history."""
    if len(series) < SEASON_WEEKS:
        return 1.0
    year_ago = target_week - timedelta(days=364)
    window = [c for w, c in series if year_ago <= w < year_ago + timedelta(days=7 * HORIZON)]
    prior = [c for w, c in series if year_ago - timedelta(days=182) <= w < year_ago + timedelta(days=182 + 7 * HORIZON)]
    if len(window) < HORIZON or len(prior) < 20 or mean(prior) <= 0:
        return 1.0
    raw = mean(window) / mean(prior)
    return max(SEASON_CAP[0], min(SEASON_CAP[1], 1 + 0.5 * (raw - 1)))      # one year of evidence: only half-believed


def predict_next(series: list[tuple[date, int]]) -> float:
    """Expected new tenders in the HORIZON weeks after the series ends."""
    counts = [c for _, c in series]
    target = series[-1][0] + timedelta(days=7)
    return HORIZON * level(counts) * season_factor(series, target)


def backtest(series: list[tuple[date, int]]) -> dict[str, Any]:
    """Each HORIZON-week window predicted from the weeks before it; compared with repeating the last four weeks."""
    errs_model: list[float] = []
    errs_naive: list[float] = []
    ratios: list[float] = []
    for i in range(MIN_WEEKS, len(series) - HORIZON + 1):
        past, future = series[:i], series[i:i + HORIZON]
        if (future[-1][0] - past[-1][0]).days != 7 * HORIZON:      # a gap (the server was off) breaks the window
            continue
        actual = sum(c for _, c in future)
        guess = predict_next(past)
        naive = sum(c for _, c in past[-HORIZON:])
        errs_model.append(abs(actual - guess))
        errs_naive.append(abs(actual - naive))
        if guess > 0:
            ratios.append(actual / guess)
    if len(errs_model) < MIN_BACKTEST:
        return {"tested": len(errs_model), "reliable": False, "needed": MIN_BACKTEST}
    ratios.sort()
    q = lambda p: ratios[min(len(ratios) - 1, int(p * len(ratios)))]  # noqa: E731
    mae, naive_mae = mean(errs_model), mean(errs_naive)
    return {"tested": len(errs_model), "reliable": mae <= 0.97 * naive_mae, "mae": round(mae, 1), "naive_mae": round(naive_mae, 1), "low_ratio": round(q(0.1), 2), "high_ratio": round(q(0.9), 2)}


def title_words(title: str) -> set[str]:
    t = re.sub(r"20\d\d([-/]\d{2,4})?|\d+", " ", (title or "").lower())
    return {w for w in re.findall(r"[a-z]{3,}", t) if w not in _STOP}


def similar(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def coming_round(memory: list[dict[str, Any]], today: date, limit: int = 15) -> list[dict[str, Any]]:
    """Tenders published in the next ~75 days of last year's calendar that have no look-alike from the department in the last 120 days."""
    last_year_from = today - timedelta(days=365)
    last_year_to = last_year_from + timedelta(days=RECUR_WINDOW_DAYS)
    recent_from = today - timedelta(days=120)
    recent: dict[str, list[set[str]]] = {}
    for m in memory:
        if _day(m["day"]) >= recent_from:
            recent.setdefault(m["dept"].lower(), []).append(title_words(m["title"]))
    out = []
    seen_sig: set[tuple[str, frozenset[str]]] = set()
    for m in memory:
        d = _day(m["day"])
        if not (last_year_from <= d <= last_year_to):
            continue
        words = title_words(m["title"])
        if len(words) < 2:
            continue
        sig = (m["dept"].lower(), frozenset(words))
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        if any(similar(words, w) >= RECUR_SIMILARITY for w in recent.get(m["dept"].lower(), [])):
            continue                                           # it has already come round this season
        out.append({"dept": m["dept"], "title": m["title"], "last_year": m["day"], "value": m["value"], "expected_from": (d + timedelta(days=365)).isoformat()})
    out.sort(key=lambda r: (r["expected_from"], -(r["value"] or 0)))
    return out[:limit]


def build(store: Any, now: datetime, top: int = 8) -> dict[str, Any]:
    today = now.date()
    first = store.first_flow_day()
    base: dict[str, Any] = {"history_days": 0, "weeks_observed": 0, "enough": False, "total": None, "departments": [], "coming_round": [], "backtest": None, "recent_four_weeks": None, "season_used": False}
    if not first:
        return base
    first_day = _day(first)
    base["history_days"] = (today - first_day).days
    since = (first_day).isoformat()
    flow = store.flow_rows(since)
    run_days = store.run_days(since)
    last_complete = week_start(today) - timedelta(days=1)
    series = weekly_counts(flow, run_days, first_day, last_complete)
    base["weeks_observed"] = len(series)
    if len(series) >= MIN_WEEKS:
        bt = backtest(series)
        expected = predict_next(series)
        ratio_lo, ratio_hi = (bt["low_ratio"], bt["high_ratio"]) if bt.get("reliable") or bt.get("low_ratio") else (None, None)
        base["total"] = {"expected": round(expected), "low": round(expected * ratio_lo) if ratio_lo else None, "high": round(expected * ratio_hi) if ratio_hi else None,
                         "range_from": "how the method missed in the past" if ratio_lo else "", "per_week": round(expected / HORIZON, 1)}
        base["recent_four_weeks"] = sum(c for _, c in series[-HORIZON:])
        base["season_used"] = len(series) >= SEASON_WEEKS
        base["backtest"] = bt
        base["enough"] = bool(bt.get("reliable"))
        by_dept: dict[str, list[dict[str, Any]]] = {}
        for r in flow:
            by_dept.setdefault(r["dept"], []).append(r)
        rows: list[dict[str, Any]] = []
        for dept, rs in by_dept.items():
            s = weekly_counts(rs, run_days, first_day, last_complete)
            if len(s) >= MIN_WEEKS and sum(c for _, c in s) >= 8:
                rows.append({"dept": dept or "Not stated", "expected": round(predict_next(s)), "last_four_weeks": sum(c for _, c in s[-HORIZON:]), "weeks_with_tenders": sum(1 for _, c in s if c)})
        rows.sort(key=lambda r: -r["expected"])
        base["departments"] = rows[:top]
    if (today - first_day).days >= 365:
        base["coming_round"] = coming_round(store.memory_rows((today - timedelta(days=365)).isoformat(), today.isoformat()), today)
    return base
