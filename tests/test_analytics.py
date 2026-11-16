from datetime import datetime, timedelta
from itertools import product

import pytest
from fastapi.testclient import TestClient

from tender_digest import runner, web
from tender_digest.config import Portal
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import DIMENSIONS, NONE, SeenStore, drill

from test_web import PASSWORD, FakeSender, settings

NOW = datetime(2026, 11, 14, 13, 0)  # a fixed "now" so day and week boundaries are predictable
PORTALS = ["maha", "delhi", "up"]
CATS = ["Civil Works", "Civil Works - Roads", "Construction Works", "", "Civil Works - Bridges"]
DEPTS = ["Public Works Department||Div 1", "Municipal Corporation||Ward 3", "Irrigation Dept", "Zilla Parishad||Block B"]
LOCS = ["Pune", "Nashik", "", "Delhi"]
VALUES = [None, 50_000.0, 250_000.0, 4_000_000.0, 60_000_000.0, 300_000_000.0, 99_999.0, 100_000.0]


def tender(i: int) -> Tender:
    return Tender(
        portal=PORTALS[i % 3], tender_id=f"T{i}", title=f"Road work {i}", reference="", organisation=DEPTS[i % 4],
        published=NOW - timedelta(days=i % 9, hours=3), closing=None if i % 17 == 0 else NOW + timedelta(days=(i % 45) - 6, hours=(i % 5) * 3),
        opening=None, value=VALUES[i % 8], category=CATS[i % 5], location=LOCS[i % 4],
    )


@pytest.fixture
def store():
    s = SeenStore(":memory:")
    for i in range(120):
        s.keep(tender(i), NOW - timedelta(days=i % 12, hours=i % 7))
    s.skip("maha:SKIPPED", NOW)
    return s


def count(store, f):
    return store.search(NOW, f, limit=1)[1]


# ---- the promise that matters: a bar's count equals what you see when you open it -------------------------------

@pytest.mark.parametrize("dim", list(DIMENSIONS))
def test_clicking_a_bar_opens_exactly_as_many_tenders_as_it_says(store, dim):
    for status in ("all", "open"):
        result = store.pivot(NOW, {"status": status}, dim, None, top=40)
        assert result["total"]["count"] == count(store, {"status": status})
        for row in result["rows"]:
            if row["drill"] is None:
                continue
            f = {"status": status, **row["drill"]}
            assert count(store, f) == row["count"], (dim, row["key"], status)


@pytest.mark.parametrize("group,split", [(g, s) for g, s in product(DIMENSIONS, DIMENSIONS) if g != s])
def test_every_cell_of_every_combination_drills_to_its_own_count(store, group, split):
    result = store.pivot(NOW, {"status": "all"}, group, split, top=40, top_split=40)
    seen = 0
    for row in result["rows"]:
        assert sum(p["count"] for p in row["parts"].values()) == row["count"]  # the cells add up to the bar
        for key, part in row["parts"].items():
            if "drill" in part:
                seen += 1
                assert count(store, {"status": "all", **part["drill"]}) == part["count"], (group, split, row["key"], key)
    assert seen > 0 or result["total"]["count"] == 0
    assert sum(r["count"] for r in result["rows"]) == result["total"]["count"]


def test_totals_values_and_averages(store):
    r = store.pivot(NOW, {"status": "all"}, "portal")
    assert r["total"]["count"] == 120
    for row in r["rows"]:
        tenders = [tender(i) for i in range(120) if PORTALS[i % 3] == row["key"]]
        known = [t.value for t in tenders if t.value is not None]
        assert row["count"] == len(tenders) and row["value"] == sum(known) and row["avg"] == pytest.approx(sum(known) / len(known))


def test_small_groups_fold_into_other_and_nothing_is_lost(store):
    r = store.pivot(NOW, {"status": "all"}, "department", None, top=2)
    assert len(r["rows"]) == 3 and r["rows"][-1]["key"] == "Other" and r["rows"][-1]["drill"] is None
    assert sum(x["count"] for x in r["rows"]) == 120
    r2 = store.pivot(NOW, {"status": "all"}, "portal", "category", top=12, top_split=2)
    assert len(r2["splits"]) == 3 and r2["splits"][-1] == "Other"
    for row in r2["rows"]:
        assert sum(p["count"] for p in row["parts"].values()) == row["count"]


def test_ordering(store):
    value_order = [r["key"] for r in store.pivot(NOW, {"status": "all"}, "value_band")["rows"]]
    assert value_order == ["Under 1 lakh", "1 to 10 lakh", "10 lakh to 1 crore", "1 to 10 crore", "Over 10 crore", "Not stated"]
    weeks = [r["key"] for r in store.pivot(NOW, {"status": "all"}, "closing_week", top=12)["rows"]]
    assert weeks == sorted(weeks) and all(len(w) == 10 and datetime.fromisoformat(w).weekday() == 0 for w in weeks if w != "No date")
    sized = [r["count"] for r in store.pivot(NOW, {"status": "all"}, "category")["rows"] if r["key"] != "Not stated"]
    assert sized == sorted(sized, reverse=True)
    closes = [r["key"] for r in store.pivot(NOW, {"status": "all"}, "closes_in")["rows"]]
    assert closes == [k for k in ["Closed", "Within 24 hours", "1 to 3 days", "4 to 7 days", "8 to 14 days", "15 to 30 days", "Over 30 days", "No date"] if k in closes]


def test_closes_in_bucket_boundaries():
    s = SeenStore(":memory:")
    cases = {"A": (NOW - timedelta(hours=1), "Closed"), "B": (NOW + timedelta(hours=23), "Within 24 hours"), "C": (NOW + timedelta(days=1, hours=1), "1 to 3 days"),
             "D": (NOW + timedelta(days=3, hours=23), "1 to 3 days"), "E": (NOW + timedelta(days=4, hours=1), "4 to 7 days"), "F": (NOW + timedelta(days=40), "Over 30 days")}
    for k, (closing, _) in cases.items():
        s.keep(Tender("maha", k, "x", "", "Org", NOW, closing, None, 1.0), NOW)
    got = {r["key"] for r in s.pivot(NOW, {"status": "all"}, "closes_in")["rows"]}
    assert got == {"Closed", "Within 24 hours", "1 to 3 days", "4 to 7 days", "Over 30 days"}
    for key, (_, bucket) in cases.items():
        rows = s.pivot(NOW, {"status": "all"}, "closes_in")["rows"]
        match = next(r for r in rows if r["key"] == bucket)
        assert match["count"] >= 1


def test_unknown_dimension_is_refused(store):
    with pytest.raises(ValueError):
        store.pivot(NOW, {}, "password")
    with pytest.raises(ValueError):
        store.pivot(NOW, {}, "portal", "nope")


# ---- filters ----------------------------------------------------------------------------------------------------

def test_multi_value_and_not_stated_filters(store):
    assert count(store, {"status": "all", "portal": ["maha", "delhi"]}) == 80
    assert count(store, {"status": "all", "portal": "up"}) == 40  # a single string still works
    civil = count(store, {"status": "all", "category": ["Civil Works", "Construction Works"]})
    assert civil == sum(1 for i in range(120) if CATS[i % 5] in ("Civil Works", "Construction Works"))
    none = count(store, {"status": "all", "category": [NONE]})
    assert none == sum(1 for i in range(120) if CATS[i % 5] == "") and none > 0
    both = count(store, {"status": "all", "category": [NONE, "Civil Works"]})
    assert both == none + sum(1 for i in range(120) if CATS[i % 5] == "Civil Works")
    assert count(store, {"status": "all", "department": ["Irrigation Dept"]}) == 30  # no '||' in the name
    assert count(store, {"status": "all", "location": [NONE]}) == 30


def test_value_range_filters(store):
    assert count(store, {"status": "all", "no_value": True}) == sum(1 for i in range(120) if VALUES[i % 8] is None)
    assert count(store, {"status": "all", "min_value": 100_000, "value_lt": 1_000_000}) == sum(1 for i in range(120) if VALUES[i % 8] in (250_000.0, 100_000.0))


def test_date_ranges_are_inclusive_of_the_whole_end_day(store):
    day = (NOW + timedelta(days=5)).strftime("%Y-%m-%d")
    expected = sum(1 for i in range(120) if tender(i).closing and tender(i).closing.strftime("%Y-%m-%d") == day)
    assert expected > 0
    assert count(store, {"status": "all", "closing_from": day, "closing_to": day}) == expected


def test_closing_within_and_default_open(store):
    assert count(store, {"closing_within": 7}) == sum(1 for i in range(120) if tender(i).closing and NOW <= tender(i).closing <= NOW + timedelta(days=7))
    assert count(store, {}) < count(store, {"status": "all"})


def test_search_text_is_literal_not_a_pattern(store):
    assert count(store, {"status": "all", "q": "%"}) == 0
    assert count(store, {"status": "all", "q": "_"}) == 0
    assert count(store, {"status": "all", "q": "Road work 7"}) >= 1
    assert count(store, {"status": "all", "q": "' OR 1=1 --"}) < count(store, {"status": "all"})  # words in a search, never SQL


def test_stats_follow_the_filters(store):
    everything = store.stats(NOW)
    only_up = store.stats(NOW, 3, {"portal": ["up"]})
    assert only_up["kpi"]["total_kept"] == 40 and everything["kpi"]["total_kept"] == 120
    assert {p["portal"] for p in only_up["by_portal"]} == {"up"}
    assert sum(b["count"] for b in only_up["by_value"]) == only_up["kpi"]["open"]
    assert only_up["kpi"]["closing_week"] <= only_up["kpi"]["open"]
    assert "avg_value" in only_up["kpi"] and "new_yesterday" in only_up["kpi"]
    assert store.stats(NOW, 3, {"status": "closed", "q": "zzz"})["kpi"]["total_kept"] == 0  # a status in the filters is ignored; the search is not


# ---- stars -------------------------------------------------------------------------------------------------------

def test_starring(store):
    assert store.set_starred("maha:T0", True, NOW) and store.set_starred("delhi:T1", True, NOW)
    assert not store.set_starred("maha:NOPE", True, NOW) and not store.set_starred("maha:SKIPPED", True, NOW)
    assert count(store, {"status": "all", "starred": True}) == 2
    rows, _ = store.search(NOW, {"status": "all", "starred": True}, limit=10)
    assert all(r["starred"] for r in rows)
    store.set_starred("maha:T0", False, NOW)
    assert count(store, {"status": "all", "starred": True}) == 1
    assert store.get_tender("delhi:T1")["starred"] is True and store.get_tender("maha:T0")["starred"] is False
    assert store.stats(NOW)["kpi"]["starred"] <= 1


# ---- the API -----------------------------------------------------------------------------------------------------

@pytest.fixture
def client(store):
    env = settings()
    env.portals = [Portal(k, k.title(), k.upper(), f"https://{k}/nicgep") for k in PORTALS]
    mgr = RunManager(env, store, background=False, sender=FakeSender())
    app = web.create_app(web.AppConfig(PASSWORD, "k", False, run_scheduler=False), env, store, mgr)
    c = TestClient(app)
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_api_needs_login_for_every_new_endpoint(store):
    env = settings()
    c = TestClient(web.create_app(web.AppConfig(PASSWORD, "k", False, run_scheduler=False), env, store))
    for method, path in (("get", "/api/analytics?group_by=portal"), ("get", "/api/facets"), ("get", "/api/tenders/detail?key=x"), ("get", "/api/views")):
        assert getattr(c, method)(path).status_code == 401, path
    assert c.post("/api/tenders/star", json={"key": "x", "starred": True}).status_code == 401
    assert c.put("/api/views", json={"views": []}).status_code == 401


def test_api_analytics_and_filters_together(client, monkeypatch):
    monkeypatch.setattr(web, "local_now", lambda tz="Asia/Kolkata": NOW)
    r = client.get("/api/analytics?group_by=portal&split_by=category&status=all").json()
    assert r["total"]["count"] == 120 and {x["key"] for x in r["rows"]} == set(PORTALS) and r["labels"]["maha"] == "MAHA"
    r2 = client.get("/api/analytics?group_by=department&status=all&portal=up&portal=maha&category=Civil+Works").json()
    expected = sum(1 for i in range(120) if PORTALS[i % 3] in ("up", "maha") and CATS[i % 5] == "Civil Works")
    assert r2["total"]["count"] == expected
    # the drill filters come back in a form the API itself accepts, and give the same count
    cell = r["rows"][0]["parts"][next(iter(r["rows"][0]["parts"]))]
    qs = "&".join(f"{k}={v}" if not isinstance(v, list) else "&".join(f"{k}={x}" for x in v) for k, v in cell["drill"].items())
    assert client.get(f"/api/tenders?status=all&{qs}").json()["total"] == cell["count"]
    assert client.get("/api/analytics?group_by=portal&split_by=portal&status=all").json()["split_by"] is None  # same on both axes: no split


@pytest.mark.parametrize("bad", [
    "/api/analytics?group_by=password", "/api/analytics?group_by=portal&split_by=nope", "/api/analytics", "/api/analytics?group_by=portal&top=1",
    "/api/tenders?closing_from=tomorrow", "/api/tenders?seen_to=2026-13-1", "/api/tenders?status=weird", "/api/tenders?min_value=-5",
    "/api/tenders?closing_within=0", "/api/stats?status=zzz",
])
def test_api_rejects_bad_input(client, bad):
    assert client.get(bad).status_code == 422


def test_api_oversized_filter_lists_are_refused(client):
    assert client.get("/api/tenders?" + "&".join(f"category=c{i}" for i in range(60))).status_code == 422
    assert client.get("/api/tenders?department=" + "x" * 300).status_code == 422


def test_api_facets_stats_detail(client, monkeypatch):
    monkeypatch.setattr(web, "local_now", lambda tz="Asia/Kolkata": NOW)
    f = client.get("/api/facets").json()
    assert {"portal", "category", "department", "location", "work_type"} == set(f)
    assert all(x["count"] > 0 for x in f["category"]) and f["portal"][0]["label"] in ("Maha", "Delhi", "Up")
    s = client.get("/api/stats?portal=up").json()
    assert {p["portal"] for p in s["by_portal"]} == {"up"} and s["by_portal"][0]["short"] == "UP"
    d = client.get("/api/tenders/detail?key=maha:T0").json()
    assert d["tender_id"] == "T0" and d["portal_name"] == "Maha" and "opening" in d and d["search_url"].startswith("https://maha")
    assert client.get("/api/tenders/detail?key=maha:SKIPPED").status_code == 404


def test_api_star_and_filter(client):
    assert client.post("/api/tenders/star", json={"key": "maha:T0", "starred": True}).json() == {"key": "maha:T0", "starred": True}
    assert client.post("/api/tenders/star", json={"key": "maha:ZZ", "starred": True}).status_code == 404
    assert client.get("/api/tenders?status=all&starred=true").json()["total"] == 1
    assert client.get("/api/tenders?status=all&starred=true").json()["items"][0]["starred"] is True
    client.post("/api/tenders/star", json={"key": "maha:T0", "starred": False})
    assert client.get("/api/tenders?status=all&starred=true").json()["total"] == 0


def test_api_saved_views(client):
    assert client.get("/api/views").json() == {"views": []}
    views = [{"id": "a", "name": " Roads over 1 crore ", "filters": {"category": ["Civil Works - Roads"], "min_value": 10000000, "status": "open"}}]
    r = client.put("/api/views", json={"views": views}).json()
    assert r["views"][0]["name"] == "Roads over 1 crore" and client.get("/api/views").json() == r
    assert client.put("/api/views", json={"views": [{"id": "a", "name": "", "filters": {}}]}).status_code == 422
    assert client.put("/api/views", json={"views": [{"id": "a", "name": "x" * 41, "filters": {}}]}).status_code == 422
    assert client.put("/api/views", json={"views": [{"id": "a", "name": "ok", "filters": {"__proto__": 1}}]}).status_code == 422
    assert client.put("/api/views", json={"views": [{"id": "a", "name": "ok", "filters": {"password": "x"}}]}).status_code == 422
    assert client.get("/api/views").json() == r  # a refused save changes nothing
    assert len(client.put("/api/views", json={"views": [{"id": str(i), "name": "v", "filters": {}} for i in range(45)]}).json()["views"]) == 30


def test_api_config_lists_dimensions(client):
    cfg = client.get("/api/config").json()
    dims = {d["key"]: d for d in cfg["dimensions"]}
    assert set(dims) == set(DIMENSIONS) and dims["closing_day"]["time"] is True and dims["portal"]["time"] is False


def test_drill_for_unknown_or_other_is_empty():
    assert drill("portal", "Other", NOW) == {} and drill("category", "", NOW) == {}
    assert drill("closes_in", "no such bucket", NOW) == {}
