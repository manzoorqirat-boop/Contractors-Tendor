"""AI features, with Claude replaced by fakes: labelling, filters from a question, the brief, documents, re-tenders, settings and the digest intro."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import ai, appsettings, digest, web
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import FakeClient, FakeSender, PASSWORD, login, settings, tender

NOW = datetime(2026, 11, 14, 9, 0)


def kept(store, n, title, org="PWD||Roads", tid=None):
    t = Tender(portal="maha", tender_id=tid or f"T{n}", title=title, reference="", description=title, organisation=org, category="Works", location="Pune", value=2e6,
               published=NOW, closing=NOW + timedelta(days=10), opening=None)
    store.keep(t, NOW)
    return t.key


# ---- parsing Claude's answers --------------------------------------------------------------------------------------

def test_json_is_found_inside_fences_and_chatter():
    assert ai._json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai._json('Here you go: [{"i": 0}] done') == [{"i": 0}]
    with pytest.raises(ai.AIError):
        ai._json("no json here")


def test_labels_are_forced_into_the_allowed_values():
    raw = [{"i": 0, "work_type": "Space travel", "civil": False, "fit": 9, "reason": "x"}, {"i": 1, "work_type": "Buildings", "fit": 4, "reason": "ok"},
           {"i": 7, "work_type": "Buildings"}, "junk"]
    out = ai.clean_labels(raw, 2, with_fit=True)
    assert out[0] == {"work_type": "Other civil", "civil": False, "fit": None, "reason": "x"}
    assert out[1]["fit"] == 4 and 7 not in out
    assert ai.clean_labels(raw, 2, with_fit=False)[1]["fit"] is None  # no profile, no score


def test_brief_and_document_answers_are_cleaned():
    assert ai.clean_brief({"recommendation": "Definitely", "reasons": ["a"] * 9})["recommendation"] == "Maybe"
    assert len(ai.clean_brief({"reasons": ["a"] * 9})["reasons"]) == 4
    assert ai.clean_doc({"summary": "s", "financial": [{"label": "EMD", "value": "2 lakh"}, "bad"]})["financial"] == [{"label": "EMD", "value": "2 lakh"}]
    with pytest.raises(ai.AIError):
        ai.read_document({}, text="  ")
    with pytest.raises(ai.AIError):
        ai.read_document({}, pdf_base64="bm90IGEgcGRm")  # "not a pdf"


# ---- labelling and sorting ----------------------------------------------------------------------------------------

def test_enrich_labels_open_tenders_once_and_sorts_by_fit(monkeypatch):
    store = SeenStore(":memory:")
    a, b = kept(store, 1, "Road widening at Pune"), kept(store, 2, "Painting of school")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    calls = []

    def fake(chunk, profile):
        calls.append(len(chunk))
        return {i: {"work_type": "Roads & highways" if "Road" in t["title"] else "Buildings", "civil": True, "fit": 5 if "Road" in t["title"] else 2, "reason": "r"} for i, t in enumerate(chunk)}

    monkeypatch.setattr(ai, "classify", fake)
    assert ai.enrich(store, NOW, "We build roads in Pune", False, 100)["scored"] == 2
    assert ai.enrich(store, NOW, "We build roads in Pune", False, 100)["scored"] == 0 and calls == [2]  # already done
    rows, _ = store.search(NOW, {}, "fit")
    assert [r["key"] for r in rows] == [a, b] and rows[0]["fit"] == 5
    assert store.search(NOW, {"work_type": ["Buildings"]})[1] == 1 and store.search(NOW, {"min_fit": 4})[1] == 1
    assert ai.enrich(store, NOW, "A different profile", False, 100)["scored"] == 2  # profile changed: scored again


def test_not_civil_is_only_hidden_when_asked(monkeypatch):
    store = SeenStore(":memory:")
    kept(store, 1, "Supply of laptops")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "classify", lambda chunk, p: {0: {"work_type": "Other civil", "civil": False, "fit": None, "reason": ""}})
    ai.enrich(store, NOW, "", False, 10)
    assert store.search(NOW, {})[1] == 1 and store.search(NOW, {})[0][0]["ai_civil"] == 0  # flagged, still shown
    store2 = SeenStore(":memory:")
    kept(store2, 1, "Supply of laptops")
    ai.enrich(store2, NOW, "", True, 10)
    assert store2.search(NOW, {})[1] == 0


def test_a_failing_ai_never_raises_out_of_enrich(monkeypatch):
    store = SeenStore(":memory:")
    kept(store, 1, "Road")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "classify", lambda c, p: (_ for _ in ()).throw(ai.AIError("busy")))
    assert ai.enrich(store, NOW, "", False, 10)["error"] == "busy"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert ai.enrich(store, NOW, "", False, 10) == {"scored": 0, "error": None}


# ---- re-tenders ---------------------------------------------------------------------------------------------------

def test_the_same_work_reissued_is_found_but_other_work_is_not():
    store = SeenStore(":memory:")
    a = kept(store, 1, "Construction of RCC drain along Station Road Ward 12 phase 1", tid="2026_PWD_001")
    b = kept(store, 2, "Construction of R.C.C. drain along Station Road, Ward 12, phase 1", tid="2026_PWD_077")
    kept(store, 3, "Repair of bridge over river near Satara town", tid="2026_PWD_090")
    kept(store, 4, "Construction of RCC drain along Station Road Ward 12 phase 1", org="Other dept||x", tid="2026_X_1")
    rel = store.related(a)
    assert [r["key"] for r in rel] == [b]
    assert store.related("missing") == []


# ---- the web endpoints --------------------------------------------------------------------------------------------

@pytest.fixture
def ctx2(monkeypatch):
    store = SeenStore(":memory:")
    env = settings()
    fake = {"maha": FakeClient([tender(90, "maha"), tender(91, "maha")]), "delhi": FakeClient([tender(92, "delhi")])}
    manager = RunManager(env, store, lambda p, s: fake[p.key], FakeSender(), background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, manager)
    c = TestClient(app)
    c.store = store
    login(type("X", (), {"client": c}))
    c.post("/api/runs", json={})
    c.key = c.get("/api/tenders").json()["items"][0]["key"]
    return c


def test_every_ai_endpoint_needs_login_and_explains_when_off(ctx2, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for path, body in (("/api/tenders/summary", {"key": ctx2.key}), ("/api/tenders/brief", {"key": ctx2.key}), ("/api/tenders/document", {"key": ctx2.key, "text": "x"}),
                       ("/api/ask", {"question": "roads"}), ("/api/ai/test", None), ("/api/ai/score", None)):
        assert ctx2.post(path, json=body).status_code in (503,), path
    st = ctx2.get("/api/ai/status").json()
    assert st["configured"] is False and st["unlabelled"] >= 1
    anon = TestClient(ctx2.app)
    assert anon.post("/api/ask", json={"question": "x"}).status_code == 401 and anon.get("/api/ai/status").status_code == 401


def test_ask_returns_only_filters_the_page_accepts(ctx2, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "ask", lambda q, portals, facets, today: {"portal": ["maha", "mars"], "min_value": 5e7, "closing_within": 7, "status": "bogus", "sort": "value",
                                                                       "q": "  drain ", "evil": "x", "explanation": "Maharashtra, over 5 crore"})
    r = ctx2.post("/api/ask", json={"question": "big drains in Maharashtra closing this week"}).json()
    assert r["filters"] == {"portal": ["maha"], "min_value": 5e7, "closing_within": 7, "q": "drain"} and r["sort"] == "value"
    assert ctx2.post("/api/ask", json={"question": "  "}).status_code == 422


def test_brief_uses_the_profile_and_the_document_and_is_saved(ctx2, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctx2.put("/api/settings", json={"company_profile": "Class A contractor, roads, Maharashtra, up to 10 crore"})
    seen = []
    monkeypatch.setattr(ai, "read_document", lambda t, text, pdf: {"summary": "s", "requirements": ["5 years"], "financial": [], "dates": [], "documents": [], "penalties": [], "red_flags": []})
    monkeypatch.setattr(ai, "brief", lambda t, profile, doc: seen.append((profile, doc)) or {"recommendation": "Bid", "headline": "h", "reasons": ["r"], "risks": [], "checks": ["c"]})
    assert ctx2.post("/api/tenders/document", json={"key": ctx2.key, "text": "NIT text"}).json()["doc"]["requirements"] == ["5 years"]
    first = ctx2.post("/api/tenders/brief", json={"key": ctx2.key}).json()
    again = ctx2.post("/api/tenders/brief", json={"key": ctx2.key}).json()
    assert first["cached"] is False and again["cached"] is True and len(seen) == 1
    assert "roads" in seen[0][0] and seen[0][1]["requirements"] == ["5 years"]
    detail = ctx2.get("/api/tenders/detail", params={"key": ctx2.key}).json()
    assert detail["brief"]["recommendation"] == "Bid" and detail["doc"]["summary"] == "s" and detail["related"] == []


def test_test_button_and_score_now(ctx2, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(ai, "ping", lambda: "OK")
    assert ctx2.post("/api/ai/test").json()["ok"] is True
    monkeypatch.setattr(ai, "classify", lambda chunk, p: {i: {"work_type": "Buildings", "civil": True, "fit": None, "reason": ""} for i in range(len(chunk))})
    r = ctx2.post("/api/ai/score").json()
    assert r["scored"] >= 1 and r["unlabelled"] == 0
    monkeypatch.setattr(ai, "ping", lambda: (_ for _ in ()).throw(ai.AIError("The Anthropic API key was rejected.")))
    assert ctx2.post("/api/ai/test").status_code == 502


# ---- settings and the digest --------------------------------------------------------------------------------------

def test_profile_and_ai_switches_are_validated():
    out = appsettings.clean({"company_profile": "  Roads  ", "ai_hide_noncivil": True}, ["maha"])
    assert out == {"company_profile": "Roads", "ai_hide_noncivil": True}
    for bad in ({"company_profile": "x" * 2001}, {"ai_digest_intro": "yes"}):
        with pytest.raises(appsettings.SettingsError):
            appsettings.clean(bad, [])


def test_digest_shows_the_intro_only_when_there_is_one():
    from tender_digest import collector
    report = collector.PortalReport(settings().portals[0], search_url="https://x/search")
    t = tender(1)
    with_intro = digest.build(NOW, [t], [], [report], intro="Look at T1 first.")
    assert "Look at T1 first." in with_intro.html and "TODAY'S HIGHLIGHTS" in with_intro.text
    assert "highlights" not in digest.build(NOW, [t], [], [report]).html.lower()
