"""Tenders added by hand: read from a notice, checked by the person, saved, then used like any collected tender."""
import base64
import json
from datetime import datetime, timedelta

from tender_digest import ai
from tender_digest.boq import risk
from tests.test_bids import NOW, app_ctx, kept  # noqa: F401  (app_ctx: a logged-in client with one collected tender)
from tests.test_boq_api import FINDINGS

LISTING = {"tender_id": "NIT/PWD/2026/17", "title": "Road repair, Ward 12", "description": "Repair of internal roads in Ward 12", "organisation": "Municipal Corporation, Roads Dept",
           "location": "Nagpur", "value": 12500000, "emd": 250000, "closing": "2099-12-02T15:00", "opening": "2099-12-03T11:00", "form_of_contract": "item rate"}
PDF = base64.b64encode(b"%PDF-1.4 notice").decode()


def body(**kw):
    return {"tender_id": "NIT/PWD/2026/17", "description": "Repair of internal roads in Ward 12", "organisation": "Municipal Corporation", "closing": "2099-12-02T15:00", "value": 12500000, **kw}


def test_every_manual_endpoint_needs_a_login(app_ctx):
    c = app_ctx.client
    c.post("/api/logout")
    for method, path in (("post", "/api/tenders/manual/read"), ("get", "/api/tenders/manual/check?tender_id=x"), ("post", "/api/tenders/manual"),
                         ("patch", "/api/tenders/manual?key=a"), ("delete", "/api/tenders/manual?key=a")):
        assert getattr(c, method)(path).status_code == 401, path


def test_the_notice_is_read_into_a_listing_for_checking_and_nothing_is_saved(app_ctx, monkeypatch):
    c = app_ctx.client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert c.post("/api/tenders/manual/read", json={"text": "NIT"}).status_code == 503
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps({**LISTING, "value": "lots", "closing": "not a date", "extra": "ignored"}))
    got = c.post("/api/tenders/manual/read", json={"text": "NOTICE INVITING TENDER ..."}).json()["listing"]
    assert got["tender_id"] == "NIT/PWD/2026/17" and got["value"] is None and got["closing"] == "" and "extra" not in got and got["emd"] == 250000
    assert c.post("/api/tenders/manual/read", json={}).status_code == 502  # nothing to read
    assert c.get("/api/tenders", params={"page_size": 50}).json()["total"] == 1


def test_saving_needs_the_essentials_and_refuses_duplicates(app_ctx):
    c = app_ctx.client
    assert c.post("/api/tenders/manual", json=body(tender_id=" ")).status_code == 422
    assert c.post("/api/tenders/manual", json=body(description="", title="")).status_code == 422
    assert c.post("/api/tenders/manual", json=body(closing="")).status_code == 422
    assert c.post("/api/tenders/manual", json=body(closing="soon")).status_code == 422
    assert c.post("/api/tenders/manual", json=body(value=-5)).status_code == 422
    assert c.post("/api/tenders/manual", json=body(tender_id="T1")).status_code == 409  # a collected tender already has this ID
    assert c.post("/api/tenders/manual", json=body(read_document=False)).status_code == 200
    assert c.post("/api/tenders/manual", json=body(read_document=False)).status_code == 409
    found = c.get("/api/tenders/manual/check", params={"tender_id": "nit/pwd/2026/17"}).json()["existing"]
    assert found and found[0]["manual"] and found[0]["portal_name"] == "Added by hand"
    assert c.get("/api/tenders/manual/check", params={"tender_id": "T1"}).json()["existing"][0]["manual"] is False


def test_a_saved_tender_is_listed_quietly_kept_with_its_notice_and_read(app_ctx, monkeypatch):
    c, store = app_ctx.client, app_ctx.store
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    doc = {"summary": "s", "requirements": ["Class A"], "financial": [], "dates": [], "documents": ["PAN"], "penalties": [], "red_flags": []}
    monkeypatch.setattr(ai, "read_document", lambda t, text, pdf: doc)
    r = c.post("/api/tenders/manual", json=body(pdf_base64=PDF, portal_name="Nagpur e-tender")).json()
    assert r["key"] == "manual:NIT/PWD/2026/17" and r["doc_read"] and r["source_kept"] and r["doc_error"] == ""
    d = c.get("/api/tenders/detail", params={"key": r["key"]}).json()
    assert d["portal_name"] == "Added by hand" and d["manual"] is True and d["has_source"] and d["search_url"] == "" and d["doc"]["requirements"] == ["Class A"]
    rows = c.get("/api/tenders", params={"page_size": 50}).json()["items"]
    assert {x["tender_id"] for x in rows} == {"T1", "NIT/PWD/2026/17"}
    assert [t.tender_id for t in store.pending(NOW)] == ["T1"]  # not in 'new since the last digest': you added it yourself


def test_a_notice_that_cannot_be_read_still_saves_the_tender(app_ctx, monkeypatch):
    c = app_ctx.client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = c.post("/api/tenders/manual", json=body(text="NIT text")).json()
    assert r["doc_read"] is False and "AI assistant is off" in r["doc_error"] and r["source_kept"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "read_document", lambda *a: (_ for _ in ()).throw(ai.AIError("busy")))
    r = c.post("/api/tenders/manual", json=body(tender_id="NIT-2", text="NIT text")).json()
    assert r["doc_error"] == "busy" and c.get("/api/tenders/detail", params={"key": r["key"]}).status_code == 200


def test_a_hand_added_tender_runs_the_whole_bid_flow_using_the_saved_notice(app_ctx, monkeypatch):
    c = app_ctx.client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    key = c.post("/api/tenders/manual", json=body(text="Special condition 6.1: lab testing is incidental.", read_document=False)).json()["key"]
    bid_id = c.post("/api/bids", json={"key": key}).json()["id"]
    bid = c.get(f"/api/bids/{bid_id}").json()
    assert bid["manual"] and bid["has_source"] and bid["search_url"] == "" and len(bid["items"]) >= 8
    assert c.post(f"/api/bids/{bid_id}/fetch").status_code == 422  # nothing on a portal to fetch
    seen = []
    monkeypatch.setattr(ai, "_call", lambda system, content, **k: seen.append(content) or json.dumps(FINDINGS))
    assert c.post(f"/api/bids/{bid_id}/risks", json={}).status_code == 200  # no upload: the kept notice is used
    assert "lab testing is incidental" in seen[0]
    other = c.post("/api/tenders/manual", json=body(tender_id="NIT-3", read_document=False)).json()["key"]
    other_bid = c.post("/api/bids", json={"key": other}).json()["id"]
    assert c.post(f"/api/bids/{other_bid}/risks", json={}).status_code == 422  # no notice kept for this one


def test_a_correction_reaches_the_tender_and_its_bid_and_only_hand_added_tenders_can_be_edited(app_ctx):
    c = app_ctx.client
    key = c.post("/api/tenders/manual", json=body(read_document=False)).json()["key"]
    bid_id = c.post("/api/bids", json={"key": key}).json()["id"]
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"closing": "2099-12-09T17:00", "value": 9000000, "location": "Pune"}).status_code == 200
    d = c.get("/api/tenders/detail", params={"key": key}).json()
    assert d["closing"].startswith("2099-12-09T17:00") and d["value"] == 9000000 and d["location"] == "Pune"
    bid = c.get(f"/api/bids/{bid_id}").json()
    assert bid["closing"].startswith("2099-12-09T17:00") and bid["value"] == 9000000
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"closing": ""}).status_code == 422
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"closing": "x"}).status_code == 422
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"clear_value": True}).status_code == 200
    assert c.get("/api/tenders/detail", params={"key": key}).json()["value"] is None
    assert c.patch("/api/tenders/manual", params={"key": app_ctx.key}, json={"location": "x"}).status_code == 404  # a collected tender


def test_a_hand_added_tender_can_be_removed_once_it_has_no_bid(app_ctx):
    c, vault = app_ctx.client, app_ctx.tmp
    key = c.post("/api/tenders/manual", json=body(text="NIT text", read_document=False)).json()["key"]
    bid_id = c.post("/api/bids", json={"key": key}).json()["id"]
    assert c.delete("/api/tenders/manual", params={"key": key}).status_code == 409
    c.delete(f"/api/bids/{bid_id}")
    assert list((vault / "vault" / "sources").glob("*"))  # the notice is on disk
    assert c.delete("/api/tenders/manual", params={"key": key}).status_code == 200
    assert c.get("/api/tenders/detail", params={"key": key}).status_code == 404 and not list((vault / "vault" / "sources").glob("*"))
    assert c.delete("/api/tenders/manual", params={"key": app_ctx.key}).status_code == 404  # never a collected tender


def test_reading_a_document_on_any_tender_keeps_it_for_later_steps(app_ctx, monkeypatch):
    c = app_ctx.client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "read_document", lambda t, text, pdf: {"summary": "", "requirements": [], "financial": [], "dates": [], "documents": [], "penalties": [], "red_flags": []})
    assert c.get("/api/tenders/detail", params={"key": app_ctx.key}).json()["has_source"] is False
    assert c.post("/api/tenders/document", json={"key": app_ctx.key, "text": "NIT"}).status_code == 200
    assert c.get("/api/tenders/detail", params={"key": app_ctx.key}).json()["has_source"] is True


def test_a_hand_added_bid_reminds_you_to_check_the_portal_until_you_say_you_did(app_ctx):
    c = app_ctx.client
    soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT17:00")
    key = c.post("/api/tenders/manual", json=body(closing=soon, read_document=False)).json()["key"]
    bid_id = c.post("/api/bids", json={"key": key}).json()["id"]
    c.patch(f"/api/bids/{bid_id}", json={"stage": "preparing"})
    kinds = lambda: [a["kind"] for a in c.get("/api/bids/alerts").json()["alerts"] if a["bid_id"] == bid_id]  # noqa: E731
    assert "recheck" in kinds()
    c.patch(f"/api/bids/{bid_id}", json={"portal_checked": datetime.now().strftime("%Y-%m-%d")})
    assert "recheck" not in kinds()
    collected = c.post("/api/bids", json={"key": app_ctx.key}).json()["id"]  # a collected tender is watched by the app: no such reminder
    assert not [a for a in c.get("/api/bids/alerts").json()["alerts"] if a["bid_id"] == collected and a["kind"] == "recheck"]


def test_listing_values_are_cleaned():
    out = ai.clean_listing({"tender_id": 123, "value": True, "emd": -1, "closing": "1999-01-01", "opening": "2026-12-05", "organisation": " PWD ", "published": 5})
    assert out["tender_id"] == "123" and out["value"] is None and out["emd"] is None and out["closing"] == "" and out["opening"] == "2026-12-05T00:00" and out["organisation"] == "PWD"
    import pytest
    with pytest.raises(ai.AIError):
        ai.clean_listing([])
