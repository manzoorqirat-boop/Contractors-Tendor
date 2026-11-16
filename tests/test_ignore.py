"""Not interested (hide and restore, whole departments), the size of work you take, safer pruning, and the clean-up preview."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import alerts, web
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, settings

NOW = datetime(2026, 11, 16, 10, 0)


def t(n, org="Public Works Department||Nashik Division", value=2e7, closing_days=10, title=None):
    return Tender(portal="maha", tender_id=f"T{n}", title=title or f"Road work {n}", reference="", description=title or f"Road work {n}", organisation=org, category="Works",
                  location="Nashik", value=value, emd=(value / 50 if value else None), published=NOW - timedelta(days=1), closing=NOW + timedelta(days=closing_days), opening=None)


@pytest.fixture
def store():
    s = SeenStore(":memory:")
    for i, args in enumerate([("PWD||A", 1e7), ("PWD||B", 5e7), ("Water Resources||C", 2e8), ("Irrigation||D", None)], 1):
        s.keep(t(i, args[0], args[1]), NOW)
    return s


def keys(store, **f):
    return sorted(r["tender_id"] for r in store.search(NOW, {"status": "all", **f}, "closing", 100, 0)[0])


# ---- not interested ---------------------------------------------------------------------------------------------

def test_hidden_tenders_leave_every_list_and_count_and_come_back(store):
    assert keys(store) == ["T1", "T2", "T3", "T4"]
    assert store.hide(["maha:T2", "maha:T3", "maha:nope"], NOW, "meera") == 2                  # an unknown tender is ignored
    assert keys(store) == ["T1", "T4"] and keys(store, hidden="only") == ["T2", "T3"] and keys(store, hidden="all") == ["T1", "T2", "T3", "T4"]
    assert store.hide(["maha:T2"], NOW) == 0                                                    # already hidden: nothing changes
    assert sum(f["count"] for f in store.facets(NOW)["portal"]) == 2                           # the filter counts agree
    assert store.unhide(["maha:T2"]) == 1 and keys(store) == ["T1", "T2", "T4"]


def test_hidden_tenders_are_not_emailed_or_in_alerts(store):
    store.hide(["maha:T1"], NOW)
    assert sorted(x.tender_id for x in store.pending(NOW)) == ["T2", "T3", "T4"]
    assert sorted(x.tender_id for x in store.closing_soon(NOW, 30)) == ["T2", "T3", "T4"]
    out = alerts.matches(store, NOW, [{"alert": True, "name": "All", "filters": {}}], [f"maha:T{i}" for i in (1, 2, 3, 4)])
    assert [i["tender_id"] for i in out[0]["items"]].count("T1") == 0 and out[0]["count"] == 3


def test_a_whole_department_can_be_hidden_and_shown_again(store):
    store.keep(t(9, "PWD||Z"), NOW)
    assert store.department_of("maha:T1") == "PWD"
    assert store.mute_department("PWD", NOW, "meera") and not store.mute_department("PWD", NOW) and not store.mute_department("  ", NOW)
    assert keys(store) == ["T3", "T4"] and keys(store, hidden="only") == ["T1", "T2", "T9"]    # new tenders from it are hidden too
    store.keep(t(10, "PWD||Y"), NOW)
    assert "T10" not in keys(store)
    assert [m["dept"] for m in store.muted_departments()] == ["PWD"] and store.muted_departments()[0]["tenders"] == 4
    assert store.unmute_department("PWD") and not store.unmute_department("PWD") and len(keys(store)) == 6


def test_tenders_with_no_department_are_not_lost_by_the_mute_rule(store):
    store.keep(t(11, ""), NOW)
    store.mute_department("PWD", NOW)
    assert "T11" in keys(store)


# ---- the size of work you take --------------------------------------------------------------------------------------

def test_size_range_keeps_tenders_with_no_stated_value(store):
    assert keys(store, size_min=2e7, size_max=1e8) == ["T2", "T4"]                              # T4 has no value: kept, never silently dropped
    assert keys(store, size_max=2e7) == ["T1", "T4"] and keys(store, size_min=1e8) == ["T3", "T4"]


def test_a_saved_view_can_use_the_size_range_in_the_daily_email(store):
    views = [{"alert": True, "name": "Mine", "filters": {"fits_me": True}}]
    new = [f"maha:T{i}" for i in (1, 2, 3, 4)]
    assert alerts.view_filters({"fits_me": True})["fits_me"] is True and "fits_me" not in alerts.view_filters({"fits_me": "no"})
    out = alerts.matches(store, NOW, views, new, size={"size_min": 2e7, "size_max": 1e8})
    assert sorted(i["tender_id"] for i in out[0]["items"]) == ["T2", "T4"]
    assert alerts.matches(store, NOW, views, new)[0]["count"] == 4                              # no range set: everything


# ---- pruning ----------------------------------------------------------------------------------------------------

def test_pruning_never_removes_an_open_or_starred_tender():
    s = SeenStore(":memory:")
    s.keep(t(1, closing_days=-100), NOW - timedelta(days=200))                                  # closed long ago
    s.keep(t(2, closing_days=30), NOW - timedelta(days=200))                                    # found long ago, still open
    s.keep(t(3, closing_days=-100), NOW - timedelta(days=200))
    s.set_starred("maha:T3", True, NOW)                                                          # closed, but starred
    s.skip("maha:old-placeholder", NOW - timedelta(days=200))
    s.hide(["maha:T1"], NOW)
    s.prune(NOW, 45)
    assert sorted(r["tender_id"] for r in s.search(NOW, {"status": "all", "hidden": "all"}, "closing", 100, 0)[0]) == ["T2", "T3"]
    assert not s.is_seen("maha:old-placeholder") and s.search(NOW, {"hidden": "only", "status": "all"}, "closing", 5, 0)[1] == 0   # the hidden mark went with its tender


# ---- clean-up -------------------------------------------------------------------------------------------------------

def test_cleanup_shows_what_it_would_do_and_protects_stars_and_bids():
    s = SeenStore(":memory:")
    from tender_digest.bids import BidBook
    book = BidBook(s)
    for i in range(1, 6):
        s.keep(t(i, closing_days=-60), NOW - timedelta(days=90))
    s.keep(t(6, closing_days=-5), NOW - timedelta(days=30))                                      # closed only 5 days ago
    s.keep(t(7, closing_days=20), NOW)                                                           # still open
    s.set_starred("maha:T2", True, NOW)
    book.create_from_tender("maha:T3", NOW)
    s.hide(["maha:T4"], NOW)
    s.skip("maha:p1", NOW - timedelta(days=90))
    s.skip("maha:p2", NOW - timedelta(days=2))
    pre = s.cleanup_preview(NOW, 30)
    assert pre["closed"] == 3 and pre["ignored_among_them"] == 1 and pre["placeholders"] == 1 and pre["kept_because_starred_or_bid"] == 2 and len(pre["oldest"]) == 3
    assert s.cleanup_preview(NOW, 365)["closed"] == 0                                           # nothing closed more than a year ago
    done = s.cleanup(NOW, 30)
    assert done == {"closed": 3, "placeholders": 1}
    assert sorted(r["tender_id"] for r in s.search(NOW, {"status": "all", "hidden": "all"}, "closing", 100, 0)[0]) == ["T2", "T3", "T6", "T7"]
    assert s.is_seen("maha:p2") and s.cleanup(NOW, 30) == {"closed": 0, "placeholders": 0}      # a second run finds nothing
    assert book.get(book.id_for_key("maha:T3"))["tender_id"] == "T3"                            # the bid is untouched


def test_cleanup_works_on_a_store_with_no_bid_workspace_yet():
    s = SeenStore(":memory:")
    s.keep(t(1, closing_days=-60), NOW - timedelta(days=90))
    assert s.cleanup_preview(NOW, 30)["closed"] == 1 and s.cleanup(NOW, 30)["closed"] == 1


# ---- over the web -----------------------------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    for i, args in enumerate([("PWD||A", 1e7), ("PWD||B", 5e7), ("Water Resources||C", 2e8), ("Irrigation||D", None)], 1):
        store.keep(Tender(portal="maha", tender_id=f"T{i}", title=f"Road work {i}", reference="", description=f"Road work {i}", organisation=args[0], category="Works", location="Nashik",
                          value=args[1], emd=None, published=datetime.now() - timedelta(days=1), closing=datetime.now() + timedelta(days=10), opening=None), datetime.now())
    env = settings()
    a = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, RunManager(env, store, lambda p, s: None, FakeSender(), background=False))
    owner = TestClient(a)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})

    def person(name, role):
        owner.post("/api/users", json={"username": name, "role": role, "password": "temporary-pass-1"})
        c = TestClient(a)
        c.post("/api/login", json={"username": name, "password": "temporary-pass-1"})
        c.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
        return c

    return type("A", (), {"owner": owner, "person": person, "store": store})


def ids(c, **params):
    return sorted(x["tender_id"] for x in c.get("/api/tenders", params={"status": "all", **params}).json()["items"])


def test_hide_restore_and_the_ignored_view_over_the_api(app):
    c = app.owner
    key = c.get("/api/tenders", params={"status": "all", "q": "T1"}).json()["items"][0]["key"]
    assert c.post("/api/tenders/hide", json={"keys": [key]}).json() == {"changed": 1, "hidden": True, "muted": []}
    assert ids(c) == ["T2", "T3", "T4"] and ids(c, hidden="only") == ["T1"]
    assert c.get("/api/mutes").json()["hidden_tenders"] == 1
    assert c.post("/api/tenders/hide", json={"keys": [key], "hidden": False}).json()["changed"] == 1 and ids(c) == ["T1", "T2", "T3", "T4"]
    assert c.get("/api/tenders", params={"hidden": "everything"}).status_code == 422


def test_hiding_with_the_department_and_managing_hidden_departments(app):
    c = app.owner
    key = c.get("/api/tenders", params={"status": "all", "q": "T2"}).json()["items"][0]["key"]
    r = c.post("/api/tenders/hide", json={"keys": [key], "mute_department": True}).json()
    assert r["muted"] == ["PWD"] and ids(c) == ["T3", "T4"]
    listed = c.get("/api/mutes").json()["departments"]
    assert [d["dept"] for d in listed] == ["PWD"] and listed[0]["tenders"] == 2
    assert c.post("/api/mutes", json={"department": "PWD", "muted": False}).json()["departments"] == []
    assert ids(c) == ["T1", "T3", "T4"] and ids(c, hidden="only") == ["T2"]                      # T2 was also hidden by itself, so it stays hidden until restored
    assert c.post("/api/mutes", json={"department": "Irrigation"}).json()["changed"] is True and ids(c) == ["T1", "T3"]
    assert any(e["action"].startswith("Hid or showed a whole department") for e in c.get("/api/audit").json()["entries"])


def test_the_size_range_and_the_fits_me_filter(app):
    c = app.owner
    assert c.get("/api/company-size").json() == {"min": None, "max": None}
    assert ids(c, fits_me="true") == ["T1", "T2", "T3", "T4"]                                   # no range set: everything
    assert c.put("/api/company-size", json={"min": 2e7, "max": 1e8}).json() == {"min": 2e7, "max": 1e8}
    assert ids(c, fits_me="true") == ["T2", "T4"] and ids(c) == ["T1", "T2", "T3", "T4"]
    assert c.put("/api/company-size", json={"min": 5e7, "max": 1e7}).status_code == 422
    assert c.put("/api/company-size", json={"min": -1}).status_code == 422 and c.put("/api/company-size", json={"min": 1e14}).status_code == 422
    assert c.put("/api/company-size", json={"min": 0, "max": None}).json() == {"min": None, "max": None}
    assert ids(c, fits_me="true") == ["T1", "T2", "T3", "T4"]


def test_who_may_do_what(app):
    viewer, bidder = app.person("vera", "viewer"), app.person("raj", "bidder")
    key = app.owner.get("/api/tenders", params={"status": "all", "q": "T1"}).json()["items"][0]["key"]
    assert viewer.post("/api/tenders/hide", json={"keys": [key]}).status_code == 403 and viewer.put("/api/company-size", json={"min": 1}).status_code == 403
    assert viewer.get("/api/mutes").status_code == 200 and viewer.get("/api/company-size").status_code == 200
    assert bidder.post("/api/tenders/hide", json={"keys": [key]}).status_code == 200 and bidder.put("/api/company-size", json={"max": 1e8}).status_code == 200
    assert bidder.get("/api/cleanup/preview").status_code == 403 and bidder.post("/api/cleanup", json={"days": 30}).status_code == 403


def test_cleanup_over_the_api(app):
    c = app.owner
    for i in range(3):
        app.store.keep(Tender(portal="maha", tender_id=f"OLD{i}", title=f"Old {i}", reference="", description="old", organisation="PWD||X", category="Works", location="x",
                              value=1e6, emd=None, published=datetime.now() - timedelta(days=200), closing=datetime.now() - timedelta(days=90), opening=None), datetime.now() - timedelta(days=200))
    pre = c.get("/api/cleanup/preview", params={"days": 30}).json()
    assert pre["closed"] == 3 and pre["tenders_now"] == 7 and pre["days"] == 30
    for bad in (6, 4000, "x"):
        assert c.get("/api/cleanup/preview", params={"days": bad}).status_code == 422
    assert c.post("/api/cleanup", json={"days": 3}).status_code == 422
    assert c.post("/api/cleanup", json={"days": 30}).json() == {"closed": 3, "placeholders": 0}
    assert c.get("/api/cleanup/preview", params={"days": 30}).json()["closed"] == 0 and len(ids(c)) == 4
    assert any(e["action"] == "Cleaned up closed tenders" for e in c.get("/api/audit").json()["entries"])
