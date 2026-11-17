"""Master data for the BOQ engine, stored in SQLite (same tables as sql/postgres_schema.sql, minus the vector columns).

Fill it from the ERP's CSV or Excel export with the `import_*` methods. Each import reports the rows it rejected and why,
and never half-loads a bad row. Rates are ERP rates in INR; units are stored as canonical units (CUM, SQM, MT ...).
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import dbsetup
from . import uom as U
from .text import alias_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_master (
    company_id TEXT NOT NULL, erp_item_code TEXT NOT NULL, item_name TEXT NOT NULL, detailed_specification TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '', primary_uom TEXT NOT NULL, standard_base_rate REAL, is_composite INTEGER NOT NULL DEFAULT 0,
    sor_code TEXT, hsn_sac_code TEXT, gst_pct REAL, updated_at TEXT NOT NULL, PRIMARY KEY (company_id, erp_item_code));
CREATE INDEX IF NOT EXISTS item_master_sor ON item_master (company_id, sor_code);
CREATE TABLE IF NOT EXISTS activity_master (
    company_id TEXT NOT NULL, activity_code TEXT NOT NULL, description TEXT NOT NULL, uom TEXT NOT NULL,
    material_cost REAL NOT NULL DEFAULT 0, labor_cost REAL NOT NULL DEFAULT 0, machinery_cost REAL NOT NULL DEFAULT 0,
    overhead_cost REAL NOT NULL DEFAULT 0, wastage_pct REAL NOT NULL DEFAULT 0, erp_item_code TEXT,
    PRIMARY KEY (company_id, activity_code));
CREATE TABLE IF NOT EXISTS uom_master (alias TEXT PRIMARY KEY, canonical_uom TEXT NOT NULL, dimension TEXT NOT NULL, factor_to_base REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sor_master (
    source TEXT NOT NULL, sor_code TEXT NOT NULL, description TEXT NOT NULL, uom TEXT NOT NULL, rate REAL NOT NULL, state TEXT,
    PRIMARY KEY (source, sor_code));
CREATE TABLE IF NOT EXISTS location_index (
    state TEXT NOT NULL, district TEXT NOT NULL DEFAULT '', cost_index REAL NOT NULL DEFAULT 1, hill_carriage_pct REAL NOT NULL DEFAULT 0,
    royalty_per_cum REAL, note TEXT, PRIMARY KEY (state, district));
CREATE TABLE IF NOT EXISTS learned_alias (
    company_id TEXT NOT NULL, alias_key TEXT NOT NULL, uom TEXT NOT NULL, erp_item_code TEXT NOT NULL, raw_description TEXT,
    verified_by TEXT, created_at TEXT NOT NULL, PRIMARY KEY (company_id, alias_key, uom));
CREATE TABLE IF NOT EXISTS boq_document (
    boq_id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT, tender_key TEXT NOT NULL, source_name TEXT, rate_type TEXT NOT NULL,
    estimated_total REAL, warnings TEXT NOT NULL DEFAULT '[]', parsed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS boq_document_tender ON boq_document (tender_key);
CREATE TABLE IF NOT EXISTS bid_risk (tender_key TEXT PRIMARY KEY, findings TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boq_line (
    boq_id INTEGER NOT NULL, line_no INTEGER NOT NULL, sr TEXT, section TEXT, description TEXT NOT NULL, uom_raw TEXT, uom TEXT,
    qty REAL, est_rate REAL, est_amount REAL, sor_code TEXT, match_status TEXT, match_tier TEXT, confidence REAL, erp_item_code TEXT,
    candidates TEXT NOT NULL DEFAULT '[]', reason TEXT, manual_cost REAL, PRIMARY KEY (boq_id, line_no));
"""

DEFAULT_COMPANY = "default"


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    if not s or s in ("-", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""


def read_rows(source: str | Path | io.TextIOBase) -> list[dict]:
    """Read a CSV (path or open file) into dicts with lower-case, trimmed header names. Blank rows are dropped."""
    if isinstance(source, (str, Path)):
        with open(source, newline="", encoding="utf-8-sig") as f:
            return read_rows(f)
    out = []
    for r in csv.DictReader(source):
        row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in r.items() if k}
        if any(row.values()):
            out.append(row)
    return out


class MasterStore:
    def __init__(self, path: str = ":memory:", company_id: str = DEFAULT_COMPANY):
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.company = company_id
        self.db = sqlite3.connect(path, check_same_thread=False)
        dbsetup.tune(self.db, path)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            if "manual_cost" not in {r["name"] for r in self.db.execute("PRAGMA table_info(boq_line)")}:
                self.db.execute("ALTER TABLE boq_line ADD COLUMN manual_cost REAL")  # added after the first release
            self.db.executemany("INSERT OR IGNORE INTO uom_master (alias, canonical_uom, dimension, factor_to_base) VALUES (?,?,?,?)",
                                [(a, c, U.UNITS[c][0], U.UNITS[c][1]) for a, c in U.all_aliases().items()])
            self.db.commit()

    # ---- imports: each returns {"added": n, "updated": n, "errors": ["row 4: ..."]} ---------------------------------

    def _import(self, rows: Iterable[dict], build, sql: str, key_of) -> dict[str, Any]:
        added = updated = 0
        errors: list[str] = []
        with self.lock:
            for n, row in enumerate(rows, start=2):  # row 1 is the header
                try:
                    values = build(row)
                except ValueError as e:
                    errors.append(f"row {n}: {e}")
                    continue
                exists = self.db.execute(*key_of(values)).fetchone()
                self.db.execute(sql, values)
                updated, added = (updated + 1, added) if exists else (updated, added + 1)
            self.db.commit()
        return {"added": added, "updated": updated, "errors": errors}

    def import_items(self, rows: Iterable[dict]) -> dict[str, Any]:
        """Item master rows: erp_item_code, item_name, primary_uom are required. Also: detailed_specification, category,
        standard_base_rate, sor_code, hsn_sac_code, gst_pct, is_composite (yes/no)."""
        now = datetime.now().isoformat(timespec="seconds")

        def build(r: dict) -> tuple:
            code, name = _pick(r, "erp_item_code", "item_code", "code"), _pick(r, "item_name", "name")
            canon = U.normalise_uom(_pick(r, "primary_uom", "uom", "unit"))
            if not code or not name:
                raise ValueError("item code and item name are both required")
            if not canon:
                raise ValueError(f"unit '{_pick(r, 'primary_uom', 'uom', 'unit')}' is not recognised")
            rate_raw = _pick(r, "standard_base_rate", "base_rate", "rate")
            rate = _num(rate_raw)
            if rate_raw and rate is None:
                raise ValueError(f"rate '{rate_raw}' is not a number")
            comp = _pick(r, "is_composite", "composite").lower() in ("1", "yes", "y", "true")
            return (self.company, code, name, _pick(r, "detailed_specification", "specification", "spec", "description"),
                    _pick(r, "category"), canon, rate, int(comp), _pick(r, "sor_code") or None, _pick(r, "hsn_sac_code", "hsn", "hsn_code") or None,
                    _num(_pick(r, "gst_pct", "gst")), now)

        return self._import(rows, build, "INSERT OR REPLACE INTO item_master (company_id, erp_item_code, item_name, detailed_specification, category, "
                            "primary_uom, standard_base_rate, is_composite, sor_code, hsn_sac_code, gst_pct, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            lambda v: ("SELECT 1 FROM item_master WHERE company_id=? AND erp_item_code=?", (v[0], v[1])))

    def import_sor(self, rows: Iterable[dict]) -> dict[str, Any]:
        """SOR / DSR rows: source (e.g. 'CPWD DSR 2023'), sor_code, description, uom, rate; optional state."""
        def build(r: dict) -> tuple:
            src, code, desc = _pick(r, "source", "schedule"), _pick(r, "sor_code", "code", "item_no"), _pick(r, "description", "item")
            canon, rate = U.normalise_uom(_pick(r, "uom", "unit")), _num(_pick(r, "rate"))
            if not (src and code and desc):
                raise ValueError("source, code and description are all required")
            if not canon:
                raise ValueError(f"unit '{_pick(r, 'uom', 'unit')}' is not recognised")
            if rate is None:
                raise ValueError("rate is missing or not a number")
            return (src, code, desc, canon, rate, _pick(r, "state") or None)

        return self._import(rows, build, "INSERT OR REPLACE INTO sor_master (source, sor_code, description, uom, rate, state) VALUES (?,?,?,?,?,?)",
                            lambda v: ("SELECT 1 FROM sor_master WHERE source=? AND sor_code=?", (v[0], v[1])))

    def import_activities(self, rows: Iterable[dict]) -> dict[str, Any]:
        """Activity / composite rate rows: activity_code, description, uom, and material_cost, labor_cost, machinery_cost,
        overhead_cost, wastage_pct (all per unit of the activity), optional erp_item_code."""
        def build(r: dict) -> tuple:
            code, desc, canon = _pick(r, "activity_code", "code"), _pick(r, "description"), U.normalise_uom(_pick(r, "uom", "unit"))
            if not code or not desc:
                raise ValueError("activity code and description are required")
            if not canon:
                raise ValueError(f"unit '{_pick(r, 'uom', 'unit')}' is not recognised")
            costs = []
            for f in ("material_cost", "labor_cost", "machinery_cost", "overhead_cost", "wastage_pct"):
                raw = _pick(r, f)
                v = _num(raw) if raw else 0.0
                if v is None or v < 0:
                    raise ValueError(f"{f} '{raw}' must be a number of zero or more")
                costs.append(v)
            return (self.company, code, desc, canon, *costs, _pick(r, "erp_item_code") or None)

        return self._import(rows, build, "INSERT OR REPLACE INTO activity_master (company_id, activity_code, description, uom, material_cost, labor_cost, "
                            "machinery_cost, overhead_cost, wastage_pct, erp_item_code) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            lambda v: ("SELECT 1 FROM activity_master WHERE company_id=? AND activity_code=?", (v[0], v[1])))

    def import_locations(self, rows: Iterable[dict]) -> dict[str, Any]:
        """Location cost index rows: state, optional district, cost_index (1.00 = base), hill_carriage_pct, royalty_per_cum, note."""
        def build(r: dict) -> tuple:
            state = _pick(r, "state")
            idx = _num(_pick(r, "cost_index")) if _pick(r, "cost_index") else 1.0
            hill = _num(_pick(r, "hill_carriage_pct")) if _pick(r, "hill_carriage_pct") else 0.0
            if not state:
                raise ValueError("state is required")
            if idx is None or idx <= 0 or hill is None or hill < 0:
                raise ValueError("cost_index must be above 0 and hill_carriage_pct must be 0 or more")
            return (state, _pick(r, "district"), idx, hill, _num(_pick(r, "royalty_per_cum")), _pick(r, "note") or None)

        return self._import(rows, build, "INSERT OR REPLACE INTO location_index (state, district, cost_index, hill_carriage_pct, royalty_per_cum, note) "
                            "VALUES (?,?,?,?,?,?)", lambda v: ("SELECT 1 FROM location_index WHERE state=? AND district=?", (v[0], v[1])))

    # ---- reads -------------------------------------------------------------------------------------------------------

    def items(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM item_master WHERE company_id=? ORDER BY erp_item_code", (self.company,))]

    def sor_rows(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM sor_master ORDER BY source, sor_code")]

    def activity(self, erp_item_code: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM activity_master WHERE company_id=? AND erp_item_code=?", (self.company, erp_item_code)).fetchone()
            return dict(r) if r else None

    def location(self, state: str, district: str = "") -> dict | None:
        with self.lock:
            for d in ([district] if district else []) + [""]:
                r = self.db.execute("SELECT * FROM location_index WHERE lower(state)=lower(?) AND lower(district)=lower(?)", (state, d)).fetchone()
                if r:
                    return dict(r)
        return None

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("item_master", "activity_master", "sor_master", "location_index", "learned_alias")}

    # ---- learned aliases ---------------------------------------------------------------------------------------------

    def add_alias(self, raw_description: str, uom: str | None, erp_item_code: str, verified_by: str = "") -> bool:
        canon = U.normalise_uom(uom)
        key = alias_key(raw_description)
        if not canon or not key:
            return False
        with self.lock:
            if not self.db.execute("SELECT 1 FROM item_master WHERE company_id=? AND erp_item_code=?", (self.company, erp_item_code)).fetchone():
                return False  # an alias must point at an item that exists
            self.db.execute("INSERT OR REPLACE INTO learned_alias (company_id, alias_key, uom, erp_item_code, raw_description, verified_by, created_at) "
                            "VALUES (?,?,?,?,?,?,?)", (self.company, key, canon, erp_item_code, raw_description[:300], verified_by,
                                                      datetime.now().isoformat(timespec="seconds")))
            self.db.commit()
        return True

    def find_alias(self, description: str, uom: str | None) -> str | None:
        canon = U.normalise_uom(uom)
        key = alias_key(description)
        if not canon or not key:
            return None
        with self.lock:
            r = self.db.execute("SELECT erp_item_code FROM learned_alias WHERE company_id=? AND alias_key=? AND uom=?", (self.company, key, canon)).fetchone()
        return r["erp_item_code"] if r else None

    # ---- parsed BOQs -------------------------------------------------------------------------------------------------

    def save_boq(self, tender_key: str, source_name: str, rate_type: str, estimated_total: float | None, warnings: list[str], rows: list[dict]) -> int:
        """Store one parsed BOQ and its match results. `rows` hold the line fields plus match fields (see boq_line)."""
        with self.lock:
            cur = self.db.execute("INSERT INTO boq_document (company_id, tender_key, source_name, rate_type, estimated_total, warnings, parsed_at) "
                                  "VALUES (?,?,?,?,?,?,?)", (self.company, tender_key, source_name, rate_type, estimated_total, json.dumps(warnings),
                                                             datetime.now().isoformat(timespec="seconds")))
            boq_id = int(cur.lastrowid or 0)
            for n, r in enumerate(rows, start=1):
                self.db.execute("INSERT INTO boq_line (boq_id, line_no, sr, section, description, uom_raw, uom, qty, est_rate, est_amount, sor_code, match_status, match_tier, confidence, erp_item_code, candidates, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (boq_id, n, r.get("sr"), r.get("section"), r.get("description", ""), r.get("uom_raw"), r.get("uom"), r.get("qty"),
                                 r.get("est_rate"), r.get("est_amount"), r.get("sor_code"), r.get("match_status"), r.get("match_tier"),
                                 r.get("confidence"), r.get("erp_item_code"), json.dumps(r.get("candidates") or []), r.get("reason")))
            self.db.commit()
        return boq_id

    def get_boq(self, boq_id: int) -> dict | None:
        with self.lock:
            d = self.db.execute("SELECT * FROM boq_document WHERE boq_id=?", (boq_id,)).fetchone()
            if not d:
                return None
            lines = [dict(r) for r in self.db.execute("SELECT * FROM boq_line WHERE boq_id=? ORDER BY line_no", (boq_id,))]
        for ln in lines:
            ln["candidates"] = json.loads(ln["candidates"] or "[]")
        out = dict(d)
        out["warnings"] = json.loads(out["warnings"] or "[]")
        out["lines"] = lines
        return out

    def latest_boq_id(self, tender_key: str) -> int | None:
        with self.lock:
            r = self.db.execute("SELECT boq_id FROM boq_document WHERE company_id=? AND tender_key=? ORDER BY boq_id DESC LIMIT 1",
                                (self.company, tender_key)).fetchone()
        return int(r["boq_id"]) if r else None

    def delete_boqs(self, tender_key: str) -> None:
        """Drop every saved BOQ of a tender (a new upload replaces the old one)."""
        with self.lock:
            ids = [r["boq_id"] for r in self.db.execute("SELECT boq_id FROM boq_document WHERE company_id=? AND tender_key=?", (self.company, tender_key))]
            for i in ids:
                self.db.execute("DELETE FROM boq_line WHERE boq_id=?", (i,))
                self.db.execute("DELETE FROM boq_document WHERE boq_id=?", (i,))
            self.db.commit()

    def set_line_cost(self, boq_id: int, line_no: int, cost: float | None) -> bool:
        with self.lock:
            cur = self.db.execute("UPDATE boq_line SET manual_cost = ? WHERE boq_id = ? AND line_no = ?", (cost, boq_id, line_no))
            self.db.commit()
            return cur.rowcount == 1

    def update_line_match(self, boq_id: int, line_no: int, status: str, tier: str, confidence: float, item_code: str | None, reason: str) -> bool:
        with self.lock:
            cur = self.db.execute("UPDATE boq_line SET match_status=?, match_tier=?, confidence=?, erp_item_code=?, reason=? WHERE boq_id=? AND line_no=?",
                                  (status, tier, confidence, item_code, reason, boq_id, line_no))
            self.db.commit()
            return cur.rowcount == 1

    def item(self, erp_item_code: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM item_master WHERE company_id=? AND erp_item_code=?", (self.company, erp_item_code)).fetchone()
        return dict(r) if r else None

    # ---- risk findings saved per tender -------------------------------------------------------------------------------

    def save_risk(self, tender_key: str, findings: dict) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO bid_risk (tender_key, findings, updated_at) VALUES (?,?,?)",
                            (tender_key, json.dumps(findings), datetime.now().isoformat(timespec="seconds")))
            self.db.commit()

    def get_risk(self, tender_key: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT findings, updated_at FROM bid_risk WHERE tender_key=?", (tender_key,)).fetchone()
        return {**json.loads(r["findings"]), "updated_at": r["updated_at"]} if r else None

    def close(self) -> None:
        with self.lock:
            self.db.close()
