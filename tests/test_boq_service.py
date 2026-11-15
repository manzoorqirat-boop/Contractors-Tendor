"""BOQ service layer: upload and match, confirm, costing, catalogue import and the risk simulator. Claude is replaced by fakes."""
import base64
from pathlib import Path

import pytest

from tender_digest import ai
from tender_digest.boq import risk
from tender_digest.boq.masters import MasterStore
from tender_digest.boq.service import MAX_VERIFY_CALLS, BoqError, BoqService, _Capped, default_boq_path

FIX = Path(__file__).parent / "fixtures"
KEY = "maha:T1"


def b64(path_or_bytes) -> str:
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return base64.b64encode(data).decode()


@pytest.fixture()
def svc():
    s = BoqService(MasterStore(":memory:"))
    s.import_catalogue("items", (FIX / "erp_items.csv").read_text())
    return s


class Yes:
    """A verifier that always says 'the best candidate, 95% sure'."""
    calls = 0

    def verify(self, line, candidates):
        Yes.calls += 1
        return {"choice": 1, "confidence": 0.95, "reason": "ok"}


# ---- catalogue -------------------------------------------------------------------------------------------------------

def test_catalogue_import_and_listing(svc):
    out = svc.catalogue()
    assert out["counts"]["item_master"] == 16 and out["items"][0]["code"] == "BC-30" and {"code", "name", "uom", "rate", "composite"} <= set(out["items"][0])
    res = svc.import_catalogue("items", "erp_item_code,item_name,primary_uom\nX1,Thing,parsec\nX2,Good thing,Nos\n")
    assert res["result"]["added"] == 1 and res["result"]["errors"] == ["row 2: unit 'parsec' is not recognised"] and res["counts"]["item_master"] == 17


def test_catalogue_import_refuses_bad_input(svc):
    with pytest.raises(BoqError, match="Unknown list"):
        svc.import_catalogue("nonsense", "a,b\n1,2")
    with pytest.raises(BoqError, match="first"):
        svc.import_catalogue("items", "   ")
    with pytest.raises(BoqError, match="No rows"):
        svc.import_catalogue("items", "erp_item_code,item_name,primary_uom\n")
    with pytest.raises(BoqError, match="too large"):
        svc.import_catalogue("items", "a" * 5_000_001)


def test_all_four_lists_can_be_imported(svc):
    assert svc.import_catalogue("sor", "source,sor_code,description,uom,rate\nCPWD DSR 2023,9.9,Brick work,Cum,6200\n")["counts"]["sor_master"] == 1
    assert svc.import_catalogue("locations", "state,cost_index\nGoa,1.05\n")["counts"]["location_index"] == 1
    assert svc.import_catalogue("activities", "activity_code,description,uom,material_cost,erp_item_code\nA1,RCC,Cum,5000,RCC-30\n")["counts"]["activity_master"] == 1


# ---- upload ------------------------------------------------------------------------------------------------------------

def test_upload_matches_costs_and_replaces_the_previous_boq(svc):
    out = svc.analyse(KEY, "boq.csv", b64(FIX / "boq_sample.csv"))
    boq = out["boq"]
    assert boq["source_name"] == "boq.csv" and len(boq["lines"]) == 26 and boq["estimated_total"] == 128640000.0
    assert sum(boq["counts"].values()) == 26 and boq["counts"]["AUTO_MATCHED"] == 0 and boq["counts"]["NEEDS_REVIEW"] >= 10
    exc = next(ln for ln in boq["lines"] if ln["description"].startswith("Excavation for roadway"))
    assert exc["item_code"] == "EXC-01" and exc["item_name"] == "Excavation in ordinary soil" and exc["status"] == "NEEDS_REVIEW" and exc["cost"]["complete"] is False
    assert out["pricing"]["priced_lines"] == 0 and out["pricing"]["coverage_pct"] == 0
    again = svc.analyse(KEY, "second.csv", b64(FIX / "boq_sample.csv"))
    assert again["boq"]["id"] != boq["id"] and again["boq"]["source_name"] == "second.csv"
    assert svc.view("maha:other")["boq"] is None


def test_upload_problems_are_explained(svc):
    with pytest.raises(BoqError, match="could not be read"):
        svc.analyse(KEY, "x.csv", "not base64!!")
    with pytest.raises(BoqError, match="empty"):
        svc.analyse(KEY, "x.csv", "")
    with pytest.raises(BoqError, match="over 10 MB"):
        svc.analyse(KEY, "x.csv", b64(b"a" * (10 * 1024 * 1024 + 1)))
    with pytest.raises(BoqError, match="No BOQ table"):
        svc.analyse(KEY, "x.csv", b64(b"just,a,notice\nnothing,here,\n"))
    with pytest.raises(BoqError, match="Use .xlsx"):
        svc.analyse(KEY, "scan.pdf", b64(b"%PDF-1.4 whatever"))               # a PDF BOQ is not read
    with pytest.raises(BoqError, match="No BOQ table"):
        svc.analyse(KEY, "old.xls", b64(b"whatever"))                         # .xls is opened, but this one holds no table
    with pytest.raises(BoqError, match="valid .xlsx"):
        svc.analyse(KEY, "bad.xlsx", b64(b"PK\x03\x04 truncated, not a real workbook"))
    assert svc.view(KEY)["boq"] is None          # a failed upload leaves nothing behind


def test_empty_catalogue_is_called_out():
    out = BoqService(MasterStore(":memory:")).analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    assert any("catalogue is empty" in w for w in out["boq"]["warnings"]) and out["boq"]["counts"]["AUTO_MATCHED"] == 0


def test_excel_upload(svc, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in __import__("csv").reader(open(FIX / "boq_sample.csv", encoding="utf-8")):
        ws.append(r)
    wb.save(tmp_path / "b.xlsx")
    assert len(svc.analyse(KEY, "b.xlsx", b64(tmp_path / "b.xlsx"))["boq"]["lines"]) == 26


def test_claude_verifier_is_used_and_capped():
    Yes.calls = 0
    s = BoqService(MasterStore(":memory:"), lambda: Yes())
    s.import_catalogue("items", (FIX / "erp_items.csv").read_text())
    out = s.analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    assert out["ai"] and Yes.calls > 0 and out["boq"]["counts"]["AUTO_MATCHED"] > 0 and Yes.calls <= MAX_VERIFY_CALLS
    capped = _Capped(Yes(), 2)
    results = [capped.verify({}, []) for _ in range(4)]
    assert [r is not None for r in results] == [True, True, False, False] and capped.used == 2
    assert not BoqService(MasterStore(":memory:"), lambda: None).view(KEY)["ai"]


# ---- confirm -------------------------------------------------------------------------------------------------------------

def line_of(out, start):
    return next(ln for ln in out["boq"]["lines"] if ln["description"].startswith(start))


def test_confirming_a_match_prices_the_line_and_teaches_the_matcher(svc):
    out = svc.analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    ln = line_of(out, "Excavation for roadway")
    out = svc.confirm(KEY, ln["line_no"], "EXC-01", "ravi")
    ln = line_of(out, "Excavation for roadway")
    assert ln["status"] == "AUTO_MATCHED" and ln["tier"] == "confirmed" and ln["confidence"] == 1.0 and ln["cost"]["complete"] and ln["cost"]["unit_cost"] == 121.5
    assert out["boq"]["counts"]["AUTO_MATCHED"] == 1 and out["pricing"]["priced_lines"] == 1 and out["pricing"]["coverage_pct"] > 0
    nxt = svc.analyse("maha:T2", "b.csv", b64(FIX / "boq_sample.csv"))          # a later tender with the same wording
    assert line_of(nxt, "Excavation for roadway")["tier"] == "alias" and line_of(nxt, "Excavation for roadway")["status"] == "AUTO_MATCHED"


def test_confirm_can_mark_not_in_catalogue_and_refuses_bad_choices(svc):
    out = svc.analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    ln = line_of(out, "Excavation for roadway")
    out = svc.confirm(KEY, ln["line_no"], None)
    assert line_of(out, "Excavation for roadway")["status"] == "NEW_NON_SOR_ITEM" and line_of(out, "Excavation for roadway")["item_code"] is None
    with pytest.raises(BoqError, match="not in your catalogue"):
        svc.confirm(KEY, ln["line_no"], "NOPE")
    with pytest.raises(BoqError, match="priced per MT"):
        svc.confirm(KEY, ln["line_no"], "TMT-500D")                      # the line is per CUM
    with pytest.raises(BoqError, match="no longer exists"):
        svc.confirm(KEY, 999, "EXC-01")
    with pytest.raises(BoqError, match="no longer exists"):
        svc.confirm("maha:none", 1, "EXC-01")


def test_review_lines_are_priced_only_when_asked(svc):
    svc.analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    assert svc.view(KEY)["pricing"]["priced_lines"] == 0
    assert svc.view(KEY, include_review=True)["pricing"]["priced_lines"] >= 8


# ---- risks and the simulator --------------------------------------------------------------------------------------------

FINDINGS = {"risks": [{"tag": t, "present": "yes" if t in ("lab_testing", "retention_dlp", "statutory_deductions") else "no_mention", "detail": "d", "clause": "6.1"} for t in risk.TAGS],
            "facts": {"retention_pct": 5, "dlp_years": 5, "ld_pct_per_day": 0.05, "ld_cap_pct": 10, "contract_months": 12, "state": "Madhya Pradesh", "estimated_cost": 128640000}}


def test_risks_need_claude_and_are_saved(svc, monkeypatch):
    assert svc.risks(KEY)["findings"] is None and len(svc.risks(KEY)["tags"]) == 12
    monkeypatch.setattr(ai, "_call", lambda *a, **k: __import__("json").dumps(FINDINGS))
    out = svc.read_risks(KEY, {"title": "Road"}, text="Retention 5%.")
    assert out["findings"]["risks"]["retention_dlp"]["present"] == "yes" and out["findings"]["facts"]["dlp_years"] == 5.0 and out["findings"]["updated_at"]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.undo()
    with pytest.raises(ai.NotConfigured):
        svc.read_risks(KEY, {}, text="x")


def test_simulation_needs_both_halves_then_works(svc):
    with pytest.raises(BoqError, match="risks first"):
        svc.simulate(KEY)
    svc.store.save_risk(KEY, risk.clean_risks(FINDINGS))
    with pytest.raises(BoqError, match="Upload the BOQ first"):
        svc.simulate(KEY)
    svc.analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    sim = svc.simulate(KEY, l1_price=130_000_000, quoted_pct=-12)
    assert sim["coverage_pct"] == 0 and sim["base_cost"] == pytest.approx(128640000) and sim["risk_high"] > sim["risk_low"] > 0
    assert any("priced from your catalogue" in w for w in sim["warnings"]) and any("12% below" in w for w in sim["warnings"])
    assert {u["tag"] for u in sim["unpriced"]} == set()                  # lab, retention, deductions: all priceable
    assert sim["break_even_pct_low"] == pytest.approx((sim["cost_low"] / 128640000 - 1) * 100, abs=0.01)


def test_location_from_the_risk_state_changes_costs(svc):
    out = svc.analyse(KEY, "b.csv", b64(FIX / "boq_sample.csv"))
    svc.confirm(KEY, line_of(out, "Excavation for roadway")["line_no"], "EXC-01")
    plain = svc.view(KEY)["pricing"]["projected_cost"]
    svc.import_catalogue("locations", "state,cost_index,hill_carriage_pct\nMadhya Pradesh,1.10,0\n")
    svc.store.save_risk(KEY, risk.clean_risks(FINDINGS))
    assert svc.view(KEY)["pricing"]["projected_cost"] > plain


def test_database_path_sits_next_to_the_main_one(monkeypatch):
    monkeypatch.delenv("BOQ_DB_PATH", raising=False)
    assert default_boq_path(":memory:") == ":memory:" and default_boq_path("/data/tender_state.db") == "/data/boq.db"
    monkeypatch.setenv("BOQ_DB_PATH", "/x/y.db")
    assert default_boq_path("/data/tender_state.db") == "/x/y.db"
