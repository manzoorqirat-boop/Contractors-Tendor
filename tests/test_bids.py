"""Bid workspace: pipeline, checklist, vault matching, change watch, alerts, results, drafts and the endpoints that serve them."""
import base64
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import ai, bids as bidmod, digest, web
from tender_digest.bids import BidBook, BidError, clean_patch, infer_kind
from tender_digest.gepnic import Tender, parse_corrigenda
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.conftest import fixture
from tests.test_web import FakeClient, FakeSender, PASSWORD, login, settings, tender

NOW = datetime(2026, 11, 14, 9, 0)
DOC = {"summary": "s", "requirements": ["Turnover of 2 crore in each of last 3 years", "Class A registration"], "financial": [{"label": "EMD", "value": "Rs 2 lakh"}],
       "dates": [], "documents": ["Copy of GST registration certificate", "Audited balance sheets for 3 years", "Notarised affidavit of no blacklisting", "Something unusual"],
       "penalties": [], "red_flags": []}


def kept(store, n=1, closing_days=10, work_type=None):
    t = Tender(portal="maha", tender_id=f"T{n}", title=f"Road {n}", reference="", description=f"Construction of road {n}", organisation="PWD||Division", category="Works",
               location="Pune", value=2e7, emd=2e5, published=NOW, closing=NOW + timedelta(days=closing_days), opening=NOW + timedelta(days=closing_days + 2))
    store.keep(t, NOW)
    if work_type:
        store.ai_save([(t.key, {"work_type": work_type, "civil": True, "fit": None, "reason": ""})], "none", False)
    return t


@pytest.fixture
def book():
    store = SeenStore(":memory:")
    return BidBook(store), store


@pytest.fixture
def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    env = settings()
    manager = RunManager(env, store, lambda p, s: FakeClient([]), FakeSender(), background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, manager)
    c = TestClient(app)
    ctx = type("Ctx", (), {"client": c, "store": store, "key": kept(store).key, "tmp": tmp_path})
    login(ctx)
    return ctx


# ---- the pipeline ------------------------------------------------------------------------------------------------

def test_a_bid_starts_from_a_tender_once_and_keeps_its_own_copy(book):
    b, store = book
    key = kept(store).key
    bid, created = b.create_from_tender(key, NOW)
    again, created2 = b.create_from_tender(key, NOW)
    assert created and not created2 and again["id"] == bid["id"] and bid["stage"] == "interested"
    store.prune(NOW + timedelta(days=400))  # the tender is cleaned up...
    assert b.get(bid["id"])["tender_id"] == "T1"  # ...the bid is not
    with pytest.raises(BidError):
        b.create_from_tender("maha:nope", NOW)


def test_only_known_fields_with_valid_values_can_change():
    assert clean_patch({"stage": "preparing", "emd_due": "2026-11-20", "my_value": "1500000"}) == {"stage": "preparing", "emd_due": "2026-11-20", "my_value": 1500000.0}
    assert clean_patch({"emd_due": ""}) == {"emd_due": None}
    for bad in ({"stage": "flying"}, {"emd_due": "next week"}, {"my_value": "lots"}, {"my_value": -5}, {"key": "x"}, {"id": 3}, "junk"):
        with pytest.raises(BidError):
            clean_patch(bad)


def test_stage_changes_are_timed_and_open_bids_sort_by_closing(book):
    b, store = book
    ids = [b.create_from_tender(kept(store, n, closing_days=d).key, NOW)[0]["id"] for n, d in ((1, 20), (2, 5), (3, 12))]
    b.update(ids[0], {"stage": "lost"}, NOW + timedelta(hours=1))
    order = [x["tender_id"] for x in b.list()]
    assert order == ["T2", "T3", "T1"]  # open ones by closing date, the lost one last
    assert b.get(ids[0])["stage_changed"] > b.get(ids[1])["stage_changed"]


# ---- checklist and the vault --------------------------------------------------------------------------------------

def test_checklist_comes_from_the_document_once_and_never_duplicates(book):
    b, store = book
    bid, _ = b.create_from_tender(kept(store).key, NOW)
    assert b.generate_items(bid["id"], NOW) == len(bidmod.STANDARD_TASKS)  # no document read yet: standard steps only
    b.set_json(bid["id"], "doc", DOC, NOW)
    added = b.generate_items(bid["id"], NOW)
    assert added == len(DOC["documents"]) + len(DOC["requirements"])
    assert b.generate_items(bid["id"], NOW) == 0
    kinds = {i["text"]: i["kind"] for i in b.items(bid["id"])}
    assert kinds["Copy of GST registration certificate"] == "document" and kinds["Class A registration"] == "condition"


def test_a_document_read_after_the_bid_started_is_picked_up(book):
    b, store = book
    t = kept(store)
    bid, _ = b.create_from_tender(t.key, NOW)
    assert b.get(bid["id"])["doc"] is None
    store.set_ai_text(t.key, "doc", __import__("json").dumps(DOC))
    assert b.get(bid["id"])["doc"]["requirements"][0].startswith("Turnover")


def test_required_papers_are_matched_to_the_vault_with_expiry_against_the_closing_date(book):
    b, store = book
    bid, _ = b.create_from_tender(kept(store, closing_days=30).key, NOW)  # closes 14 Dec
    b.set_json(bid["id"], "doc", DOC, NOW)
    b.generate_items(bid["id"], NOW)
    b.vault_add({"name": "GST certificate", "kind": "gst"}, None, "", NOW)
    b.vault_add({"name": "Balance sheets FY24", "kind": "turnover", "expiry": "2026-12-01"}, None, "", NOW)  # lapses before it closes
    b.vault_add({"name": "Old affidavit", "kind": "affidavit", "expiry": "2026-01-01"}, None, "", NOW)  # already lapsed
    got = {i["text"]: i["match"] for i in b.with_matches(b.get(bid["id"]), NOW) if i["kind"] == "document"}
    assert got["Copy of GST registration certificate"]["status"] == "ok"
    assert got["Audited balance sheets for 3 years"]["status"] == "expires_before_close"
    assert got["Notarised affidavit of no blacklisting"]["status"] == "expired"
    assert got["Something unusual"]["status"] == "unknown"
    # a paper chosen by hand overrides the guess; one that is deleted drops out of the checklist
    gst = next(d for d in b.vault_list() if d["kind"] == "gst")
    item = next(i for i in b.items(bid["id"]) if i["text"] == "Something unusual")
    b.update_item(bid["id"], item["id"], {"vault_id": gst["id"]})
    assert next(i for i in b.with_matches(b.get(bid["id"]), NOW) if i["id"] == item["id"])["match"]["status"] == "ok"
    b.vault_delete(gst["id"])
    assert b.items(bid["id"])[[i["id"] for i in b.items(bid["id"])].index(item["id"])]["vault_id"] is None
    with pytest.raises(BidError):
        b.update_item(bid["id"], item["id"], {"vault_id": 9999})


def test_missing_papers_are_called_missing_and_kinds_are_guessed_sensibly():
    assert infer_kind("Copy of GST registration certificate") == "gst"
    assert infer_kind("Labour licence") == "labour"
    assert infer_kind("EMD of Rs 5 lakh by DD") == "emd"
    assert infer_kind("Class A contractor licence") == "registration"
    assert infer_kind("something else") == "other"


def test_vault_files_are_checked_stored_outside_the_web_root_and_removed_with_the_record(tmp_path):
    b = BidBook(SeenStore(":memory:"), tmp_path / "v")
    d = b.vault_add({"name": "PAN", "kind": "pan"}, b"%PDF-1.4 x", "../../my pan.pdf", NOW)
    path, shown = b.vault_file(d["id"])
    assert path.parent == (tmp_path / "v").resolve() and path.read_bytes().startswith(b"%PDF") and "/" not in shown and ".." not in shown.replace(".pdf", "")
    for name in ("x.exe", "x.html", "x.svg"):
        with pytest.raises(BidError):
            b.vault_add({"name": "bad"}, b"data", name, NOW)
    with pytest.raises(BidError):
        b.vault_add({"name": "big"}, b"0" * (bidmod.MAX_FILE_BYTES + 1), "x.pdf", NOW)
    with pytest.raises(BidError):
        b.vault_add({"name": "x", "kind": "weird"}, None, "", NOW)
    with pytest.raises(BidError):
        b.vault_add({"name": "x", "expiry": "soon"}, None, "", NOW)
    assert b.vault_delete(d["id"]) and not path.exists() and b.vault_file(d["id"]) is None


# ---- watching for changes ----------------------------------------------------------------------------------------

class WatchClient:
    def __init__(self, pairs=None, corrigenda=None, fail=False):
        self.pairs, self.corr, self.fail = pairs or {}, corrigenda, fail

    def detail_page(self, t):
        if self.fail:
            raise RuntimeError("portal down")
        return self.pairs, self.corr


def test_moved_dates_and_new_corrigenda_are_recorded_once_and_the_tender_list_follows(book):
    b, store = book
    t = kept(store, closing_days=10)
    bid, _ = b.create_from_tender(t.key, NOW)
    later = NOW + timedelta(days=14)
    fresh = Tender(**{**t.__dict__, "closing": later})
    assert b.watch(fresh, WatchClient(corrigenda=[]), NOW) == 1  # the closing date moved
    assert b.get(bid["id"])["closing"] == later.isoformat(timespec="seconds")
    assert store.get_tender(t.key)["closing"] == later.isoformat(timespec="seconds")  # digest and 'closing soon' now use the new date
    assert b.watch(fresh, WatchClient(corrigenda=[]), NOW) == 0  # nothing new the next time
    assert b.watch(fresh, WatchClient(corrigenda=["Date extension (Corrigendum)"]), NOW) == 1
    assert b.watch(fresh, WatchClient(corrigenda=["Date extension (Corrigendum)"]), NOW) == 0
    assert b.watch(fresh, WatchClient(corrigenda=["Date extension (Corrigendum)", "BOQ revised (Addendum)"]), NOW) == 1
    assert [c["what"] for c in b.changes(bid["id"])] == ["corrigendum", "corrigendum", "closing"]
    assert b.unseen_total() == 3
    b.mark_seen(bid["id"])
    assert b.unseen_total() == 0


def test_corrigenda_already_published_when_you_start_are_reported_once(book):
    b, store = book
    t = kept(store)
    bid, _ = b.create_from_tender(t.key, NOW)
    assert b.watch(t, WatchClient(corrigenda=["A (x)", "B (y)"]), NOW) == 1
    assert "2 already published" in b.changes(bid["id"])[0]["new"]


def test_the_prebid_meeting_date_is_filled_in_but_never_overwrites_yours(book):
    b, store = book
    t = kept(store)
    bid, _ = b.create_from_tender(t.key, NOW)
    b.watch(t, WatchClient({"Pre Bid Meeting Date": "20-Nov-2026 11:00 AM"}, []), NOW)
    assert b.get(bid["id"])["prebid_date"] == "2026-11-20T11:00:00"
    b.update(bid["id"], {"prebid_date": "2026-11-22"}, NOW)
    b.watch(t, WatchClient({"Pre Bid Meeting Date": "25-Nov-2026 11:00 AM"}, []), NOW)
    assert b.get(bid["id"])["prebid_date"] == "2026-11-22"


def test_a_portal_failure_or_a_finished_bid_never_raises_or_records(book):
    b, store = book
    t = kept(store)
    bid, _ = b.create_from_tender(t.key, NOW)
    assert b.watch(t, WatchClient(fail=True), NOW) == 0
    b.update(bid["id"], {"stage": "lost"}, NOW)
    assert b.watch(Tender(**{**t.__dict__, "closing": NOW + timedelta(days=40)}), WatchClient(corrigenda=["x (y)"]), NOW) == 0
    assert b.watch_keys() == {}


def test_corrigenda_are_read_from_a_detail_page():
    html = ('<table><tr class="list_header"><td>S.No</td><td>Corrigendum Title</td><td>Corrigendum Type</td><td>View</td></tr>'
            '<tr><td>1</td><td>Date extension</td><td>Extension</td><td>v</td></tr></table>')
    assert parse_corrigenda(html) == ["Date extension (Extension)"]
    assert parse_corrigenda(fixture("tender_detail.html")) == [] and parse_corrigenda("<p>nothing</p>") == []


def test_a_full_run_watches_tenders_you_bid_for_and_the_email_reports_the_change(tmp_path):
    store = SeenStore(":memory:")
    env = settings()
    sender = FakeSender()
    t1 = tender(90, "maha")
    clients = {"maha": FakeClient([t1]), "delhi": FakeClient([])}
    manager = RunManager(env, store, lambda p, s: clients[p.key], sender, background=False)
    manager.start("manual", None, False)
    key = "maha:T90"
    bid, _ = manager.bids.create_from_tender(key, runner_now := datetime.now())
    clients["maha"] = FakeClient([tender(90, "maha", closing=t1.closing + timedelta(days=9))])
    manager.start("manual", None, True)
    changes = manager.bids.changes(bid["id"])
    assert any(c["what"] == "closing" for c in changes)
    subject_html = sender.calls[-1][0][5:7]
    assert "Your bids" in subject_html[0] and "closing date moved" in subject_html[0]
    assert all(c["id"] for c in changes) and manager.bids.digest_block(datetime.now())["changes"] == []  # mailed once, not again


# ---- alerts and results -------------------------------------------------------------------------------------------

def test_alerts_cover_deadlines_checklists_and_expiring_papers(book):
    b, store = book
    bid, _ = b.create_from_tender(kept(store, closing_days=2).key, NOW)
    b.update(bid["id"], {"emd_due": "2026-11-15", "prebid_date": "2026-11-16", "stage": "preparing"}, NOW)
    b.generate_items(bid["id"], NOW)
    b.vault_add({"name": "GST", "kind": "gst", "expiry": "2026-11-20"}, None, "", NOW)
    far, _ = b.create_from_tender(kept(store, 2, closing_days=60).key, NOW)
    got = {(a["kind"], a["bid_id"]): a for a in b.alerts(NOW, 7)}
    assert ("closing", bid["id"]) in got and ("emd", bid["id"]) in got and ("prebid", bid["id"]) in got and ("checklist", bid["id"]) in got
    assert ("vault", None) in got and ("closing", far["id"]) not in got
    assert got[("emd", bid["id"])]["days_left"] == 1
    b.update(bid["id"], {"stage": "dropped"}, NOW)
    assert not [a for a in b.alerts(NOW, 7) if a["bid_id"] == bid["id"]]


def test_results_show_win_rate_gap_to_the_winner_and_history_on_similar_tenders(book):
    b, store = book
    rows = [(1, "won", "Roads & highways", 1.9e7, None), (2, "lost", "Roads & highways", 2.2e7, 2.0e7), (3, "lost", "Buildings", 3.0e7, 2.5e7)]
    for n, stage, wt, mine, l1 in rows:
        bid, _ = b.create_from_tender(kept(store, n, work_type=wt).key, NOW)
        b.update(bid["id"], {"stage": stage, "my_value": mine, **({"l1_value": l1} if l1 else {})}, NOW)
    b.update(b.create_from_tender(kept(store, 4).key, NOW)[0]["id"], {"stage": "dropped"}, NOW)
    s = b.stats()
    assert s["won"] == 1 and s["lost"] == 2 and s["win_rate"] == 33 and s["dropped"] == 1
    assert s["avg_gap_to_l1"] == 15.0 and s["gap_samples"] == 2  # 10% and 20% above the winner
    assert {g["label"]: (g["won"], g["lost"]) for g in s["by_work_type"]}["Roads & highways"] == (1, 1)
    notes = b.history_notes("Roads & highways", "PWD||Division", 2e7)
    assert notes[0] == "Your record on Roads & highways work: 1 won, 1 lost" and len(notes) == 3
    assert BidBook(SeenStore(":memory:")).history_notes("x", "y", 1) == []


def test_the_digest_adds_a_section_only_when_there_is_something_to_say():
    block = {"alerts": [{"label": "Bid closes", "date": "2026-11-16T17:00:00", "days_left": 2, "urgency": "soon", "title": "Road 1", "tender_id": "T1", "extra": ""}],
             "changes": [{"id": 1, "what": "closing", "old": "2026-11-16T17:00:00", "new": "2026-11-30T17:00:00", "tender_id": "T1", "title": "Road 1"}]}
    plain = digest.build(NOW, [], [], [])
    with_bids = digest.build(NOW, [], [], [], bids=block)
    assert "Your bids" not in plain.html and "Your bids" in with_bids.html and "closing date moved" in with_bids.html and "YOUR BIDS" in with_bids.text


# ---- the endpoints -----------------------------------------------------------------------------------------------

def test_every_bid_and_vault_endpoint_needs_a_login(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    store = SeenStore(":memory:")
    c = TestClient(web.create_app(web.AppConfig(PASSWORD, "s", cookie_secure=False, run_scheduler=False), settings(), store))
    for method, path in (("get", "/api/bids"), ("post", "/api/bids"), ("get", "/api/bids/1"), ("patch", "/api/bids/1"), ("delete", "/api/bids/1"), ("get", "/api/bids/alerts"),
                         ("get", "/api/bids/stats"), ("post", "/api/bids/1/eligibility"), ("post", "/api/bids/1/draft"), ("get", "/api/vault"), ("post", "/api/vault"),
                         ("get", "/api/vault/1/file"), ("put", "/api/company-facts")):
        assert getattr(c, method)(path).status_code == 401, path


def test_a_bid_goes_from_a_tender_through_its_stages_with_a_checklist(app_ctx):
    c = app_ctx.client
    made = c.post("/api/bids", json={"key": app_ctx.key}).json()
    assert made["created"] and c.post("/api/bids", json={"key": app_ctx.key}).json() == {"id": made["id"], "created": False}
    assert c.post("/api/bids", json={"key": "maha:nope"}).status_code == 404
    bid = c.get(f"/api/bids/{made['id']}").json()
    assert bid["stage"] == "interested" and len(bid["items"]) == len(bidmod.STANDARD_TASKS) and bid["value_text"].startswith("₹")
    detail = c.get("/api/tenders/detail", params={"key": app_ctx.key}).json()
    assert detail["bid_id"] == made["id"] and detail["history"] == []
    r = c.patch(f"/api/bids/{made['id']}", json={"stage": "preparing", "owner": "Asha", "emd_due": "2026-11-18"}).json()
    assert r["stage"] == "preparing" and r["owner"] == "Asha"
    assert c.patch(f"/api/bids/{made['id']}", json={"stage": "nope"}).status_code == 422
    assert c.patch("/api/bids/999", json={"stage": "lost"}).status_code == 404
    listing = c.get("/api/bids").json()
    assert listing["counts"]["preparing"] == 1 and listing["bids"][0]["items_total"] == len(bid["items"])
    item = c.post(f"/api/bids/{made['id']}/items", json={"text": "Call the bank about the DD", "kind": "task"}).json()
    assert c.patch(f"/api/bids/{made['id']}/items/{item['id']}", json={"done": True}).json()["done"] is True
    assert c.post(f"/api/bids/{made['id']}/items", json={"text": "  "}).status_code == 422
    assert c.delete(f"/api/bids/{made['id']}/items/{item['id']}").status_code == 200
    assert c.delete(f"/api/bids/{made['id']}").status_code == 200 and c.get(f"/api/bids/{made['id']}").status_code == 404


def test_the_vault_takes_a_file_serves_it_as_a_download_and_holds_company_facts(app_ctx):
    c = app_ctx.client
    pdf = base64.b64encode(b"%PDF-1.4 hello").decode()
    doc = c.post("/api/vault", json={"name": "GST certificate", "kind": "gst", "expiry": "2030-01-01", "filename": "gst.pdf", "file_base64": pdf}).json()
    got = c.get(f"/api/vault/{doc['id']}/file")
    assert got.content == b"%PDF-1.4 hello" and got.headers["content-type"] == "application/octet-stream" and "attachment" in got.headers["content-disposition"]
    assert c.post("/api/vault", json={"name": "x", "filename": "x.exe", "file_base64": pdf}).status_code == 422
    assert c.post("/api/vault", json={"name": "x", "filename": "x.pdf", "file_base64": "!!!"}).status_code == 422
    assert c.patch(f"/api/vault/{doc['id']}", json={"notes": "original with Asha"}).json()["notes"] == "original with Asha"
    assert c.put("/api/company-facts", json={"facts": [{"label": "Turnover FY25", "value": "Rs 4.2 crore"}, {"label": "", "value": "ignored"}]}).json()["facts"] == [{"label": "Turnover FY25", "value": "Rs 4.2 crore"}]
    listing = c.get("/api/vault").json()
    assert listing["documents"][0]["status"] == "ok" and listing["facts"][0]["label"] == "Turnover FY25" and any(k["key"] == "gst" for k in listing["kinds"])
    assert c.delete(f"/api/vault/{doc['id']}").status_code == 200 and c.get(f"/api/vault/{doc['id']}/file").status_code == 404


def test_eligibility_and_drafts_use_the_profile_facts_and_vault_and_are_saved(app_ctx, monkeypatch):
    c = app_ctx.client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    c.put("/api/settings", json={"company_profile": "Class A civil contractor, roads"})
    c.put("/api/company-facts", json={"facts": [{"label": "Turnover FY25", "value": "Rs 4.2 crore"}]})
    c.post("/api/vault", json={"name": "GST certificate", "kind": "gst"})
    bid_id = c.post("/api/bids", json={"key": app_ctx.key}).json()["id"]
    prompts = []

    def fake_call(system, content, max_tokens=2000, use="main"):
        prompts.append((system, content))
        if "eligibility" in system:
            return '{"verdict":"Eligible","summary":"ok","checks":[{"requirement":"Class A registration","status":"met","evidence":"Profile says Class A","action":""}]}'
        return "To,\nThe Executive Engineer\n[Date]\nSubject: Bid for T1"

    monkeypatch.setattr(ai, "_call", fake_call)
    r = c.post(f"/api/bids/{bid_id}/eligibility")
    assert r.status_code == 422  # no conditions yet: asks for the document first
    assert "Read the tender document" in r.json()["detail"]
    monkeypatch.setattr(ai, "read_document", lambda t, text, pdf: DOC)
    read = c.post(f"/api/bids/{bid_id}/document", json={"key": app_ctx.key, "text": "NIT"}).json()
    assert read["doc"]["requirements"] and read["added"] == len(DOC["documents"]) + len(DOC["requirements"])
    ok = c.post(f"/api/bids/{bid_id}/eligibility").json()
    assert ok["verdict"] == "Eligible"
    body = prompts[-1][1]
    assert "Class A civil contractor" in body and "Rs 4.2 crore" in body and "GST certificate" in body and "Turnover of 2 crore" in body
    assert c.get(f"/api/bids/{bid_id}").json()["eligibility"]["verdict"] == "Eligible"
    first = c.post(f"/api/bids/{bid_id}/draft", json={"kind": "cover_letter"}).json()
    again = c.post(f"/api/bids/{bid_id}/draft", json={"kind": "cover_letter"}).json()
    assert first["cached"] is False and again["cached"] is True and "Executive Engineer" in first["text"]
    assert c.post(f"/api/bids/{bid_id}/draft", json={"kind": "custom"}).status_code == 422
    assert c.post(f"/api/bids/{bid_id}/draft", json={"kind": "poem"}).status_code == 422
    assert "placeholder" in prompts[-1][0] or "square brackets" in prompts[-1][0]
    assert c.get(f"/api/bids/{bid_id}").json()["drafts"]["cover_letter"] == first["text"]


def test_ai_features_explain_when_they_are_off(app_ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = app_ctx.client
    bid_id = c.post("/api/bids", json={"key": app_ctx.key}).json()["id"]
    assert c.post(f"/api/bids/{bid_id}/draft", json={"kind": "cover_letter"}).status_code == 503
    assert c.post(f"/api/bids/{bid_id}/eligibility").status_code == 503


def test_eligibility_answers_are_cleaned_and_a_gap_beats_an_optimistic_verdict():
    out = ai.clean_eligibility({"verdict": "Eligible", "checks": [{"requirement": "a", "status": "met"}, {"requirement": "b", "status": "gap", "action": "get it"}, {"status": "met"}, "junk"]})
    assert out["verdict"] == "Gaps" and len(out["checks"]) == 2
    assert ai.clean_eligibility({"verdict": "Maybe?", "checks": []})["verdict"] == "Unclear"
    with pytest.raises(ai.AIError):
        ai.eligibility({}, "", [], [], {"requirements": [], "financial": []})


# ---- fetching from the portal ------------------------------------------------------------------------------------

PORTAL_PAIRS = {"Tender Fee in ₹": "590", "Processing Fee in ₹": "500", "EMD Amount in ₹": "8,600", "EMD Fee Type": "fixed", "Tenderer Class": "Class-D", "Bid Validity(Days)": "60",
                "Clarification End Date": "17-Nov-2026 05:00 PM", "Pre Bid Meeting Date": "NA", "Bid Submission End Date": "19-Nov-2026 05:30 PM"}


class PortalFake:
    search_url = "https://x/s"

    def __init__(self, tenders, file=b"%PDF-1.4 nit", links=None, down=False):
        self.tenders, self.file, self.down = tenders, file, down
        self.links = [{"name": "Tendernotice_1.pdf", "href": "/doc"}] if links is None else links
        self.downloaded = []

    def organisations(self):
        if self.down:
            from tender_digest.gepnic import PortalError
            raise PortalError("down")
        return [("Other Dept", 1, "X"), ("PWD", len(self.tenders), "L")]

    def tenders_of(self, link, portal):
        return self.tenders if link == "L" else []

    def detail_full(self, t):
        return PORTAL_PAIRS, ["Date extension (Corrigendum)"], self.links

    def download(self, href):
        self.downloaded.append(href)
        return self.file, "application/pdf"


def fetch_ctx(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "v"))
    store = SeenStore(":memory:")
    env = settings()
    manager = RunManager(env, store, lambda p, s: fake, FakeSender(), background=False)
    c = TestClient(web.create_app(web.AppConfig(PASSWORD, "s", cookie_secure=False, run_scheduler=False), env, store, manager))
    key = kept(store).key
    ctx = type("Ctx", (), {"client": c})
    login(ctx)
    return c, c.post("/api/bids", json={"key": key}).json()["id"], store


def test_fetch_reads_the_public_fields_and_the_notice_and_fills_the_checklist(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    fake = PortalFake([Tender(portal="maha", tender_id="T1", title="", reference="", organisation="PWD||Division", published=None, closing=None, opening=None)])
    c, bid_id, _ = fetch_ctx(tmp_path, monkeypatch, fake)
    monkeypatch.setattr(ai, "read_document", lambda t, text, pdf: DOC)
    r = c.post(f"/api/bids/{bid_id}/fetch").json()
    assert r["nit"] == "read" and fake.downloaded == ["/doc"] and r["corrigenda"] == ["Date extension (Corrigendum)"]
    bid = c.get(f"/api/bids/{bid_id}").json()
    texts = [i["text"] for i in bid["items"]]
    assert "Pay the tender fee (₹590) online" in texts and "Registration class required: Class-D" in texts and "Copy of GST registration certificate" in texts
    assert bid["query_deadline"] == "2026-11-17T17:00:00" and bid["prebid_date"] is None  # 'NA' is not a date
    assert {"label": "Tenderer Class", "value": "Class-D"} in bid["portal_facts"]
    assert c.post(f"/api/bids/{bid_id}/fetch").json()["added"] == 0  # nothing duplicated


def test_fetch_without_ai_still_gives_the_facts_and_a_login_wall_is_reported_not_bypassed(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    t = Tender(portal="maha", tender_id="T1", title="", reference="", organisation="PWD||Division", published=None, closing=None, opening=None)
    c, bid_id, _ = fetch_ctx(tmp_path, monkeypatch, PortalFake([t]))
    assert c.post(f"/api/bids/{bid_id}/fetch").json()["nit"] == "ai_off"
    c, bid_id, _ = fetch_ctx(tmp_path, monkeypatch, PortalFake([t], file=b"<html>Please enter the captcha</html>"))
    r = c.post(f"/api/bids/{bid_id}/fetch").json()
    assert r["nit"] == "login" and "never gets past" in r["message"] and r["facts"]
    c, bid_id, _ = fetch_ctx(tmp_path, monkeypatch, PortalFake([t], links=[]))
    assert c.post(f"/api/bids/{bid_id}/fetch").json()["nit"] == "no_link"


def test_fetch_explains_a_missing_tender_or_a_portal_that_is_down(tmp_path, monkeypatch):
    c, bid_id, _ = fetch_ctx(tmp_path, monkeypatch, PortalFake([]))
    assert c.post(f"/api/bids/{bid_id}/fetch").status_code == 404
    t = Tender(portal="maha", tender_id="T1", title="", reference="", organisation="PWD||Division", published=None, closing=None, opening=None)
    c, bid_id, _ = fetch_ctx(tmp_path, monkeypatch, PortalFake([t], down=True))
    assert c.post(f"/api/bids/{bid_id}/fetch").status_code == 502
    assert c.post("/api/bids/999/fetch").status_code == 404


def test_document_links_are_found_on_a_real_detail_page():
    from tender_digest.gepnic import parse_doc_links, pick_nit
    links = parse_doc_links(fixture("tender_detail.html"))
    assert links and pick_nit(links)["name"] == "Tendernotice_1.pdf" and pick_nit([]) is None
