"""Rate comparison (Layer 3.3): what a matched BOQ line really costs you, against the buyer's estimate.

    true unit cost = net material qty x (1 + wastage %) x ERP rate + labour + machinery + overheads

An activity (composite) rate from the activity master already holds those parts. A composite item rate is used as it is.
A raw-material rate (cement, steel ...) only covers the material, so it is shown but never counted as a full cost.
Only AUTO_MATCHED lines are priced unless `include_review` is set: a guess must not move the bid figure silently.
Lines that cannot be priced are carried at the buyer's estimate, and the result says how much of the BOQ that is.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .match import AUTO_MATCHED, NEEDS_REVIEW, MatchResult

# standard wastage allowances (low %, high %) by material, from the blueprint
WASTAGE: dict[str, tuple[float, float]] = {
    "cement": (2, 3), "rmc": (2, 3), "ready mix": (2, 3), "steel": (3, 5), "tmt": (3, 5), "brick": (5, 8), "aac": (5, 8), "block": (5, 8),
    "sand": (5, 7), "aggregate": (5, 7), "tile": (5, 10), "granite": (5, 10), "pipe": (2, 5), "cable": (2, 5),
}


def default_wastage(text: str) -> tuple[float, float, float] | None:
    """(low, mid, high) wastage % for a material named in `text`, or None when no allowance is known."""
    low = (text or "").lower()
    for key, (a, b) in WASTAGE.items():
        if key in low:
            return a, (a + b) / 2, b
    return None


def true_unit_cost(net_material_cost: float, wastage_pct: float, labor: float = 0.0, machinery: float = 0.0, overheads: float = 0.0) -> float:
    """net_material_cost is the net material quantity x the ERP rate, per unit of the BOQ item."""
    return net_material_cost * (1 + wastage_pct / 100.0) + labor + machinery + overheads


@dataclass
class LineCost:
    index: int
    item_code: str | None
    basis: str                     # activity | composite item | material only | sor reference | none
    unit_cost: float | None
    est_rate: float | None
    variance_pct: float | None     # (unit cost - estimate) / estimate x 100; negative means you are cheaper than the estimate
    complete: bool                 # True when unit_cost is a full cost that may be added up
    est_amount: float | None
    cost_amount: float | None
    note: str


def _est_amount(line: Any) -> float | None:
    if getattr(line, "amount", None) is not None:
        return float(line.amount)
    if getattr(line, "qty", None) is not None and getattr(line, "rate", None) is not None:
        return float(line.qty) * float(line.rate)
    return None


def cost_line(line: Any, result: MatchResult, items_by_code: dict[str, dict], activity_for: Callable[[str], dict | None] | None = None,
              location: dict | None = None, include_review: bool = False) -> LineCost:
    est = _est_amount(line)
    base: dict[str, Any] = dict(index=result.index, item_code=result.item_code, est_rate=getattr(line, "rate", None), est_amount=est)

    def out(basis: str, unit: float | None, complete: bool, note: str) -> LineCost:
        rate = base["est_rate"]
        var = round((unit - rate) / rate * 100, 2) if unit is not None and rate else None
        cost = round(unit * line.qty, 2) if complete and unit is not None and getattr(line, "qty", None) is not None else None
        return LineCost(basis=basis, unit_cost=None if unit is None else round(unit, 2), variance_pct=var, complete=complete and cost is not None,
                        cost_amount=cost, note=note, **base)

    usable = result.status == AUTO_MATCHED or (include_review and result.status == NEEDS_REVIEW)
    item = items_by_code.get(result.item_code or "") if usable else None
    if not item:
        ref = result.sor_reference
        if ref:
            return out("sor reference", ref["rate"], False, f"Benchmark only: {ref['source']} rate for code {ref['code']}. Not your cost.")
        return out("none", None, False, "Not priced: no confirmed match." if not usable else "Matched item is missing from the item master.")

    factor = 1.0
    if location:
        factor = float(location.get("cost_index") or 1.0) * (1 + float(location.get("hill_carriage_pct") or 0.0) / 100.0)
    act = activity_for(item["erp_item_code"]) if activity_for else None
    if act:
        unit = true_unit_cost(act["material_cost"], act["wastage_pct"], act["labor_cost"], act["machinery_cost"], act["overhead_cost"])
        return out("activity", unit * factor, True, "Built from the activity master: material with wastage, labour, machinery and overheads.")
    rate = item.get("standard_base_rate")
    if rate is None:
        return out("none", None, False, "The matched item has no rate in the item master.")
    if item.get("is_composite"):
        return out("composite item", float(rate) * factor, True, "Composite item rate used as it is.")
    w = default_wastage(f"{item['item_name']} {item.get('category') or ''}")
    unit = float(rate) * (1 + (w[1] if w else 0.0) / 100.0) * factor
    return out("material only", unit, False, "Material rate" + (f" with {w[1]:g}% wastage" if w else "") + "; labour, machinery and overheads are not in it, so it is not counted as a full cost.")


def price_boq(lines: list[Any], results: list[MatchResult], items: list[dict], activity_for: Callable[[str], dict | None] | None = None,
              location: dict | None = None, include_review: bool = False, manual: dict[int, float] | None = None) -> dict[str, Any]:
    """Cost every line it can and say how far the result can be trusted.

    Unpriced lines are carried at the buyer's estimate. `break_even_pct` is the single percentage a bidder would quote
    to just recover cost: negative means below the estimate (a 'less' quote), positive above it.
    """
    by_code = {i["erp_item_code"]: i for i in items}
    costs = [cost_line(ln, r, by_code, activity_for, location, include_review) for ln, r in zip(lines, results, strict=False)]
    for i, cost in (manual or {}).items():  # a cost the person entered for a line (a lump sum, or an item not in the catalogue) replaces whatever was worked out
        if 0 <= i < len(costs):
            c, qty = costs[i], getattr(lines[i], "qty", None)
            unit = round(cost / qty, 2) if qty else None
            var = round((unit - c.est_rate) / c.est_rate * 100, 2) if unit is not None and c.est_rate else (round((cost - c.est_amount) / c.est_amount * 100, 2) if c.est_amount else None)
            costs[i] = LineCost(index=c.index, item_code=c.item_code, basis="your cost", unit_cost=unit, est_rate=c.est_rate, variance_pct=var, complete=True,
                                est_amount=c.est_amount, cost_amount=round(cost, 2), note="The cost you entered for this line.")
    est_total = sum(c.est_amount or 0.0 for c in costs)
    priced = [c for c in costs if c.complete and c.est_amount]
    priced_est = sum(c.est_amount or 0.0 for c in priced)
    priced_cost = sum(c.cost_amount or 0.0 for c in priced)
    projected = priced_cost + (est_total - priced_est)
    return {
        "lines": costs,
        "estimate_total": round(est_total, 2),
        "priced_lines": len(priced), "total_lines": len(costs),
        "coverage_pct": round(100 * priced_est / est_total, 1) if est_total else 0.0,
        "priced_estimate": round(priced_est, 2), "priced_cost": round(priced_cost, 2),
        "projected_cost": round(projected, 2),
        "break_even_pct": round((projected / est_total - 1) * 100, 2) if est_total else None,
        "assumption": "Lines that could not be priced are carried at the buyer's estimate.",
    }
