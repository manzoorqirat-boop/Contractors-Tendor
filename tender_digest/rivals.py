"""Who usually wins here? From the winners on record: your own bidder lists and imported award results that name the winner.

For a tender in a given department and kind of work, this lists the companies that have won similar ones: how many, their share of the wins, the price they usually
win at against the estimate, when they last won, and (from your own bids) how you have fared against them. The list is only as good as the results behind it, so it says how
many there are and where they came from, and a list of fewer than MIN_RESULTS is not shown as a pattern.

It reports who won, never why. A few firms winning most awards in a department can mean they are good, local, or that the work suits them; it is something to know before
bidding, not an accusation, and the page says so.
"""
from __future__ import annotations

from statistics import median
from typing import Any

MIN_GROUP = 5            # winners a group needs before it is used as "the same kind of tender"
MIN_RESULTS = 3          # fewer than this and there is nothing to show
CONCENTRATION_MIN = 8    # winners needed before talking about how concentrated the wins are


def _pick(rows: list[dict[str, Any]], work_type: str, dept: str) -> tuple[list[dict[str, Any]], str]:
    wt = (work_type or "").strip()
    d = (dept or "").strip().lower()
    both = [r for r in rows if wt and d and r["work_type"] == wt and r["dept"] == d]
    if len(both) >= MIN_GROUP:
        return both, f"{wt} awards in this department"
    same_dept = [r for r in rows if d and r["dept"] == d]
    if len(same_dept) >= MIN_GROUP:
        return same_dept, "awards in this department"
    same_type = [r for r in rows if wt and r["work_type"] == wt]
    if len(same_type) >= MIN_GROUP:
        return same_type, f"{wt} awards"
    return rows, "all awards on record"


def who_wins(rows: list[dict[str, Any]], key_of: Any, work_type: str | None, department: str | None, head_to_head: dict[str, dict[str, int]] | None = None, limit: int = 8) -> dict[str, Any]:
    """The winners among similar tenders. `rows` as from MarketBook.winner_rows; `key_of` turns a company name into a key that matches its spellings;
    `head_to_head` is {key: {met, ahead}} from your own bids."""
    group, basis = _pick([r for r in rows if r.get("name")], work_type or "", department or "")
    total = len(group)
    out: dict[str, Any] = {"total": total, "basis": basis, "rows": [], "top3_share_pct": None, "own": sum(1 for r in group if r["source"] == "own"), "market": sum(1 for r in group if r["source"] == "market")}
    if total < MIN_RESULTS:
        return out
    by: dict[str, list[dict[str, Any]]] = {}
    for r in group:
        k = key_of(r["name"])
        if k:
            by.setdefault(k, []).append(r)
    hh = head_to_head or {}
    table = []
    for k, rs in by.items():
        names = [r["name"] for r in rs]
        shown = max(set(names), key=lambda n: (names.count(n), len(n)))
        gaps = [(r["price"] / r["estimate"] - 1) * 100 for r in rs if r.get("price") and r.get("estimate") and r["estimate"] > 0 and -70 < (r["price"] / r["estimate"] - 1) * 100 < 150]
        h = hh.get(k)
        table.append({"key": k, "name": shown, "mine": any(r["mine"] for r in rs), "wins": len(rs), "share_pct": round(100 * len(rs) / total),
                      "usual_vs_estimate": round(median(gaps), 1) if gaps else None, "priced": len(gaps), "last": max((r["when"] for r in rs), default="") or None,
                      "met": h["met"] if h else 0, "ahead": h["ahead"] if h else 0})
    table.sort(key=lambda t: (-t["wins"], t["name"]))
    out["rows"] = table[:limit]
    if total >= CONCENTRATION_MIN and len(table) > 3:      # with three winners or fewer "the top three took 100%" says nothing
        out["top3_share_pct"] = round(100 * sum(t["wins"] for t in table[:3]) / total)
    return out
