"""Read a tender BOQ from CSV or Excel (Layer 3.1).

Handles the two BOQ styles: *item rate* (bidder fills a rate on every line) and *percentage rate* (buyer pre-fills the
estimated rates and the bidder quotes one overall % above or below). Section headings and sub-totals are recognised and
kept out of the item list. Line arithmetic (quantity x rate against amount) is checked and any mismatch is reported.
PDF BOQs are not read here: portals publish the BOQ as .xls/.xlsx, and PDF layouts are too varied to parse reliably.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import uom as U

_HEADS = {
    "sr": re.compile(r"^(sr\.?|s\.?\s?no\.?|sl\.?|sl\.? no\.?|item no\.?|no\.?|serial)", re.I),
    "desc": re.compile(r"description|particulars|item of work|name of (work|item)|^item$|^work$", re.I),
    "uom": re.compile(r"^(unit|uom|units?)\b", re.I),
    "qty": re.compile(r"^(qty|quantity)\b", re.I),
    "rate": re.compile(r"\brate\b", re.I),
    "amount": re.compile(r"^(amount|total|total amount|value)\b", re.I),
    "code": re.compile(r"(sor|dsr|ssr|schedule)\s*(item|code|no)|^code$", re.I),
}
_SKIP = re.compile(r"sub[\s-]?total|grand\s+total|^total\b|carried (forward|over)|brought forward|^note\b|^in words", re.I)
_PCT = re.compile(r"percentage|%\s*(above|below|excess|less)|above\s+or\s+below|excess\s*(/|or)\s*less|\(\s*\+\s*\)\s*/\s*\(\s*-\s*\)", re.I)
_ITEM_RATE = re.compile(r"item[\s-]*rate", re.I)
_CODE_IN_TEXT = re.compile(r"\b(?:DSR|SOR|SSR|CPWD|MORTH|MoRTH)\b[^\d\n]{0,25}?(?:item|code|no\.?|clause)?[\s:#-]*(\d{1,3}(?:\.\d{1,3}){1,3})", re.I)


def number(v: Any) -> float | None:
    """'1,02,41,420.00' -> 10241420.0; '(250)' -> -250; blank, '-' and words -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("₹", "").replace("Rs.", "").replace("Rs", "").replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not s or not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    return -float(s) if neg else float(s)


@dataclass
class BoqLine:
    row: int                       # row number in the source sheet, for pointing the user back at it
    sr: str
    section: str
    description: str
    uom_raw: str
    uom: str | None                # canonical unit, None when blank or not recognised
    qty: float | None
    rate: float | None
    amount: float | None
    sor_code: str | None = None


@dataclass
class BoqDocument:
    rate_type: str                 # 'item_rate' | 'percentage_rate' | 'unknown'
    lines: list[BoqLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimated_total: float | None = None   # sum of line amounts (amount column, else qty x rate)

    def as_rows(self) -> list[dict]:
        return [{"sr": ln.sr, "section": ln.section, "description": ln.description, "uom_raw": ln.uom_raw, "uom": ln.uom, "qty": ln.qty,
                 "est_rate": ln.rate, "est_amount": ln.amount, "sor_code": ln.sor_code} for ln in self.lines]


def _find_header(rows: list[list[Any]]) -> tuple[int, dict[str, int]] | None:
    for i, row in enumerate(rows[:40]):
        found: dict[str, int] = {}
        for j, cell in enumerate(row):
            text = str(cell or "").strip()
            if not text:
                continue
            for key, rx in _HEADS.items():
                if key not in found and rx.search(text):
                    found[key] = j
                    break
        if "desc" in found and "qty" in found and ("uom" in found or "rate" in found):
            return i, found
    return None


def parse_rows(rows: list[list[Any]]) -> BoqDocument:
    """Parse a sheet given as a list of rows (each a list of cell values)."""
    doc = BoqDocument(rate_type="unknown")
    alltext = " ".join(str(c) for r in rows for c in r if c is not None)
    hdr = _find_header(rows)
    if not hdr:
        doc.warnings.append("No BOQ table found: could not find a header row with Description, Quantity and Unit/Rate columns.")
        return doc
    start, col = hdr
    section = ""
    for i in range(start + 1, len(rows)):
        r = rows[i]
        cell = lambda k: (r[col[k]] if k in col and col[k] < len(r) else None)  # noqa: E731
        desc = re.sub(r"\s+", " ", str(cell("desc") or "")).strip()
        if not desc or _SKIP.search(desc):
            continue
        qty, rate, amount = number(cell("qty")), number(cell("rate")), number(cell("amount"))
        uom_raw = str(cell("uom") or "").strip()
        if qty is None and not uom_raw and rate is None:  # a heading such as 'B. Granular Sub-base and Base Courses'
            section = desc
            continue
        code = str(cell("code") or "").strip() or None
        if not code:
            m = _CODE_IN_TEXT.search(desc)
            code = m.group(1) if m else None
        line = BoqLine(row=i + 1, sr=str(cell("sr") or "").strip(), section=section, description=desc, uom_raw=uom_raw,
                       uom=U.normalise_uom(uom_raw), qty=qty, rate=rate, amount=amount, sor_code=code)
        if line.uom is None:
            doc.warnings.append(f"Row {line.row}: unit '{uom_raw}' is not recognised." if uom_raw else f"Row {line.row}: no unit given.")
        if qty is not None and rate is not None and amount is not None and abs(qty * rate - amount) > max(1.0, 0.005 * abs(amount)):
            doc.warnings.append(f"Row {line.row}: quantity x rate = {qty * rate:,.2f} but the amount column says {amount:,.2f}.")
        doc.lines.append(line)

    amounts = [ln.amount if ln.amount is not None else (ln.qty * ln.rate if ln.qty is not None and ln.rate is not None else None) for ln in doc.lines]
    if amounts and all(a is not None for a in amounts):
        doc.estimated_total = round(sum(a for a in amounts if a is not None), 2)
    filled = sum(1 for ln in doc.lines if ln.rate is not None)
    if _PCT.search(alltext):
        doc.rate_type = "percentage_rate"
    elif _ITEM_RATE.search(alltext):
        doc.rate_type = "item_rate"
    elif doc.lines:
        doc.rate_type = "percentage_rate" if filled >= 0.8 * len(doc.lines) else "item_rate" if filled <= 0.2 * len(doc.lines) else "unknown"
    if not doc.lines:
        doc.warnings.append("The table has a header but no item rows.")
    return doc


def read_csv(path: str | Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f)]


def read_xlsx(path: str | Path) -> list[list[Any]]:
    """First sheet that holds a BOQ table; falls back to the first sheet. Needs openpyxl (not a hard dependency of the app)."""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("Reading Excel BOQs needs the openpyxl package. Install it with: pip install openpyxl") from None
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    first = None
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        first = first if first is not None else rows
        if _find_header(rows):
            return rows
    return first or []


def parse_file(path: str | Path) -> BoqDocument:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".csv", ".txt"):
        return parse_rows(read_csv(p))
    if ext in (".xlsx", ".xlsm"):
        return parse_rows(read_xlsx(p))
    if ext == ".xls":
        raise RuntimeError("Old .xls files are not read. Open the file in Excel, save it as .xlsx, and try again.")
    raise RuntimeError(f"Cannot read a BOQ from a {ext or 'file without an extension'}. Use .xlsx or .csv.")
