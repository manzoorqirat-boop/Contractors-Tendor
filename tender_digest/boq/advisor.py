"""Price advisor: turns your cost range and your past results into a quote you can defend.

Inputs are your cost before and after the risk allowances, the buyer's estimate, and how the lowest bidder (L1) priced similar
tenders you have already seen, as a percentage above (+) or below (-) their estimate. Output is a table of quotes from "loses
money" to "comfortable", each with the margin it leaves and the share of past winners it would have beaten.

The win chance is the plain share of past L1 prices that were at or above the quote: no fitted curve, so the number always has a
sample count behind it. With fewer than MIN_SAMPLES results it is not shown at all.
"""
from __future__ import annotations

from typing import Any

MIN_SAMPLES = 3


def price_for_margin(cost: float, margin_pct: float) -> float:
    """The price that leaves `margin_pct` of the price as profit over `cost`."""
    return cost / (1 - margin_pct / 100)


def win_chance(pct: float, outcomes: list[float]) -> float | None:
    """Share (0 to 100) of past L1 prices at or above `pct`. None when there is too little history."""
    if len(outcomes) < MIN_SAMPLES:
        return None
    return round(100 * sum(1 for o in outcomes if o >= pct - 1e-9) / len(outcomes), 1)


def confidence(n: int) -> str:
    return "none" if n < MIN_SAMPLES else "rough" if n < 5 else "fair" if n < 15 else "good"


def advise(cost_low: float, cost_high: float, estimate: float, outcomes: list[float], min_margin_pct: float = 3.0,
           target_margin_pct: float = 8.0, basis: str = "", samples: int | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """`samples` is how many real results stand behind `outcomes` when the outcomes are a smoothed or blended picture of them (then there are more
    outcomes than results); by default each outcome is one result. `extra` is added to the answer as it is."""
    if not (0 < cost_low <= cost_high):
        raise ValueError("Costs must be above zero, and the high cost cannot be below the low cost.")
    if estimate <= 0:
        raise ValueError("The buyer's estimate must be above zero.")
    if not (0 <= min_margin_pct < target_margin_pct < 50):
        raise ValueError("Margins must satisfy 0 <= minimum < target < 50 (per cent of the price).")
    outcomes = sorted(o for o in outcomes if o == o and -90 < o < 200)
    if samples is not None and samples < MIN_SAMPLES:
        outcomes = []
    cost_mid = (cost_low + cost_high) / 2

    def pct_of(price: float) -> float:
        return (price / estimate - 1) * 100

    floor_pct = pct_of(price_for_margin(cost_high, min_margin_pct))          # worst case: every allowance hits
    target_pct = max(pct_of(price_for_margin(cost_mid, target_margin_pct)), floor_pct)

    lo = min(floor_pct, outcomes[0] if outcomes else floor_pct) - 2
    hi = max(target_pct, outcomes[-1] if outcomes else target_pct) + 2
    step = 0.5 if hi - lo <= 40 else 1.0
    start = int(lo / step) * step - step
    points: list[dict[str, Any]] = []
    q = start
    while q <= hi + 1e-9 and len(points) < 160:
        price = estimate * (1 + q / 100)
        worst, best = price - cost_high, price - cost_low
        win = win_chance(q, outcomes)
        points.append({"pct": round(q, 2), "price": round(price, 2), "margin_worst": round(worst, 2), "margin_best": round(best, 2),
                       "margin_pct_worst": round(100 * worst / price, 2),
                       "win": win, "expected": round(win / 100 * (price - cost_mid), 2) if win is not None else None})
        q += step

    n = len(outcomes) if samples is None else (samples if outcomes else 0)
    safe = [p for p in points if p["pct"] >= floor_pct - 1e-9]
    if n >= MIN_SAMPLES and safe:
        best_point = max(safe, key=lambda p: (p["expected"] or 0, p["pct"]))
        recommended = best_point["pct"]
    else:
        recommended = min(safe, key=lambda p: abs(p["pct"] - target_pct))["pct"] if safe else round(target_pct, 2)

    notes: list[str] = []
    if n < MIN_SAMPLES:
        notes.append("There are not enough recorded results to estimate a win chance, so the recommendation is simply your target margin. "
                     "Record the winning price on lost and won bids (Bids > Results) and this panel will learn from them.")
    else:
        at_target = win_chance(target_pct, outcomes)
        if at_target is not None and at_target < 15:
            notes.append(f"At your target margin only about {at_target:.0f}% of similar past winners priced higher, so winning is unlikely. "
                         "Consider a lower margin, or a no-bid.")
        if n < 5:
            notes.append(f"Only {n} past results back this up, so treat the win chance as a rough guide.")
    if floor_pct <= -10:
        notes.append("Your break-even is 10% or more below the estimate. Abnormally-low-bid rules may ask for extra performance security.")
    if cost_high - cost_low > 0.1 * cost_low:
        notes.append("The risk range is wide (more than 10% of cost). Firm up the unclear risks before settling on the quote.")

    return {"estimate": round(estimate, 2), "cost_low": round(cost_low, 2), "cost_high": round(cost_high, 2),
            "floor_pct": round(floor_pct, 2), "target_pct": round(target_pct, 2), "recommended_pct": recommended,
            "samples": n, "confidence": confidence(n), "basis": basis, "points": points, "notes": notes,
            "min_margin": min_margin_pct, "target_margin": target_margin_pct, **(extra or {})}
