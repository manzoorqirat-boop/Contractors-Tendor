"""PDF BOQs, and entering your own cost for lump-sum lines."""
import base64
import io

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak
from reportlab.lib.styles import getSampleStyleSheet

from tender_digest.boq.masters import MasterStore
from tender_digest.boq.service import BoqError, BoqService
from tests.test_bids import app_ctx  # noqa: F401
from tests.test_boq_api import load_catalogue, start_bid
from tests.test_boq_service import FIX, KEY

HEAD = ["Sr No", "Description of item", "Unit", "Quantity", "Rate", "Amount"]
ROWS = [["1", "Providing and laying M25 cement concrete", "Cum", "120", "6200", "744000"],
        ["2", "Reinforcement steel Fe 500D", "MT", "8", "62000", "496000"],
        ["3", "Site mobilisation (lump sum)", "LS", "1", "150000", "150000"]]


def make_pdf(pages=1, rows=ROWS, repeat_header=True) -> bytes:
    buf = io.BytesIO()
    story = []
    for p in range(pages):
        data = [HEAD] + rows if (p == 0 or repeat_header) else rows
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        story += [Paragraph("BILL OF QUANTITIES", getSampleStyleSheet()["Title"]), t, PageBreak()]
    SimpleDocTemplate(buf, pagesize=A4).build(story)
    return buf.getvalue()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture()
def svc():
    s = BoqService(MasterStore(":memory:"))
    s.import_catalogue("items", (FIX / "erp_items.csv").read_text())
    return s


def test_pdf_table_is_read_and_flagged(svc):
    out = svc.analyse(KEY, "boq.pdf", b64(make_pdf()))
    lines = out["boq"]["lines"]
    assert [ln["description"][:12] for ln in lines] == ["Providing an", "Reinforcemen", "Site mobilis"]
    assert lines[0]["qty"] == 120 and lines[1]["uom"] == "MT"
    assert any("Read from a PDF" in w for w in out["boq"]["warnings"])


def test_repeated_headers_on_later_pages_are_dropped(svc):
    out = svc.analyse(KEY, "boq.pdf", b64(make_pdf(pages=3)))
    assert len(out["boq"]["lines"]) == 9
    assert not any(ln["description"].lower().startswith("description") for ln in out["boq"]["lines"])


def test_pdf_is_detected_by_content_not_name(svc):
    assert len(svc.analyse(KEY, "boq.bin", b64(make_pdf()))["boq"]["lines"]) == 3


def test_a_scan_without_tables_asks_for_excel_or_the_assistant(svc):
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build([Paragraph("Just a notice with no table at all.", getSampleStyleSheet()["Normal"])])
    with pytest.raises(BoqError, match="No BOQ table could be found"):
        svc.analyse(KEY, "scan.pdf", b64(buf.getvalue()))
    assert svc.view(KEY)["boq"] is None


def test_ai_reads_a_pdf_only_when_rules_find_nothing(svc):
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build([Paragraph("Scanned page", getSampleStyleSheet()["Normal"])])
    seen = []

    def reader(data):
        seen.append(data)
        return [HEAD, ["1", "Brick work in CM 1:6", "Cum", "40", "5800", "232000"]]

    out = svc.analyse(KEY, "scan.pdf", b64(buf.getvalue()), pdf_reader=reader)
    assert len(seen) == 1 and out["boq"]["lines"][0]["qty"] == 40
    assert "AI assistant read the items" in out["boq"]["warnings"][0]
    svc.analyse(KEY, "good.pdf", b64(make_pdf()), pdf_reader=reader)
    assert len(seen) == 1  # a readable PDF never goes to the assistant


def test_pdf_page_limit(svc):
    with pytest.raises(BoqError, match="80"):
        svc.analyse(KEY, "big.pdf", b64(make_pdf(pages=82, rows=ROWS[:1])))


def test_manual_cost_prices_a_lump_sum(svc):
    out = svc.analyse(KEY, "boq.pdf", b64(make_pdf()))
    lump = next(ln for ln in out["boq"]["lines"] if ln["description"].startswith("Site mob"))
    assert lump["lump_sum"] is True and lump["manual_cost"] is None
    before = svc.view(KEY)["pricing"]
    after = svc.set_cost(KEY, lump["line_no"], 90000)
    line = next(ln for ln in after["boq"]["lines"] if ln["line_no"] == lump["line_no"])
    assert line["manual_cost"] == 90000 and line["cost"]["basis"] == "your cost" and line["cost"]["cost_amount"] == 90000
    assert after["pricing"]["coverage_pct"] > before["coverage_pct"]
    cleared = svc.set_cost(KEY, lump["line_no"], None)
    assert next(ln for ln in cleared["boq"]["lines"] if ln["line_no"] == lump["line_no"])["manual_cost"] is None


@pytest.mark.parametrize("bad", [0, -5, 1e13])
def test_manual_cost_must_be_positive(svc, bad):
    out = svc.analyse(KEY, "boq.pdf", b64(make_pdf()))
    with pytest.raises(BoqError, match="above zero"):
        svc.set_cost(KEY, out["boq"]["lines"][0]["line_no"], bad)
    with pytest.raises(BoqError, match="no longer exists"):
        svc.set_cost(KEY, 999, 10)


def test_old_database_gets_the_new_column(tmp_path):
    p = tmp_path / "boq.db"
    s = MasterStore(str(p))
    s.db.execute("ALTER TABLE boq_line DROP COLUMN manual_cost")
    s.db.commit()
    s.db.close()
    again = MasterStore(str(p))
    assert "manual_cost" in [r[1] for r in again.db.execute("PRAGMA table_info(boq_line)")]


def test_endpoint_roles_and_effect(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    load_catalogue(app_ctx)
    r = c.post(f"/api/bids/{bid}/boq", json={"filename": "boq.pdf", "file_base64": b64(make_pdf())})
    assert r.status_code == 200
    line_no = next(ln for ln in r.json()["boq"]["lines"] if ln["lump_sum"])["line_no"]
    ok = c.post(f"/api/bids/{bid}/boq/lines/{line_no}/cost", json={"cost": 120000})
    assert ok.status_code == 200 and any(ln["manual_cost"] == 120000 for ln in ok.json()["boq"]["lines"])
    assert c.post(f"/api/bids/{bid}/boq/lines/{line_no}/cost", json={"cost": -1}).status_code == 422
    assert c.post(f"/api/bids/9999/boq/lines/{line_no}/cost", json={"cost": 5}).status_code == 404
