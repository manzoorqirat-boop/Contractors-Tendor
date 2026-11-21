"""Learning how the lowest bidder (L1) prices: from your own decided bids and from award results you import.

Every result is one number: L1's price as a percentage above (+) or below (-) the buyer's estimate. The advisor needs the spread of that number for a tender like
the one in front of you. Your own history is too thin to give it for most segments (one company decides a few dozen bids), so results from any source are pooled and
the estimate is built from the most specific group that has enough results, borrowing strength from wider groups when it does not:

    all results  ->  same work type  ->  same size  ->  same department  ->  same department and work type

Each level is blended into the one above it with weight n / (n + K), where n is how many (recency-weighted) results the level holds. A level with 2 results barely
moves the answer; one with 40 mostly decides it. This is empirical-Bayes shrinkage: a small, explainable model that is honest with little data, where a neural network
would only memorise noise. Newer results count more (half-life two years), because buying habits and market prices drift.

`backtest` checks the whole idea against history: for each result in date order, estimate from the earlier ones only and see whether the real L1 fell inside the range
that was predicted. The share that did is shown next to the advice, so nobody has to take the model on trust.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

K = 8.0                      # how many results a level needs before it counts for half: bigger = more cautious about specific groups
HALF_LIFE_DAYS = 730.0       # a result this old counts half as much
UNDATED_WEIGHT = 0.6         # a result with no date is probably old, but not worthless
MIN_SPECIFIC = 5             # raw results a group needs to be named as "the basis"
GRID = 50                    # the answer is handed to the advisor as this many equally likely outcomes
QUANTILES = (10, 25, 50, 75, 90)
SANE_PCT = (-70.0, 150.0)    # an L1 outside this range against the estimate is a typing error or a different job, not a price


def band(v: float | None) -> str:
    if v is None or v <= 0:
        return "Not stated"
    return "Under 1 lakh" if v < 1e5 else "1 to 10 lakh" if v < 1e6 else "10 lakh to 1 crore" if v < 1e7 else "1 to 10 crore" if v < 1e8 else "Over 10 crore"


def department(org: str | None) -> str:
    return ((org or "").split("||")[0].strip()).lower()


def row_from(estimate: float | None, l1_price: float | None, *, work_type: str | None = None, organisation: str | None = None, value: float | None = None,
             awarded: str | None = None, source: str = "market") -> dict[str, Any] | None:
    """One usable result, or None when the numbers cannot be one (no estimate, no price, or a ratio that is plainly a mistake)."""
    if not estimate or not l1_price or estimate <= 0 or l1_price <= 0:
        return None
    pct = (l1_price / estimate - 1) * 100
    if not (SANE_PCT[0] <= pct <= SANE_PCT[1]):
        return None
    when = None
    if awarded:
        try:
            when = datetime.fromisoformat(awarded[:19])
        except ValueError:
            when = None
    return {"pct": pct, "work_type": (work_type or "").strip(), "dept": department(organisation), "band": band(value or estimate), "when": when, "source": source}


def weight(row: dict[str, Any], now: datetime) -> float:
    when = row.get("when")
    if not when:
        return UNDATED_WEIGHT
    age = max(0.0, (now - when).total_seconds() / 86400)
    return 0.5 ** (age / HALF_LIFE_DAYS)


def quantile(samples: list[tuple[float, float]], q: float) -> float:
    """Weighted quantile (q from 0 to 1) of (value, weight) pairs."""
    pts = sorted(samples)
    total = sum(w for _, w in pts)
    target, run = q * total, 0.0
    for v, w in pts:
        run += w
        if run >= target - 1e-12:
            return v
    return pts[-1][0]


def _levels(rows: list[dict[str, Any]], work_type: str, dept: str, size: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """From widest to most specific: the rows that match at each level (a level the query cannot name is left out)."""
    out: list[tuple[str, list[dict[str, Any]]]] = [("all results", rows)]
    if work_type:
        out.append((work_type, [r for r in rows if r["work_type"] == work_type]))
    if size != "Not stated":
        out.append((f"size {size}", [r for r in rows if r["band"] == size]))
    if dept:
        out.append(("this department", [r for r in rows if r["dept"] == dept]))
        if work_type:
            out.append((f"{work_type} in this department", [r for r in rows if r["dept"] == dept and r["work_type"] == work_type]))
    return out


def estimate(rows: list[dict[str, Any]], work_type: str | None, organisation: str | None, value: float | None, now: datetime) -> dict[str, Any]:
    """The expected spread of L1 against the estimate for a tender like this one.

    Returns outcomes (GRID equally likely values, for the advisor), quantiles, how many results stand behind it, where they came from and a plain-words basis.
    """
    empty: dict[str, Any] = {"outcomes": [], "samples": 0, "quantiles": {}, "basis": "", "own": 0, "market": 0, "levels": []}
    if not rows:
        return empty
    wt, dept, size = (work_type or "").strip(), department(organisation), band(value)
    weights = [weight(r, now) for r in rows]
    for r, w in zip(rows, weights, strict=True):
        r["_w"] = w
    dist: list[tuple[float, float]] = []     # (pct, probability weight) of the blend so far, always summing to 1
    used: list[dict[str, Any]] = []
    basis_rows = rows
    basis_name = "all results"
    for name, group in _levels(rows, wt, dept, size):
        if not group:
            continue
        n_eff = sum(r["_w"] for r in group)
        total = sum(r["_w"] for r in group) or 1.0
        local = [(r["pct"], r["_w"] / total) for r in group]
        lam = n_eff / (n_eff + K)
        dist = local if not dist else [(p, w * lam) for p, w in local] + [(p, w * (1 - lam)) for p, w in dist]
        used.append({"level": name, "n": len(group), "weight": round(lam, 2)})
        if len(group) >= MIN_SPECIFIC:
            basis_rows, basis_name = group, name
    outcomes = [round(quantile(dist, (i + 0.5) / GRID), 2) for i in range(GRID)]
    quantiles = {f"p{q}": round(quantile(dist, q / 100), 1) for q in QUANTILES}
    own = sum(1 for r in basis_rows if r["source"] == "own")
    basis = f"{len(basis_rows)} result{'s' if len(basis_rows) != 1 else ''}: {basis_name}" + (f" ({own} yours, {len(basis_rows) - own} imported)" if basis_rows else "")
    return {"outcomes": outcomes, "samples": len(basis_rows), "quantiles": quantiles, "basis": basis, "own": own, "market": len(basis_rows) - own, "levels": used}


def _pinball(q: float, predicted: float, actual: float) -> float:
    return max(q * (actual - predicted), (q - 1) * (actual - predicted))


def backtest(rows: list[dict[str, Any]], now: datetime, min_history: int = 15, limit: int = 300) -> dict[str, Any]:
    """Is the model any good? Results in date order; each is predicted from the earlier ones only. Compared with the plain approach of using every earlier result.

    Reports how often the real L1 fell inside the predicted middle half and middle 80% (they should be about 50% and 80%), and how far the predicted median was off,
    for this model and for the plain pooled median. Dated results only: without dates there is no "earlier".
    """
    dated = sorted((r for r in rows if r.get("when")), key=lambda r: r["when"])[-limit:]
    if len(dated) <= min_history:
        return {"tested": 0, "needed": min_history + 1, "have": len(dated)}
    inside50 = inside80 = 0
    err_model = err_plain = loss_model = loss_plain = 0.0
    tested = 0
    for i in range(min_history, len(dated)):
        target, history = dated[i], dated[:i]
        guess = estimate([dict(h) for h in history], target["work_type"], (target["dept"] or None), None if target["band"] == "Not stated" else _band_midpoint(target["band"]), target["when"])
        if not guess["quantiles"]:
            continue
        q = guess["quantiles"]
        plain = [(h["pct"], 1.0) for h in history]
        plain_med = quantile(plain, 0.5)
        actual = target["pct"]
        tested += 1
        inside50 += q["p25"] <= actual <= q["p75"]
        inside80 += q["p10"] <= actual <= q["p90"]
        err_model += abs(actual - q["p50"])
        err_plain += abs(actual - plain_med)
        for quant, key in ((0.1, "p10"), (0.25, "p25"), (0.5, "p50"), (0.75, "p75"), (0.9, "p90")):
            loss_model += _pinball(quant, q[key], actual)
            loss_plain += _pinball(quant, quantile(plain, quant), actual)
    if not tested:
        return {"tested": 0, "needed": min_history + 1, "have": len(dated)}
    return {"tested": tested, "inside_middle_half_pct": round(100 * inside50 / tested), "inside_middle_80_pct": round(100 * inside80 / tested),
            "median_error_pts": round(err_model / tested, 1), "plain_median_error_pts": round(err_plain / tested, 1),
            "better_than_plain_pct": round(100 * (1 - loss_model / loss_plain)) if loss_plain else 0}


def _band_midpoint(b: str) -> float:
    return {"Under 1 lakh": 5e4, "1 to 10 lakh": 5e5, "10 lakh to 1 crore": 5e6, "1 to 10 crore": 5e7, "Over 10 crore": 2e8}.get(b, 5e6)
