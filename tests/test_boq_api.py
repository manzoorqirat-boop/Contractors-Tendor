"""BOQ endpoints on a bid: catalogue, upload, confirm, risks and the simulator. Claude is replaced by fakes."""
import base64
import json
from pathlib import Path

from tender_digest import ai
from tender_digest.boq import risk
from tests.test_bids import app_ctx  # noqa: F401  (the fixture: a logged-in client with one tender)

FIX = Path(__file__).parent / "fixtures"
FINDINGS = {"risks": [{"tag": t, "present": "yes" if t == "lab_testing" else "no_mention", "detail": "d", "clause": "6.1"} for t in risk.TAGS],
            "facts": {"retention_pct": 5, "dlp_years": 5, "estimated_cost": 128640000, "state": "Madhya Pradesh"}}


def b64(name):
    return base64.b64encode((FIX / name).read_bytes()).decode()


def start_bid(ctx):
    return ctx.client.post("/api/bids", json={"key": ctx.key}).json()["id"]


def load_catalogue(ctx):
    r = ctx.client.post("/api/boq/catalogue", json={"kind": "items", "csv_text": (FIX / "erp_items.csv").read_text()})
    assert r.status_code == 200 and r.json()["result"]["added"] == 16 and r.json()["result"]["errors"] == []


def test_boq_endpoints_need_a_login_and_a_real_bid(app_ctx):
    c = app_ctx.client
    assert c.get("/api/bids/9999/boq").status_code == 404
    assert c.post("/api/bids/9999/boq", json={"filename": "b.csv", "file_base64": b64("boq_sample.csv")}).status_code == 404
    assert c.post("/api/bids/9999/boq/lines/1", json={"item_code": None}).status_code == 404
    assert c.get("/api/bids/9999/risks").status_code == 404
    c.post("/api/logout")
    assert c.get("/api/boq/catalogue").status_code == 401 and c.post("/api/boq/catalogue", json={}).status_code == 401


def test_catalogue_import_then_list(app_ctx):
    load_catalogue(app_ctx)
    out = app_ctx.client.get("/api/boq/catalogue").json()
    assert out["counts"]["item_master"] == 16 and len(out["items"]) == 16
    r = app_ctx.client.post("/api/boq/catalogue", json={"kind": "nonsense", "csv_text": "a,b\n1,2"})
    assert r.status_code == 422 and "Unknown list" in r.json()["detail"]


def test_upload_confirm_and_read_back(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    load_catalogue(app_ctx)
    assert c.get(f"/api/bids/{bid}/boq").json()["boq"] is None
    out = c.post(f"/api/bids/{bid}/boq", json={"filename": "boq.csv", "file_base64": b64("boq_sample.csv")}).json()
    assert len(out["boq"]["lines"]) == 26 and out["pricing"]["priced_lines"] == 0
    line = next(ln for ln in out["boq"]["lines"] if ln["description"].startswith("Excavation for roadway"))
    done = c.post(f"/api/bids/{bid}/boq/lines/{line['line_no']}", json={"item_code": "EXC-01"}).json()
    assert done["pricing"]["priced_lines"] == 1
    assert c.get(f"/api/bids/{bid}/boq", params={"include_review": True}).json()["pricing"]["priced_lines"] >= 8
    bad = c.post(f"/api/bids/{bid}/boq/lines/{line['line_no']}", json={"item_code": "TMT-500D"})
    assert bad.status_code == 422 and "priced per MT" in bad.json()["detail"]


def test_bad_upload_is_a_422_with_a_reason(app_ctx):
    bid = start_bid(app_ctx)
    r = app_ctx.client.post(f"/api/bids/{bid}/boq", json={"filename": "x.csv", "file_base64": base64.b64encode(b"just,a,notice\n").decode()})
    assert r.status_code == 422 and "No BOQ table" in r.json()["detail"]


def test_risks_need_the_ai_key_then_simulate(app_ctx, monkeypatch):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert c.get(f"/api/bids/{bid}/risks").json()["findings"] is None
    assert c.post(f"/api/bids/{bid}/risks", json={"text": "Retention 5%"}).status_code == 503
    assert c.post(f"/api/bids/{bid}/risks/simulate", json={}).status_code == 422          # no risks read yet
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(FINDINGS))
    out = c.post(f"/api/bids/{bid}/risks", json={"text": "Retention 5%"}).json()
    assert out["findings"]["risks"]["lab_testing"]["present"] == "yes"
    assert c.post(f"/api/bids/{bid}/risks/simulate", json={}).status_code == 422          # no BOQ yet
    load_catalogue(app_ctx)
    c.post(f"/api/bids/{bid}/boq", json={"filename": "boq.csv", "file_base64": b64("boq_sample.csv")})
    sim = c.post(f"/api/bids/{bid}/risks/simulate", json={"l1_price": 130000000, "quoted_pct": -12}).json()
    assert sim["risk_high"] > sim["risk_low"] > 0 and any("12% below" in w for w in sim["warnings"])
    monkeypatch.setattr(ai, "_call", lambda *a, **k: (_ for _ in ()).throw(ai.AIError("busy")))
    assert c.post(f"/api/bids/{bid}/risks", json={"text": "x"}).status_code == 502
