"""Which open bids to submit when earnest money (EMD) cash is limited?

Every bid you submit ties up its EMD until the result. With limited free cash, the question is which bids deserve it. For each open bid this estimates the expected profit,
chance of winning x the estimate x your target margin, and picks the set that maximises the total expected profit within the cash, exactly (a knapsack over the EMD amounts),
not by a rule of thumb. Each bid says where its chance of winning came from (your win model, your overall win rate, or an assumption), and every bid left out says why.

It is a rough guide with stated assumptions: the margin is the same for every bid, the estimate stands in for the contract value (you rarely win at exactly the estimate),
EMDs are assumed to come back (a forfeited one is a loss this ignores), and the cash is a single pool. It cannot know your capacity, your relationships or strategy, so it informs
the choice and does not make it.
"""
from __future__ import annotations

import math
from typing import Any

MAX_STEPS = 6000


def _unit(available: float) -> float:
    """Rupees per step of the cash table: ₹5,000 for ordinary amounts, larger for very large ones so the table stays small."""
    return max(5000.0, math.ceil(available / MAX_STEPS / 5000) * 5000.0)


def expected_profit(p: float, value: float | None, margin_pct: float) -> float:
    return round(p * (value or 0) * margin_pct / 100, 2)


def choose(items: list[tuple[float, float]], available: float) -> list[int]:
    """The indexes of the items, as (cost, gain) pairs, with the greatest total gain within `available` (0/1 knapsack, exact to the step size)."""
    unit = _unit(available)
    cap = int(available // unit)
    weights = [int(math.ceil(max(c, 0) / unit)) for c, _ in items]
    best = [[0.0] * (cap + 1) for _ in range(len(items) + 1)]
    for i, ((_, gain), w) in enumerate(zip(items, weights, strict=True), start=1):
        for c in range(cap + 1):
            best[i][c] = best[i - 1][c]
            if w <= c and best[i - 1][c - w] + gain > best[i][c]:
                best[i][c] = best[i - 1][c - w] + gain
    chosen, c = [], cap
    for i in range(len(items), 0, -1):
        if best[i][c] != best[i - 1][c]:
            chosen.append(i - 1)
            c -= weights[i - 1]
    return sorted(chosen)


def plan(cands: list[dict[str, Any]], available: float | None, margin_pct: float) -> dict[str, Any]:
    """`cands`: open bids as {id, title, tender_id, value, emd, closing, p, p_source}. `available`: free cash for EMDs, or None when it was not given."""
    rows = []
    for c in cands:
        emd = c.get("emd")
        rows.append({**c, "emd_known": emd is not None, "emd": emd or 0.0, "profit": expected_profit(c["p"], c.get("value"), margin_pct)})
    usable = [r for r in rows if r["profit"] > 0]
    picked = list(range(len(usable))) if available is None else choose([(r["emd"], r["profit"]) for r in usable], max(available, 0.0))
    chosen_ids = {usable[i]["id"] for i in picked}
    used = sum(r["emd"] for r in usable if r["id"] in chosen_ids)
    left = None if available is None else max(available, 0.0) - used
    out_rows = []
    for r in rows:
        take = r["id"] in chosen_ids
        reason = ""
        if not take:
            if r["profit"] <= 0:
                reason = "It has no estimated value, so its expected profit cannot be worked out" if not r.get("value") else "Its chance of winning is zero"
            elif available is not None and r["emd"] > max(available, 0.0):
                reason = f"Its EMD is more than all the cash you gave ({r['emd']:,.0f} against {max(available, 0.0):,.0f})"
            else:
                reason = "The cash earns more expected profit on the bids chosen instead"
        out_rows.append({**r, "decision": "submit" if take else "skip", "reason": reason})
    out_rows.sort(key=lambda r: (r["decision"] != "submit", r.get("closing") is None, r.get("closing") or "", -r["profit"]))
    more = None
    if left is not None:
        skipped = sorted((r for r in out_rows if r["decision"] == "skip" and r["profit"] > 0), key=lambda r: -r["profit"])
        if skipped:
            top = skipped[0]
            more = {"id": top["id"], "title": top["title"], "extra_cash": max(0.0, round(top["emd"] - left, 2)), "profit": top["profit"]}
    return {"rows": out_rows, "emd_used": round(used, 2), "available": available, "left": None if left is None else round(left, 2),
            "expected_profit": round(sum(r["profit"] for r in out_rows if r["decision"] == "submit"), 2), "submit": len(chosen_ids), "candidates": len(rows), "margin_pct": margin_pct,
            "with_more_cash": more, "emd_unknown": sum(1 for r in out_rows if r["decision"] == "submit" and not r["emd_known"])}
