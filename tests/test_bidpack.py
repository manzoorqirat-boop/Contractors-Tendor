"""Bid pack zip, letter templates with versions, comparing tenders, and corrigenda recorded by hand."""
import io
import zipfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import letters, web
from tender_digest.letters import LetterError, Letters, render
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_bids import NOW, kept
from tests.test_web import PASSWORD, FakeSender, settings

GOOD = "purple-tractor-window"
PDF = b"%PDF-1.4 certificate"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    env = settings()
    manager = RunManager(env, store, lambda p, s: None, FakeSender(), background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, manager)
    owner = TestClient(app)
    assert owner.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    t1, t2 = kept(store, 1, 10), kept(store, 2, 20)
    bid = owner.post("/api/bids", json={"key": t1.key}).json()["id"]

    def person(username, role):
        assert owner.post("/api/users", json={"username": username, "role": role, "password": "temporary-pass-1"}).status_code == 200
        c = TestClient(app)
        c.post("/api/login", json={"username": username, "password": "temporary-pass-1"})
        c.post("/api/account/password", json={"current": "temporary-pass-1", "new": GOOD})
        return c

    return type("C", (), {"owner": owner, "bid": bid, "t1": t1, "t2": t2, "store": store, "person": person, "app": app})


def add_paper(c, name, kind, with_file=True, expiry=""):
    import base64
    body = {"name": name, "kind": kind, "expiry": expiry}
    if with_file:
        body |= {"filename": f"{kind}.pdf", "file_base64": base64.b64encode(PDF).decode()}
    r = c.owner.post("/api/vault", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---- the pack -----------------------------------------------------------------------------------------------------

def checklist(c, texts):
    for t in texts:
        assert c.owner.post(f"/api/bids/{c.bid}/items", json={"text": t, "kind": "document"}).status_code == 200


def test_pack_holds_files_in_checklist_order_with_an_honest_index(ctx):
    add_paper(ctx, "GST certificate", "gst")
    add_paper(ctx, "Old affidavit", "affidavit", expiry="2026-01-01")
    add_paper(ctx, "PAN card", "pan", with_file=False)
    checklist(ctx, ["Copy of GST registration certificate", "Notarised affidavit of no blacklisting", "PAN card copy", "Labour licence"])
    r = ctx.owner.get(f"/api/bids/{ctx.bid}/pack")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and "bid-pack-T1.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert names[0].startswith("01-Copy of GST") and names[1].startswith("02-Notarised") and "00-index.txt" in names
    assert z.read(names[0]) == PDF
    index = z.read("00-index.txt").decode()
    assert "CHECK: expired" in index and "without a file attached" in index and "MISSING" in index
    assert r.headers["x-pack-files"] == "2" and int(r.headers["x-pack-attention"]) >= 3


def test_a_paper_that_answers_two_lines_is_packed_once(ctx):
    doc = add_paper(ctx, "GST certificate", "gst")
    checklist(ctx, ["GST registration certificate", "GST certificate (self attested)"])
    z = zipfile.ZipFile(io.BytesIO(ctx.owner.get(f"/api/bids/{ctx.bid}/pack").content))
    assert len([n for n in z.namelist() if n.endswith(".pdf")]) == 1 and "already in the pack" in z.read("00-index.txt").decode()
    assert doc["id"]


def test_pack_includes_notice_and_drafts_and_survives_odd_names(ctx):
    add_paper(ctx, "GST", "gst")
    checklist(ctx, ["../../etc/passwd GST certificate <script>"])
    ctx.owner.post(f"/api/bids/{ctx.bid}/document", json={"text": "Notice text for the pack " * 5})
    z = zipfile.ZipFile(io.BytesIO(ctx.owner.get(f"/api/bids/{ctx.bid}/pack").content))
    for n in z.namelist():
        assert ".." not in n and not n.startswith("/") and "<" not in n


def test_pack_is_not_for_view_only_accounts_or_unknown_bids(ctx):
    vera = ctx.person("vera", "viewer")
    assert vera.get(f"/api/bids/{ctx.bid}/pack").status_code == 403
    assert ctx.owner.get("/api/bids/999/pack").status_code == 404
    assert ctx.person("beena", "bidder").get(f"/api/bids/{ctx.bid}/pack").status_code == 200


# ---- templates --------------------------------------------------------------------------------------------------

def test_render_fills_known_placeholders_and_reports_the_rest():
    text, missing = render("To {{ organisation }}, re {{tender_id}} {{nonsense}} {{emd}}", {"organisation": "PWD", "tender_id": "T1", "emd": ""})
    assert text == "To PWD, re T1 {{nonsense}} {{emd}}" and missing == ["nonsense", "emd"]
    assert render("{{a}}{{a}}", {"a": "x"}) == ("xx", [])


def test_every_save_keeps_the_old_wording_and_restore_adds_a_version():
    book = Letters(SeenStore(":memory:"))
    t = book.create("Covering letter", "Dear Sir,\nWe bid for {{tender_id}}.", "meera", NOW)
    assert t["version"] == 1 and t["placeholders"] == ["tender_id"]
    assert book.save(t["id"], "Dear Sir,\nWe bid for {{tender_id}}.", "x", NOW)["version"] == 1       # unchanged: no new version
    v2 = book.save(t["id"], "Respected Sir,\nWe bid for {{tender_id}} ({{closing}}).", "raj", NOW + timedelta(days=1), note="more formal")
    assert v2["version"] == 2 and v2["author"] == "raj" and v2["note"] == "more formal"
    assert [v["version"] for v in book.versions(t["id"])] == [2, 1]
    back = book.restore(t["id"], 1, "meera", NOW + timedelta(days=2))
    assert back["version"] == 3 and back["body"].startswith("Dear Sir") and back["note"] == "Restored version 1"
    assert len(book.versions(t["id"])) == 3
    with pytest.raises(LetterError, match="no longer exists"):
        book.restore(t["id"], 99, "x", NOW)


def test_template_rules(monkeypatch):
    book = Letters(SeenStore(":memory:"))
    a = book.create("A", "body", "u", NOW)
    with pytest.raises(LetterError, match="already exists"):
        book.create("a", "other", "u", NOW)
    with pytest.raises(LetterError, match="name"):
        book.create("  ", "x", "u", NOW)
    with pytest.raises(LetterError, match="empty"):
        book.create("B", " \n ", "u", NOW)
    with pytest.raises(LetterError, match="characters"):
        book.create("B", "x" * (letters.MAX_BODY + 1), "u", NOW)
    b = book.create("B", "x", "u", NOW)
    with pytest.raises(LetterError, match="already exists"):
        book.save(b["id"], "x", "u", NOW, name="A")
    assert book.save(b["id"], "x", "u", NOW, name="Renamed")["name"] == "Renamed"
    monkeypatch.setattr(letters, "MAX_TEMPLATES", 2)
    with pytest.raises(LetterError, match="Up to 2"):
        book.create("C", "x", "u", NOW)
    monkeypatch.setattr(letters, "MAX_VERSIONS", 3)
    for i in range(6):
        book.save(a["id"], f"body {i}", "u", NOW)
    assert [v["version"] for v in book.versions(a["id"])] == [7, 6, 5]
    assert book.delete(a["id"]) and not book.delete(a["id"]) and book.versions(a["id"]) == []


def test_templates_over_the_api_and_filled_from_a_bid(ctx):
    c = ctx.owner
    c.put("/api/company-facts", json={"facts": [{"label": "Company name", "value": "Rao Infra Pvt Ltd"}, {"label": "GSTIN", "value": "27ABCDE1234F1Z5"}]})
    r = c.post("/api/letter-templates", json={"name": "Cover", "body": "{{company_company_name}} ({{company_gstin}}) bids for {{tender_id}}: {{title}}.\nCloses {{closing}}. {{unknown_thing}}"})
    assert r.status_code == 200
    tid = r.json()["id"]
    listed = c.get("/api/letter-templates").json()
    assert listed["templates"][0]["name"] == "Cover" and {"key": "company_gstin", "label": "GSTIN"} in listed["fields"] and any(f["key"] == "closing" for f in listed["fields"])
    out = c.post(f"/api/bids/{ctx.bid}/letter", json={"template_id": tid}).json()
    assert "Rao Infra Pvt Ltd (27ABCDE1234F1Z5) bids for T1: Construction of road 1." in out["text"] and "{{unknown_thing}}" in out["text"] and out["missing"] == ["unknown_thing"]
    assert c.put(f"/api/letter-templates/{tid}", json={"body": "Changed {{tender_id}}", "note": "v2"}).json()["version"] == 2
    assert [v["version"] for v in c.get(f"/api/letter-templates/{tid}/versions").json()["versions"]] == [2, 1]
    assert c.post(f"/api/letter-templates/{tid}/restore", json={"version": 1}).json()["version"] == 3
    assert c.post("/api/letter-templates", json={"name": "cover", "body": "x"}).status_code == 422
    assert c.put("/api/letter-templates/999", json={"body": "x"}).status_code == 404
    assert c.post("/api/letter-templates/999/restore", json={"version": 1}).status_code == 404
    assert c.post(f"/api/bids/{ctx.bid}/letter", json={"template_id": 999}).status_code == 404
    assert c.delete(f"/api/letter-templates/{tid}").status_code == 200 and c.delete(f"/api/letter-templates/{tid}").status_code == 404


def test_viewers_read_templates_but_cannot_change_them(ctx):
    tid = ctx.owner.post("/api/letter-templates", json={"name": "Cover", "body": "x"}).json()["id"]
    vera = ctx.person("vera", "viewer")
    assert vera.get("/api/letter-templates").status_code == 200
    assert vera.put(f"/api/letter-templates/{tid}", json={"body": "y"}).status_code == 403
    assert vera.post("/api/letter-templates", json={"name": "N", "body": "y"}).status_code == 403
    assert vera.delete(f"/api/letter-templates/{tid}").status_code == 403


# ---- compare -----------------------------------------------------------------------------------------------------

def test_compare_two_to_four_tenders(ctx):
    r = ctx.owner.get("/api/tenders/compare", params=[("keys", ctx.t1.key), ("keys", ctx.t2.key)])
    assert r.status_code == 200
    a, b = r.json()["tenders"]
    assert a["tender_id"] == "T1" and b["tender_id"] == "T2" and a["emd_pct"] == 1.0 and a["bid_id"] == ctx.bid and b["bid_id"] is None
    assert a["days_left"] is not None and a["portal_name"]
    assert ctx.owner.get("/api/tenders/compare", params=[("keys", ctx.t1.key)]).status_code == 422
    assert ctx.owner.get("/api/tenders/compare", params=[("keys", ctx.t1.key), ("keys", ctx.t1.key)]).status_code == 422  # the same tender twice is one
    assert ctx.owner.get("/api/tenders/compare", params=[("keys", ctx.t1.key), ("keys", "maha:gone")]).status_code == 404
    assert ctx.owner.get("/api/tenders/compare", params=[("keys", f"k{i}") for i in range(5)]).status_code == 422


# ---- corrigenda by hand ------------------------------------------------------------------------------------------

def test_a_corrigendum_is_logged_and_can_move_the_closing_date(ctx):
    c = ctx.owner
    before = c.get(f"/api/bids/{ctx.bid}").json()["closing"]
    r = c.post(f"/api/bids/{ctx.bid}/corrigenda", json={"note": "  Corrigendum 2: closing extended by 7 days  ", "closing": "2026-12-05"})
    assert r.status_code == 200
    bid = r.json()
    assert bid["closing"].startswith("2026-12-05") and bid["closing"] != before
    whats = [(x["what"], x["new"]) for x in bid["changes"]]
    assert any(w == "corrigendum" and "closing extended by 7 days (noted by admin)" in n for w, n in whats) and any(w == "closing" for w, _ in whats)
    assert bid["corrigenda"] == ["Corrigendum 2: closing extended by 7 days"]
    listed = c.get("/api/tenders/detail", params={"key": ctx.t1.key}).json()
    assert listed["closing"].startswith("2026-12-05")                       # the tender list follows
    c.post(f"/api/bids/{ctx.bid}/corrigenda", json={"note": "Addendum: BOQ item 12 revised"})
    assert len(c.get(f"/api/bids/{ctx.bid}").json()["corrigenda"]) == 2 and c.get(f"/api/bids/{ctx.bid}").json()["closing"].startswith("2026-12-05")


def test_corrigendum_input_is_checked(ctx):
    c = ctx.owner
    assert c.post(f"/api/bids/{ctx.bid}/corrigenda", json={"note": "   "}).status_code == 422
    assert c.post(f"/api/bids/{ctx.bid}/corrigenda", json={"note": "x", "closing": "next week"}).status_code == 422
    assert c.post(f"/api/bids/{ctx.bid}/corrigenda", json={"note": "x" * 601}).status_code == 422
    assert c.post("/api/bids/999/corrigenda", json={"note": "x"}).status_code == 404
    assert c.get(f"/api/bids/{ctx.bid}").json()["changes"] == []             # nothing half-recorded
    assert ctx.person("vera", "viewer").post(f"/api/bids/{ctx.bid}/corrigenda", json={"note": "x"}).status_code == 403
