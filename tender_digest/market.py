"""The pool of award results the price advisor learns from: your own decided bids, plus results imported from a spreadsheet or typed in.

A contractor's own history is a few dozen bids. Public award lists (a portal's "results of tenders" or "award of contract" pages, a department's published
results, a spreadsheet you keep) hold far more, and the advisor is only as good as the results behind it. This module takes those in:

  * a CSV or a table pasted from a spreadsheet; column names are matched loosely (Department / Office, Estimated cost, Awarded amount, ...), Indian number
    formats work (5,00,000; Rs. 5,00,000; 2.5 crore) and so do day-first dates;
  * one result typed in by hand.

Every row is checked: it needs an estimate and an awarded price, and a price that is a tenth of the estimate, or five times it, is refused as a typing error
rather than being allowed to bend the advice. The same tender imported twice is kept once. Nothing here contacts a portal.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from typing import Any

from . import pricing
from .bids import BidBook

MAX_ROWS = 5000
MAX_TEXT = 1_000_000

# our field -> header words that mean it (matched as a whole word or phrase after tidying the header), most likely first
_HEADERS: dict[str, tuple[str, ...]] = {
    "estimate": ("estimated cost", "estimated value", "estimated amount", "estimate", "tender value", "tender estimate", "engineer estimate", "engineers estimate", "est cost", "est value", "pac", "cost put to tender", "value"),
    "price": ("awarded amount", "awarded value", "awarded price", "award value", "contract value", "contract amount", "accepted amount", "accepted value", "winning price", "winning bid",
              "winning amount", "l1 price", "l1 amount", "l1 value", "l1", "lowest bid", "lowest price", "quoted amount", "bid amount", "contract price", "work order value", "awarded to value", "amount"),
    "department": ("department", "organisation", "organization", "organisation chain", "office", "division", "circle", "authority", "buyer", "agency", "dept"),
    "work_type": ("work type", "type of work", "category", "tender category", "product category", "type"),
    "location": ("location", "place", "district", "taluka", "state"),
    "bidders": ("bidders", "no of bidders", "number of bidders", "bids received", "no of bids", "offers received", "participants"),
    "date": ("awarded date", "award date", "date of award", "aoc date", "contract date", "date", "published date", "result date"),
    "tender_id": ("tender id", "tender no", "tender number", "nit no", "nit number", "bid number", "reference", "ref no", "id"),
}


def _tidy(header: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (header or "").lower())).strip()


def map_headers(headers: list[str]) -> dict[str, int]:
    """{our field: column index}. Each column serves one field; exact header words win over a looser match."""
    tidy = [_tidy(h) for h in headers]
    found: dict[str, int] = {}
    used: set[int] = set()
    for strict in (True, False):
        for field, words in _HEADERS.items():
            if field in found:
                continue
            for w in words:
                for i, h in enumerate(tidy):
                    if i in used:
                        continue
                    if h == w or (not strict and len(w) > 3 and re.search(rf"\b{re.escape(w)}\b", h)):
                        found[field] = i
                        used.add(i)
                        break
                if field in found:
                    break
    return found


def parse_amount(text: str | None) -> float | None:
    """'₹ 5,00,000', 'Rs. 8,51,184.50', '2.5 crore', '50 lakh', '12L' -> rupees. Anything else -> None."""
    t = (text or "").strip().lower()
    if not t:
        return None
    t = re.sub(r"(₹|rs\.?|inr|/-)", " ", t)
    m = re.fullmatch(r"\s*(\d[\d,]*(?:\.\d+)?|\.\d+)\s*(crores?|cr|lakhs?|lacs?|lac|l|k|thousand)?\s*", t)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = m.group(2) or ""
    mult = 1e7 if unit.startswith("cr") else 1e5 if unit.startswith(("lakh", "lac")) or unit == "l" else 1e3 if unit in ("k", "thousand") else 1.0
    return n * mult


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y", "%d/%m/%y", "%d-%m-%y")


def parse_date(text: str | None) -> str | None:
    """A day-first date, as ISO 'YYYY-MM-DD'. Times are ignored. None when it cannot be read (the row is kept, just undated)."""
    t = re.sub(r"[T ]\d{1,2}:\d{2}.*$", "", (text or "").strip())
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if 1990 <= d.year <= datetime.now().year + 1:
            return d.date().isoformat()
    return None


def _ref(tender_id: str, dept: str, estimate: float, price: float, awarded: str | None) -> str:
    if tender_id:
        return "id:" + re.sub(r"\s+", "", tender_id.lower())
    raw = f"{dept.lower()}|{round(estimate)}|{round(price)}|{awarded or ''}"
    return "h:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def parse_table(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """(good rows, rejected rows with the reason, info about the columns). Never raises on bad content."""
    text = (text or "").lstrip("﻿")
    if len(text) > MAX_TEXT:
        return [], [{"line": 0, "reason": f"The file is too large ({len(text) // 1024} KB). Import up to {MAX_TEXT // 1024} KB at a time"}], {}
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return [], [{"line": 0, "reason": "Needs a header row and at least one result"}], {}
    cols = map_headers(rows[0])
    info = {"columns": {k: rows[0][i].strip() for k, i in cols.items()}, "missing": [f for f in ("estimate", "price") if f not in cols]}
    if info["missing"]:
        names = {"estimate": "the buyer's estimate (a column such as 'Estimated cost')", "price": "the awarded or lowest price (a column such as 'Awarded amount')"}
        return [], [{"line": 1, "reason": "Could not find " + " and ".join(names[m] for m in info["missing"])}], info
    good: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    for n, r in enumerate(rows[1:MAX_ROWS + 1], start=2):
        def cell(field: str, r: list[str] = r) -> str:
            i = cols.get(field)
            return r[i].strip() if i is not None and i < len(r) else ""
        est, price = parse_amount(cell("estimate")), parse_amount(cell("price"))
        if not est or not price:
            bad.append({"line": n, "reason": "The estimate or the awarded price is missing or not a number"})
            continue
        if pricing.row_from(est, price) is None:
            bad.append({"line": n, "reason": f"The price is {price / est * 100:.0f}% of the estimate: that looks like a typing error, so it was left out"})
            continue
        bidders = re.sub(r"[^\d]", "", cell("bidders"))
        awarded = parse_date(cell("date"))
        dept = cell("department")[:300]
        good.append({"department": dept, "work_type": cell("work_type")[:80], "location": cell("location")[:80], "estimate": est, "price": price,
                     "bidders": int(bidders) if bidders and int(bidders) < 1000 else None, "awarded": awarded, "ref": _ref(cell("tender_id")[:80], dept, est, price, awarded)})
    if len(rows) - 1 > MAX_ROWS:
        bad.append({"line": MAX_ROWS + 2, "reason": f"Only the first {MAX_ROWS} rows were read"})
    return good, bad, info


class MarketBook:
    def __init__(self, bids: BidBook):
        self.bids = bids
        self.db, self.lock = bids.db, bids.lock
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_results (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ref TEXT NOT NULL UNIQUE, department TEXT NOT NULL DEFAULT '', work_type TEXT NOT NULL DEFAULT '',
                  location TEXT NOT NULL DEFAULT '', estimate REAL NOT NULL, price REAL NOT NULL, bidders INTEGER, awarded TEXT, added TEXT NOT NULL);
                """)
            self.db.commit()

    def add_rows(self, rows: list[dict[str, Any]], source: str, now: datetime) -> dict[str, int]:
        added = dup = 0
        with self.lock:
            for r in rows:
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO market_results (source, ref, department, work_type, location, estimate, price, bidders, awarded, added) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (source, r["ref"], r["department"], r["work_type"], r["location"], r["estimate"], r["price"], r["bidders"], r["awarded"], now.isoformat(timespec="seconds")))
                if cur.rowcount:
                    added += 1
                else:
                    dup += 1
            self.db.commit()
        return {"added": added, "duplicates": dup}

    def recent(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM market_results ORDER BY COALESCE(awarded, added) DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def delete(self, result_id: int) -> bool:
        with self.lock:
            n = self.db.execute("DELETE FROM market_results WHERE id = ?", (result_id,)).rowcount
            self.db.commit()
        return bool(n)

    def clear(self, source: str | None = None) -> int:
        with self.lock:
            n = self.db.execute("DELETE FROM market_results" + (" WHERE source = ?" if source else ""), (source,) if source else ()).rowcount
            self.db.commit()
        return n

    def model_rows(self) -> list[dict[str, Any]]:
        """Every usable result for the estimator: your decided bids, then the pool, without counting a tender twice."""
        out: list[dict[str, Any]] = []
        own_ids: set[str] = set()
        with self.lock:
            bids = [dict(r) for r in self.db.execute("SELECT key, tender_id, stage, work_type, organisation, value, my_value, l1_value, stage_changed, updated FROM bids WHERE stage IN ('won','lost')")]
            pool = [dict(r) for r in self.db.execute("SELECT ref, department, work_type, estimate, price, awarded FROM market_results")]
        for b in bids:
            low = b["l1_value"] or (b["my_value"] if b["stage"] == "won" else None)
            row = pricing.row_from(b["value"], low, work_type=b["work_type"], organisation=b["organisation"], awarded=b["stage_changed"] or b["updated"], source="own")
            if row:
                out.append(row)
                own_ids.add("id:" + re.sub(r"\s+", "", (b["tender_id"] or "").lower()))
        for p in pool:
            if p["ref"] in own_ids:
                continue
            row = pricing.row_from(p["estimate"], p["price"], work_type=p["work_type"], organisation=p["department"], awarded=p["awarded"], source="market")
            if row:
                out.append(row)
        return out

    def summary(self) -> dict[str, Any]:
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM market_results").fetchone()[0]
            span = self.db.execute("SELECT MIN(awarded), MAX(awarded), COUNT(awarded) FROM market_results").fetchone()
            by_type = [dict(r) for r in self.db.execute("SELECT work_type AS label, COUNT(*) AS n FROM market_results GROUP BY work_type ORDER BY n DESC LIMIT 8")]
            by_dept = [dict(r) for r in self.db.execute("SELECT department AS label, COUNT(*) AS n FROM market_results GROUP BY department ORDER BY n DESC LIMIT 8")]
        rows = self.model_rows()
        return {"imported": total, "own": sum(1 for r in rows if r["source"] == "own"), "usable": len(rows), "dated": span[2], "first": span[0], "last": span[1],
                "by_work_type": [{"label": r["label"] or "Not stated", "n": r["n"]} for r in by_type], "by_department": [{"label": r["label"] or "Not stated", "n": r["n"]} for r in by_dept]}
