"""What the web app calls: upload a BOQ, match it, confirm matches, cost it, and run the risk simulator.

Kept free of FastAPI so it can be tested directly. Every problem a person can fix raises BoqError with a plain message,
which the API turns into a 422. AI problems stay as ai.NotConfigured / ai.AIError for the API to map.
"""
from __future__ import annotations

import base64
import dataclasses
import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from . import match as M
from . import parse, rates, risk
from .masters import MasterStore, read_rows

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_CSV_CHARS = 5_000_000
MAX_VERIFY_CALLS = 300          # Claude checks per BOQ upload; beyond this, lines fall back to rule-based matching
KINDS = {"items": "import_items", "sor": "import_sor", "activities": "import_activities", "locations": "import_locations"}


class BoqError(Exception):
    pass


def default_boq_path(state_path: str) -> str:
    """Next to the main database on the same persistent volume; in memory for the in-memory test database."""
    if os.environ.get("BOQ_DB_PATH"):
        return os.environ["BOQ_DB_PATH"]
    if state_path == ":memory:":
        return ":memory:"
    return str(Path(state_path).expanduser().parent / "boq.db")


class _Capped:
    """Wraps a verifier so one upload cannot make an unbounded number of Claude calls."""

    def __init__(self, inner: Any, cap: int):
        self.inner, self.cap, self.used = inner, cap, 0

    def verify(self, line: dict, candidates: list[dict]) -> dict | None:
        if self.used >= self.cap:
            return None
        self.used += 1
        return self.inner.verify(line, candidates)


def _decode(file_base64: str) -> bytes:
    try:
        raw = base64.b64decode(file_base64, validate=True)
    except ValueError:
        raise BoqError("That file could not be read.") from None
    if not raw:
        raise BoqError("That file is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise BoqError("That file is over 10 MB.")
    return raw


class BoqService:
    def __init__(self, store: MasterStore, verifier_factory: Callable[[], Any] | None = None):
        self.store = store
        self._verifier_factory = verifier_factory

    # ---- catalogue ------------------------------------------------------------------------------------------------

    def catalogue(self, with_items: bool = True) -> dict:
        out: dict[str, Any] = {"counts": self.store.counts()}
        if with_items:
            out["items"] = [{"code": i["erp_item_code"], "name": i["item_name"], "uom": i["primary_uom"], "rate": i["standard_base_rate"],
                             "composite": bool(i["is_composite"])} for i in self.store.items()][:3000]
        return out

    def import_catalogue(self, kind: str, csv_text: str) -> dict:
        if kind not in KINDS:
            raise BoqError(f"Unknown list '{kind}'. Use one of: {', '.join(KINDS)}.")
        if not csv_text.strip():
            raise BoqError("Paste or choose a CSV file first.")
        if len(csv_text) > MAX_CSV_CHARS:
            raise BoqError("That file is too large. Split it into parts of under 5 MB.")
        rows = read_rows(io.StringIO(csv_text))
        if not rows:
            raise BoqError("No rows found. The first line must be the column names.")
        res = getattr(self.store, KINDS[kind])(rows)
        res["errors"] = res["errors"][:50]
        return {"result": res, "counts": self.store.counts()}

    # ---- upload and match -------------------------------------------------------------------------------------------

    def analyse(self, tender_key: str, filename: str, file_base64: str) -> dict:
        raw = _decode(file_base64)
        suffix = Path(filename or "").suffix.lower() or ".csv"
        with tempfile.TemporaryDirectory(prefix="tendai-boq-") as d:
            path = Path(d) / f"upload{suffix}"
            path.write_bytes(raw)
            try:
                doc = parse.parse_file(path)
            except RuntimeError as e:
                raise BoqError(str(e)) from None
            except Exception:
                raise BoqError("That file could not be read as a BOQ. Check that it is a valid .xlsx, .xlsm, .xls or .csv file.") from None
        if not doc.lines:
            raise BoqError(doc.warnings[0] if doc.warnings else "No items were found in that file.")
        inner = self._verifier_factory() if self._verifier_factory else None
        verifier = _Capped(inner, MAX_VERIFY_CALLS) if inner else None
        results = M.Matcher.from_store(self.store, verifier).match_all(doc.lines)
        rows = doc.as_rows()
        for row, r in zip(rows, results):
            row.update(r.to_row())
        warnings = list(doc.warnings)
        if verifier and verifier.used >= MAX_VERIFY_CALLS:
            warnings.append(f"Claude checked the first {MAX_VERIFY_CALLS} lines only; the rest were matched by rules and need review.")
        if not self.store.items():
            warnings.append("The item catalogue is empty, so nothing could be matched. Load your item list, then upload the BOQ again.")
        self.store.delete_boqs(tender_key)
        self.store.save_boq(tender_key, (filename or "BOQ")[:200], doc.rate_type, doc.estimated_total, warnings, rows)
        return self.view(tender_key)

    # ---- confirm a match --------------------------------------------------------------------------------------------

    def confirm(self, tender_key: str, line_no: int, item_code: str | None, verified_by: str = "") -> dict:
        boq_id = self.store.latest_boq_id(tender_key)
        boq = self.store.get_boq(boq_id) if boq_id else None
        line = next((ln for ln in boq["lines"] if ln["line_no"] == line_no), None) if boq else None
        if not boq or not line:
            raise BoqError("That BOQ line no longer exists. Reload the page.")
        if not item_code:
            self.store.update_line_match(boq["boq_id"], line_no, M.NEW_NON_SOR_ITEM, "manual", 0.0, None, "You marked this as not in your catalogue.")
            return self.view(tender_key)
        item = self.store.item(item_code)
        if not item:
            raise BoqError("That item is not in your catalogue.")
        if line["uom"] and item["primary_uom"] != line["uom"]:
            raise BoqError(f"That item is priced per {item['primary_uom']} but the BOQ line is per {line['uom']}.")
        self.store.update_line_match(boq["boq_id"], line_no, M.AUTO_MATCHED, "confirmed", 1.0, item_code, "Confirmed by you.")
        self.store.add_alias(line["description"], line["uom"], item_code, verified_by)
        return self.view(tender_key)

    # ---- the view the page shows --------------------------------------------------------------------------------------

    def _lines_and_results(self, boq: dict) -> tuple[list[Any], list[M.MatchResult]]:
        sor = M.Matcher.from_store(self.store)
        lines, results = [], []
        for i, ln in enumerate(boq["lines"]):
            lines.append(SimpleNamespace(row=ln["line_no"], description=ln["description"], uom=ln["uom"], uom_raw=ln["uom_raw"], qty=ln["qty"],
                                         rate=ln["est_rate"], amount=ln["est_amount"], sor_code=ln["sor_code"]))
            results.append(M.MatchResult(i, ln["match_status"] or M.NEW_NON_SOR_ITEM, ln["match_tier"] or "none", ln["confidence"] or 0.0, ln["erp_item_code"],
                                         ln["reason"] or "", ln["candidates"], sor.sor_reference(ln["sor_code"], ln["uom"])))
        return lines, results

    def price(self, tender_key: str, include_review: bool = False, state: str = "") -> dict | None:
        boq_id = self.store.latest_boq_id(tender_key)
        boq = self.store.get_boq(boq_id) if boq_id else None
        if not boq:
            return None
        lines, results = self._lines_and_results(boq)
        loc = self.store.location(state) if state else None
        out = rates.price_boq(lines, results, self.store.items(), self.store.activity, loc, include_review)
        out["lines"] = {boq["lines"][c.index]["line_no"]: dataclasses.asdict(c) for c in out["lines"]}
        out["location"] = loc
        out["include_review"] = include_review
        return out

    def view(self, tender_key: str, include_review: bool = False) -> dict:
        boq_id = self.store.latest_boq_id(tender_key)
        boq = self.store.get_boq(boq_id) if boq_id else None
        base = {"catalogue": self.store.counts(), "ai": bool(self._verifier_factory and self._verifier_factory())}
        if not boq:
            return {**base, "boq": None, "pricing": None}
        saved = self.store.get_risk(tender_key)
        state = (saved or {}).get("facts", {}).get("state", "") if saved else ""
        pricing = self.price(tender_key, include_review, state) or {}
        costs = pricing.pop("lines", {})
        names = {i["erp_item_code"]: i["item_name"] for i in self.store.items()}
        counts = {M.AUTO_MATCHED: 0, M.NEEDS_REVIEW: 0, M.NEW_NON_SOR_ITEM: 0}
        lines = []
        for ln in boq["lines"]:
            counts[ln["match_status"]] = counts.get(ln["match_status"], 0) + 1
            lines.append({"line_no": ln["line_no"], "sr": ln["sr"], "section": ln["section"], "description": ln["description"], "uom": ln["uom"],
                          "uom_raw": ln["uom_raw"], "qty": ln["qty"], "est_rate": ln["est_rate"], "est_amount": ln["est_amount"],
                          "status": ln["match_status"], "tier": ln["match_tier"], "confidence": ln["confidence"], "item_code": ln["erp_item_code"],
                          "item_name": names.get(ln["erp_item_code"] or "", ""), "candidates": ln["candidates"], "reason": ln["reason"],
                          "cost": costs.get(ln["line_no"])})
        return {**base, "pricing": pricing,
                "boq": {"id": boq["boq_id"], "source_name": boq["source_name"], "rate_type": boq["rate_type"], "estimated_total": boq["estimated_total"],
                        "warnings": boq["warnings"], "parsed_at": boq["parsed_at"], "counts": counts, "lines": lines}}

    # ---- risk ---------------------------------------------------------------------------------------------------------

    def read_risks(self, tender_key: str, tender: dict, text: str = "", pdf_base64: str = "") -> dict:
        """Have Claude read the tender text or PDF for the 12 risk drivers and save the findings. Raises ai errors as they are."""
        findings = risk.extract_risks(tender, text, pdf_base64)
        self.store.save_risk(tender_key, findings)
        return self.risks(tender_key)

    def risks(self, tender_key: str) -> dict:
        saved = self.store.get_risk(tender_key)
        if not saved:
            return {"findings": None, "tags": [{"tag": t["tag"], "label": t["label"]} for t in risk.RISK_TAGS]}
        return {"findings": saved, "tags": [{"tag": t["tag"], "label": t["label"]} for t in risk.RISK_TAGS]}

    def simulate(self, tender_key: str, estimate: float | None = None, exposure: dict[str, float] | None = None, l1_price: float | None = None,
                 quoted_pct: float | None = None, include_review: bool = False) -> dict:
        saved = self.store.get_risk(tender_key)
        if not saved:
            raise BoqError("Read the tender document for risks first.")
        boq_id = self.store.latest_boq_id(tender_key)
        boq = self.store.get_boq(boq_id) if boq_id else None
        if not boq:
            raise BoqError("Upload the BOQ first: the simulator starts from your costed BOQ.")
        pricing = self.price(tender_key, include_review, saved.get("facts", {}).get("state", ""))
        assert pricing is not None
        lines, _ = self._lines_and_results(boq)
        findings = {"risks": saved["risks"], "facts": saved["facts"]}
        sim = risk.simulate(pricing["projected_cost"], findings, estimate or boq["estimated_total"], lines, exposure, l1_price, quoted_pct)
        sim["coverage_pct"] = pricing["coverage_pct"]
        sim["priced_lines"], sim["total_lines"] = pricing["priced_lines"], pricing["total_lines"]
        if pricing["coverage_pct"] < 50:
            sim["warnings"] = [f"Only {pricing['coverage_pct']:.0f}% of the BOQ value is priced from your catalogue. The rest is carried at the buyer's "
                               "estimate, so treat this as a rough range."] + sim["warnings"]
        return sim

    @classmethod
    def open(cls, state_path: str, verifier_factory: Callable[[], Any] | None = None) -> "BoqService":
        return cls(MasterStore(default_boq_path(state_path)), verifier_factory)
