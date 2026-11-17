"""BOQ engine: units, parsing, master data, matching, rate costing, risk extraction and the margin simulator. Claude is replaced by fakes."""
import csv
from datetime import date
from pathlib import Path

import pytest

from tender_digest import ai
from tender_digest.boq import match, parse, rates, risk, uom
from tender_digest.boq.masters import MasterStore, read_rows
from tender_digest.boq.text import normalise, spec_conflicts, specs

FIX = Path(__file__).parent / "fixtures"


def line(desc, unit="Cum", qty=100.0, rate=1000.0, code=None, row=1):
    return parse.BoqLine(row=row, sr="1", section="", description=desc, uom_raw=unit, uom=uom.normalise_uom(unit), qty=qty, rate=rate,
                         amount=None if qty is None or rate is None else qty * rate, sor_code=code)


@pytest.fixture()
def store():
    s = MasterStore(":memory:")
    res = s.import_items(read_rows(FIX / "erp_items.csv"))
    assert res["errors"] == []
    return s


# ---- units ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,canon", [("Cum", "CUM"), ("Cu.m", "CUM"), ("M3", "CUM"), ("Sq. m", "SQM"), ("M2", "SQM"), ("Rmt", "RMT"), ("R.M.", "RMT"),
                                       ("Tonne", "MT"), ("MT", "MT"), ("Kgs", "KG"), ("Nos.", "NOS"), ("Lump sum", "LS"), ("Sft", "SFT"), ("", None), ("furlong", None), (None, None)])
def test_units_are_normalised(raw, canon):
    assert uom.normalise_uom(raw) == canon


def test_conversions_match_the_blueprint():
    assert uom.convert(1, "MT", "KG") == pytest.approx(1000)
    assert uom.convert(100, "SFT", "SQM") == pytest.approx(9.29, abs=0.01)
    assert uom.convert(1, "CUM", "SQM") is None and uom.convert(1, "CUM", "ZZ") is None


# ---- text and specs -----------------------------------------------------------------------------------------------------

def test_descriptions_are_normalised_and_specs_compared():
    assert normalise("R.C.C. M-30 in sub-structure") == "rcc m30 sub structure"
    assert specs("RCC M30 with Fe 500D steel and NP4 pipe, 1200 mm dia, VG-30")["concrete grade"] == {"M30"}
    assert spec_conflicts("RCC M30 foundation", "RCC M25 foundation") == ["concrete grade: M30 vs M25"]
    assert spec_conflicts("WMM 250 mm thick", "WMM 250 mm thick laid") == []
    assert spec_conflicts("RCC foundation", "RCC M25 foundation") == []  # a spec stated on one side only is not a clash


# ---- parsing ------------------------------------------------------------------------------------------------------------

def test_sample_boq_parses_completely():
    doc = parse.parse_file(FIX / "boq_sample.csv")
    assert len(doc.lines) == 26
    assert doc.rate_type == "percentage_rate"
    assert doc.estimated_total == 128640000.00
    assert doc.warnings == []
    assert {ln.section for ln in doc.lines} >= {"A. Earthwork and Site Clearance", "D. Cross Drainage and Minor Bridges"}
    assert all(ln.uom for ln in doc.lines)          # every unit in the sample is recognised
    assert doc.lines[0].row == 7 and doc.lines[0].uom == "SQM"


def test_item_rate_boq_with_blank_rates_and_arithmetic_check():
    rows = [["Item rate BOQ"], ["Sr", "Description", "Unit", "Qty", "Rate", "Amount"], [1, "Excavation", "Cum", "10", "", ""],
            [2, "PCC M15", "Cum", "5", "5,000.00", "30000"], [3, "Mystery", "furlong", "2", "", ""], ["", "Total", "", "", "", ""]]
    doc = parse.parse_rows(rows)
    assert doc.rate_type == "item_rate" and len(doc.lines) == 3
    assert "Row 4: quantity x rate = 25,000.00 but the amount column says 30,000.00." in doc.warnings   # 5 x 5000, sheet says 30000
    assert any("furlong" in w for w in doc.warnings)
    assert doc.estimated_total is None


def test_sheet_without_a_table_is_reported_not_crashed():
    doc = parse.parse_rows([["just a notice"], ["nothing here"]])
    assert doc.lines == [] and "No BOQ table" in doc.warnings[0]


def test_numbers_and_codes():
    assert parse.number("1,02,41,420.00") == 10241420.0 and parse.number("(250)") == -250 and parse.number("-") is None and parse.number("Rs. 5") == 5
    doc = parse.parse_rows([["Sr", "Description", "Unit", "Qty", "Rate"], [1, "Brick work as per DSR item 16.4 in 1:4 mortar", "Cum", 10, 100]])
    assert doc.lines[0].sor_code == "16.4"


def test_excel_roundtrip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    with open(FIX / "boq_sample.csv", encoding="utf-8") as f:
        for r in csv.reader(f):
            ws.append(r)
    wb.save(tmp_path / "b.xlsx")
    assert len(parse.parse_file(tmp_path / "b.xlsx").lines) == 26
    # portals often rename files: an .xlsx called .xls, and macro-enabled .xlsm, must both read
    (tmp_path / "renamed.xls").write_bytes((tmp_path / "b.xlsx").read_bytes())
    assert len(parse.parse_file(tmp_path / "renamed.xls").lines) == 26
    (tmp_path / "macro.xlsm").write_bytes((tmp_path / "b.xlsx").read_bytes())
    assert len(parse.parse_file(tmp_path / "macro.xlsm").lines) == 26


def test_html_table_saved_as_xls(tmp_path):
    html = ("<html><body><table><tr><th>Sr</th><th>Description</th><th>Unit</th><th>Quantity</th><th>Rate</th></tr>"
            "<tr><td>1</td><td>Earthwork in excavation</td><td>cum</td><td>100</td><td></td></tr></table></body></html>")
    (tmp_path / "boq.xls").write_text(html, encoding="utf-8")
    doc = parse.parse_file(tmp_path / "boq.xls")
    assert len(doc.lines) == 1 and doc.lines[0].qty == 100


def test_unknown_extension_is_refused(tmp_path):
    (tmp_path / "x.doc").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="xlsx"):
        parse.parse_file(tmp_path / "x.doc")


# ---- master data ----------------------------------------------------------------------------------------------------------

def test_item_import_reports_bad_rows_and_upserts():
    s = MasterStore(":memory:")
    rows = [{"erp_item_code": "A", "item_name": "Item A", "primary_uom": "Cu.m", "standard_base_rate": "1,250.50"},
            {"erp_item_code": "B", "item_name": "", "primary_uom": "Cum"},
            {"erp_item_code": "C", "item_name": "Item C", "primary_uom": "parsec"},
            {"erp_item_code": "D", "item_name": "Item D", "primary_uom": "Cum", "standard_base_rate": "abc"}]
    r = s.import_items(rows)
    assert (r["added"], r["updated"]) == (1, 0) and len(r["errors"]) == 3
    assert r["errors"][0].startswith("row 3:") and "parsec" in r["errors"][1] and "not a number" in r["errors"][2]
    assert s.items()[0]["primary_uom"] == "CUM" and s.items()[0]["standard_base_rate"] == 1250.5
    assert s.import_items([{"erp_item_code": "A", "item_name": "Item A v2", "primary_uom": "Cum"}]) == {"added": 0, "updated": 1, "errors": []}


def test_other_masters_import_and_lookup():
    s = MasterStore(":memory:")
    assert s.import_sor([{"source": "CPWD DSR 2023", "sor_code": "16.4", "description": "RCC M30", "uom": "Cum", "rate": "7,000"}])["added"] == 1
    assert s.import_sor([{"source": "", "sor_code": "x", "description": "y", "uom": "Cum", "rate": "1"}])["errors"]
    assert s.import_activities([{"activity_code": "RCC30", "description": "RCC M30", "uom": "Cum", "material_cost": "5000", "labor_cost": "900",
                                 "machinery_cost": "300", "overhead_cost": "200", "wastage_pct": "3", "erp_item_code": "RCC-30"}])["added"] == 1
    assert s.import_locations([{"state": "Himachal Pradesh", "cost_index": "1.12", "hill_carriage_pct": "20"}])["added"] == 1
    assert s.import_locations([{"state": "Goa", "cost_index": "0"}])["errors"]
    assert s.location("himachal pradesh", "Shimla")["hill_carriage_pct"] == 20 and s.location("Nowhere") is None
    assert s.activity("RCC-30")["wastage_pct"] == 3
    assert s.counts()["sor_master"] == 1


def test_alias_must_point_at_a_real_item(store):
    assert store.add_alias("RCC in M30 for abutments", "cum", "RCC-30", "ravi") is True
    assert store.find_alias("RCC in M30 for abutments", "Cum") == "RCC-30"
    assert store.find_alias("RCC in M30 for abutments", "Sqm") is None
    assert store.add_alias("anything", "cum", "NO-SUCH-ITEM") is False and store.add_alias("anything", "furlong", "RCC-30") is False


def test_boq_is_saved_and_read_back(store):
    doc = parse.parse_file(FIX / "boq_sample.csv")
    rows = doc.as_rows()
    rows[0].update(match_status="AUTO_MATCHED", match_tier="alias", confidence=1.0, erp_item_code="X", candidates=[{"code": "X"}], reason="r")
    bid = store.save_boq("maha/T1", "boq_sample.csv", doc.rate_type, doc.estimated_total, doc.warnings, rows)
    got = store.get_boq(bid)
    assert got["rate_type"] == "percentage_rate" and len(got["lines"]) == 26 and got["lines"][0]["candidates"] == [{"code": "X"}]
    assert store.get_boq(999) is None


# ---- matching -------------------------------------------------------------------------------------------------------------

def test_exact_sor_code_matches_without_claude(store):
    m = match.Matcher.from_store(store)
    r = m.match_line(line("Providing RCC for bridge abutments as per DSR item 16.4", code="16.4"))
    assert (r.status, r.tier, r.item_code, r.confidence) == (match.AUTO_MATCHED, "sor_code", "RCC-30", 0.98)


def test_sor_code_with_a_different_unit_does_not_auto_match(store):
    r = match.Matcher.from_store(store).match_line(line("Some RCC wall", unit="Sqm", code="16.4"))
    assert r.status == match.NEW_NON_SOR_ITEM and r.tier == "none"     # nothing in the catalogue is priced per SQM


def test_unit_filter_keeps_other_units_out(store):
    r = match.Matcher.from_store(store).match_line(line("TMT steel Fe 500D bars", unit="Cum"))
    assert r.item_code is None and all(c["uom"] == "CUM" for c in r.candidates)


def test_clashing_grade_is_never_offered(store):
    r = match.Matcher.from_store(store).match_line(line("RCC M35 in superstructure deck slab"))
    assert r.item_code == "RCC-35" and all(not c["conflicts"] for c in r.candidates)
    r = match.Matcher.from_store(store).match_line(line("RCC M40 in superstructure"))
    assert r.status == match.NEW_NON_SOR_ITEM and "concrete grade" in r.reason


def test_thickness_clash_picks_the_right_wmm(store):
    r = match.Matcher.from_store(store).match_line(line("Wet mix macadam 250 mm thick laid with paver"))
    assert r.item_code == "WMM-250"
    assert "WMM-150" not in [c["code"] for c in r.candidates]


def test_text_similarity_alone_never_auto_matches(store):
    r = match.Matcher.from_store(store).match_line(line("Granular sub-base Grading II, 200 mm thick, laid and compacted"))
    assert r.item_code == "GSB-01" and r.tier == "semantic_rules"
    assert r.status == match.NEEDS_REVIEW and r.confidence < match.AUTO_MIN and "Not checked by Claude" in r.reason


class FakeVerifier:
    def __init__(self, verdict):
        self.verdict, self.seen = verdict, []

    def verify(self, ln, candidates):
        self.seen.append((ln, candidates))
        return self.verdict


def test_verifier_decides_the_status(store):
    ln = line("Granular sub-base Grading II, 200 mm thick, laid and compacted")
    v = FakeVerifier({"choice": 1, "confidence": 0.95, "reason": "Same grading and thickness."})
    r = match.Matcher.from_store(store, v).match_line(ln)
    assert (r.status, r.tier, r.item_code) == (match.AUTO_MATCHED, "semantic_llm", "GSB-01") and len(v.seen[0][1]) <= 3
    r = match.Matcher.from_store(store, FakeVerifier({"choice": 1, "confidence": 0.8, "reason": "Spec missing."})).match_line(ln)
    assert r.status == match.NEEDS_REVIEW
    r = match.Matcher.from_store(store, FakeVerifier({"choice": None, "confidence": 0.9, "reason": "Different item."})).match_line(ln)
    assert r.status == match.NEW_NON_SOR_ITEM and r.item_code is None and r.confidence < match.REVIEW_MIN
    r = match.Matcher.from_store(store, FakeVerifier({"choice": 1, "confidence": 0.4, "reason": "Doubtful."})).match_line(ln)
    assert r.status == match.NEW_NON_SOR_ITEM and r.item_code is None


def test_failed_verifier_falls_back_to_rules(store):
    r = match.Matcher.from_store(store, FakeVerifier(None)).match_line(line("Granular sub-base Grading II, 200 mm thick, laid and compacted"))
    assert r.tier == "semantic_rules"


def test_learned_alias_skips_everything_else(store):
    ln = line("Supply of fancy bridge concrete, grade thirty")
    m = match.Matcher.from_store(store)
    assert m.match_line(ln).status != match.AUTO_MATCHED
    assert match.learn(store, ln, "RCC-30", "ravi")
    m = match.Matcher.from_store(store, FakeVerifier({"choice": None, "confidence": 0, "reason": ""}))
    r = m.match_line(ln)
    assert (r.status, r.tier, r.item_code, r.confidence) == (match.AUTO_MATCHED, "alias", "RCC-30", 1.0)


def test_lump_sum_and_unknown_unit(store):
    m = match.Matcher.from_store(store)
    assert m.match_line(line("Quality control laboratory (lump sum)", unit="LS")).status == match.NEW_NON_SOR_ITEM
    r = m.match_line(line("Excavation in ordinary soil", unit="furlong"))
    assert r.status == match.NEEDS_REVIEW and "not recognised" in r.reason and r.item_code is None


def test_sor_reference_is_carried_when_the_code_is_in_the_schedule(store):
    store.import_sor([{"source": "CPWD DSR 2023", "sor_code": "9.9", "description": "Brick work", "uom": "Cum", "rate": "6200"}])
    r = match.Matcher.from_store(store).match_line(line("Brick work in CM 1:4 as per DSR 9.9", code="9.9"))
    assert r.sor_reference == {"source": "CPWD DSR 2023", "code": "9.9", "rate": 6200.0, "description": "Brick work"}


def test_clean_verdict_forces_the_shape():
    assert match.clean_verdict({"choice": 2, "confidence": 1.7, "reason": "x"}, 3) == {"choice": 2, "confidence": 1.0, "reason": "x"}
    assert match.clean_verdict({"choice": 9, "confidence": 0.5}, 3)["choice"] is None
    assert match.clean_verdict({"choice": True, "confidence": 0.5}, 3)["choice"] is None
    assert match.clean_verdict({"choice": 1, "confidence": "high"}, 3) is None and match.clean_verdict("no", 3) is None


def test_claude_verifier_uses_the_fast_model_and_survives_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_call", lambda system, content, max_tokens=2000, use="main": calls.append((use, content)) or '{"choice": 1, "confidence": 0.93, "reason": "ok"}')
    cands = [{"name": "RCC M30", "spec": "abutments", "uom": "CUM"}]
    assert match.ClaudeVerifier().verify({"description": "RCC M30", "uom": "CUM"}, cands)["confidence"] == 0.93
    assert calls[0][0] == "fast" and "<boq_line>" in calls[0][1]

    def boom(*a, **k):
        raise ai.AIError("busy")
    monkeypatch.setattr(ai, "_call", boom)
    assert match.ClaudeVerifier().verify({"description": "x", "uom": "CUM"}, cands) is None


def test_sample_boq_against_the_catalogue(store):
    doc = parse.parse_file(FIX / "boq_sample.csv")
    results = match.Matcher.from_store(store).match_all(doc.lines)
    def result_for(start):
        return next(r for ln, r in zip(doc.lines, results) if ln.description.startswith(start))
    assert result_for("Excavation for roadway").item_code == "EXC-01"
    assert result_for("RCC M30 in substructure").item_code == "RCC-30"
    assert result_for("RCC M35 in superstructure").item_code == "RCC-35"
    assert result_for("Quality control laboratory").status == match.NEW_NON_SOR_ITEM
    assert all(r.status != match.AUTO_MATCHED or r.tier in ("alias", "sor_code") for r in results)   # no Claude, so no other auto-match
    assert sum(r.item_code is not None for r in results) >= 12


# ---- rates ---------------------------------------------------------------------------------------------------------------------

def test_true_unit_cost_formula():
    assert rates.true_unit_cost(1000, 3, 200, 100, 50) == pytest.approx(1380.0)
    assert rates.default_wastage("TMT steel Fe500") == (3, 4.0, 5) and rates.default_wastage("Excavation") is None


def test_boq_costing_coverage_and_break_even(store):
    store.import_activities([{"activity_code": "RCC30", "description": "RCC M30", "uom": "Cum", "material_cost": "5000", "labor_cost": "900",
                              "machinery_cost": "300", "overhead_cost": "200", "wastage_pct": "3", "erp_item_code": "RCC-30"}])
    lines = [line("RCC M30 for abutments", qty=100, rate=8650), line("Excavation in ordinary soil", qty=200, rate=118),
             line("TMT steel Fe 500D bars", unit="MT", qty=10, rate=71500), line("Something unmatched", qty=5, rate=1000)]
    res = [match.MatchResult(0, match.AUTO_MATCHED, "alias", 1.0, "RCC-30", ""), match.MatchResult(1, match.AUTO_MATCHED, "alias", 1.0, "EXC-01", ""),
           match.MatchResult(2, match.AUTO_MATCHED, "alias", 1.0, "TMT-500D", ""), match.MatchResult(3, match.NEW_NON_SOR_ITEM, "none", 0, None, "")]
    out = rates.price_boq(lines, res, store.items(), store.activity)
    rcc, exc, tmt, none = out["lines"]
    assert rcc.basis == "activity" and rcc.unit_cost == pytest.approx(5000 * 1.03 + 1400) and rcc.complete
    assert exc.basis == "composite item" and exc.unit_cost == 121.5 and exc.variance_pct == pytest.approx((121.5 - 118) / 118 * 100, abs=0.01)
    assert tmt.basis == "material only" and not tmt.complete and tmt.cost_amount is None
    assert none.basis == "none"
    est = 100 * 8650 + 200 * 118 + 10 * 71500 + 5 * 1000
    assert out["estimate_total"] == est and out["priced_lines"] == 2
    assert out["projected_cost"] == pytest.approx(100 * (5000 * 1.03 + 1400) + 200 * 121.5 + (10 * 71500 + 5 * 1000))
    assert out["coverage_pct"] == pytest.approx(100 * (100 * 8650 + 200 * 118) / est, abs=0.1)


def test_review_lines_are_priced_only_on_request_and_location_applies(store):
    ln = [line("Excavation in ordinary soil", qty=100, rate=118)]
    r = [match.MatchResult(0, match.NEEDS_REVIEW, "semantic_rules", 0.8, "EXC-01", "")]
    assert rates.price_boq(ln, r, store.items())["priced_lines"] == 0
    hill = {"cost_index": 1.1, "hill_carriage_pct": 20}
    out = rates.price_boq(ln, r, store.items(), location=hill, include_review=True)
    assert out["lines"][0].unit_cost == pytest.approx(121.5 * 1.1 * 1.2, abs=0.01)


def test_sor_reference_is_a_benchmark_not_a_cost(store):
    ref = {"source": "CPWD DSR 2023", "code": "9.9", "rate": 6200.0, "description": "Brick work"}
    out = rates.price_boq([line("Brick work", rate=7000)], [match.MatchResult(0, match.NEW_NON_SOR_ITEM, "none", 0, None, "", sor_reference=ref)], store.items())
    assert out["lines"][0].basis == "sor reference" and not out["lines"][0].complete and out["priced_lines"] == 0


# ---- risk ----------------------------------------------------------------------------------------------------------------------

def findings(**present):
    raw = {"risks": [{"tag": t, "present": present.get(t, "no_mention"), "detail": f"{t} detail", "clause": "5.1"} for t in risk.TAGS],
           "facts": {"contract_months": 12, "retention_pct": 5, "dlp_years": 5, "ld_pct_per_day": 0.05, "ld_cap_pct": 10, "estimated_cost": 128640000,
                     "price_variation": "none", "gst": "exclusive", "state": "Madhya Pradesh", "rate_basis": "percentage_rate"}}
    return risk.clean_risks(raw)


def test_there_are_twelve_tags_and_the_cleaner_forces_the_shape():
    assert len(risk.TAGS) == 12 and len(set(risk.TAGS)) == 12
    out = risk.clean_risks({"risks": [{"tag": "lab_testing", "present": "maybe", "detail": "x" * 999}, {"tag": "made_up", "present": "yes"}, "junk"],
                            "facts": {"retention_pct": 250, "dlp_years": True, "gst": "weird", "contract_months": 12, "state": "MP"}})
    assert list(out["risks"]) == risk.TAGS and out["risks"]["lab_testing"]["present"] == "unclear" and len(out["risks"]["lab_testing"]["detail"]) == 240
    assert out["risks"]["retention_dlp"] == {"present": "unclear", "detail": "", "clause": ""}
    assert out["facts"]["retention_pct"] is None and out["facts"]["dlp_years"] is None and out["facts"]["gst"] == "not stated" and out["facts"]["contract_months"] == 12.0
    with pytest.raises(ValueError):
        risk.clean_risks([1, 2])


def test_extract_risks_with_a_fake_claude(monkeypatch):
    sent = {}

    def fake(system, content, max_tokens=2000, use="main"):
        sent.update(system=system, content=content)
        return '```json\n{"risks": [{"tag": "retention_dlp", "present": "yes", "detail": "5% for 5 years", "clause": "6.4"}], "facts": {"retention_pct": 5, "dlp_years": 5}}\n```'
    monkeypatch.setattr(ai, "_call", fake)
    out = risk.extract_risks({"title": "Road"}, text="Retention money 5%. Defect liability 5 years.")
    assert out["risks"]["retention_dlp"]["present"] == "yes" and out["risks"]["lab_testing"]["present"] == "unclear" and out["facts"]["dlp_years"] == 5.0
    assert "not instructions" in sent["system"] and all(t in sent["system"] for t in risk.TAGS) and "<document>" in sent["content"]
    with pytest.raises(ai.AIError):
        risk.extract_risks({}, text="  ")
    with pytest.raises(ai.AIError, match="not a PDF"):
        import base64
        risk.extract_risks({}, pdf_base64=base64.b64encode(b"hello").decode())
    monkeypatch.setattr(ai, "_call", lambda *a, **k: "[1, 2]")
    with pytest.raises(ai.AIError):
        risk.extract_risks({}, text="x")


def test_simulation_prices_what_it_can_and_lists_the_rest():
    f = findings(lab_testing="yes", retention_dlp="yes", statutory_deductions="yes", pollution_compliance="yes", approved_makes="yes",
                 row_resource_penalties="yes", free_issue_gst="unclear")
    base = 100_000_000.0
    out = risk.simulate(base, f)
    by = {a["tag"]: a for a in out["adjustments"]}
    assert by["lab_testing"]["low"] == 500_000 and by["lab_testing"]["high"] == 1_500_000
    assert by["statutory_deductions"]["low"] == 1_000_000 and by["statutory_deductions"]["high"] == 2_000_000
    held = 128_640_000 * 0.05                                           # retention is worked on the buyer's estimate
    assert by["retention_dlp"]["low"] == pytest.approx(held * 0.10 * 5) and by["retention_dlp"]["high"] == pytest.approx(held * 0.12 * 5)
    assert out["risk_low"] == pytest.approx(500_000 + 1_000_000 + held * 0.5) and out["cost_low"] == pytest.approx(base + out["risk_low"])
    unpriced = {u["tag"] for u in out["unpriced"]}
    assert {"pollution_compliance", "approved_makes", "row_resource_penalties"} <= unpriced
    assert "free_issue_gst" in out["unclear"]
    assert out["exposures"][0]["amount"] == pytest.approx(12_864_000)  # 10% cap of the estimate
    assert {c["label"] for c in out["cash_flow"]} == {"GST TDS", "Income tax TDS"}
    assert out["break_even_pct_low"] == pytest.approx((out["cost_low"] / 128_640_000 - 1) * 100, abs=0.01)


def test_subset_drivers_use_given_or_boq_exposure():
    doc = parse.parse_file(FIX / "boq_sample.csv")
    share = risk.exposure_from_boq(doc.lines, "monsoon_weather")
    assert 0.2 < share < 0.9 and risk.exposure_from_boq(doc.lines, "approved_makes") is None
    f = findings(monsoon_weather="yes", approved_makes="yes")
    base = 100_000_000.0
    out = risk.simulate(base, f, lines=doc.lines, exposure={"approved_makes": 0.1})
    by = {a["tag"]: a for a in out["adjustments"]}
    assert by["monsoon_weather"]["high"] == pytest.approx(base * share * 0.08, abs=1) and "upper bound" in by["monsoon_weather"]["basis"]
    assert by["approved_makes"]["low"] == pytest.approx(base * 0.1 * 0.15) and by["approved_makes"]["high"] == pytest.approx(base * 0.1 * 0.35)
    assert not out["unpriced"]


def test_margin_and_low_bid_warnings():
    f = findings(lab_testing="yes")
    out = risk.simulate(100_000_000, f, l1_price=100_500_000, quoted_pct=-12)
    assert out["margin_at_l1_low"] == pytest.approx(100_500_000 - 101_500_000) and any("lose money" in w for w in out["warnings"])
    assert any("12% below" in w for w in out["warnings"])
    assert not any("below" in w for w in risk.simulate(100_000_000, f, quoted_pct=-3)["warnings"])
    with pytest.raises(ValueError):
        risk.simulate(0, f)


def test_missing_escalation_clause_is_called_out():
    f = findings()  # no_mention for escalation, but the facts say: no price variation, 12 months
    assert any("no price variation" in w for w in risk.simulate(1e8, f)["warnings"])


def test_monsoon_window():
    assert risk.monsoon_months(date(2026, 12, 1), 12) == 4
    assert risk.monsoon_months(date(2026, 10, 1), 3, "Tamil Nadu") == 3 and risk.monsoon_months(date(2026, 10, 1), 3) == 0
    assert risk.monsoon_months(date(2026, 12, 1), 3, "Tamil Nadu") == 1     # Dec, Jan, Feb: only December is in the window


def test_unbalanced_bidding_detection():
    lines = [line(f"Item {i}", qty=10, rate=1000) for i in range(6)]
    quoted = [1500, 1500, 1000, 980, 1000, 960]      # first third carries +50%, the rest slightly under
    out = risk.unbalanced_flags(lines, quoted)
    assert out["front_loaded"] and out["early_premium_pct"] == 50.0 and len(out["items_far_above_estimate"]) == 2
    assert not risk.unbalanced_flags(lines, [1000] * 6)["front_loaded"]
    assert risk.unbalanced_flags(lines, [None] * 6)["items_far_above_estimate"] == []


def test_column_number_row_and_whole_number_serials():
    doc = parse.parse_rows([["Sr", "Description", "Unit", "Quantity", "Rate"], [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, "Earthwork in excavation", "cum", 10, None]])
    assert len(doc.lines) == 1 and doc.lines[0].sr == "1"
