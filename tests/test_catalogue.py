"""The tick-list of known portals: every entry must be usable as typed, adding many at once is safe, and only portals that can be read get switched on."""
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from tender_digest import appsettings, catalogue, web
from tender_digest.config import load_portals
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD, Prober, env_settings


@pytest.fixture
def ctx():
    store, env, prober = SeenStore(":memory:"), env_settings(), Prober()
    manager = RunManager(env, store, background=False)
    client = TestClient(web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, manager, prober))
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return type("Ctx", (), {"store": store, "env": env, "prober": prober, "client": client})


# ---- the list itself ---------------------------------------------------------------------------------------------

def test_the_catalogue_is_a_sensible_size_and_has_no_repeats():
    ids = [catalogue.key_of(n) for n, *_ in catalogue.ENTRIES]
    urls = [u.rstrip("/").lower() for _, _, u, _ in catalogue.ENTRIES]
    assert len(ids) >= 25 and len(set(ids)) == len(ids) and len(set(urls)) == len(urls)


def test_nothing_in_it_repeats_a_built_in_portal():
    built_in = {p.base_url.rstrip("/").lower() for p in load_portals()}
    assert built_in and not built_in & {u.rstrip("/").lower() for _, _, u, _ in catalogue.ENTRIES}


@pytest.mark.parametrize("name,state,url,group", catalogue.ENTRIES)
def test_every_entry_would_be_accepted_by_the_add_a_portal_form(name, state, url, group, monkeypatch):
    monkeypatch.setenv("PORTAL_HOST_SUFFIXES", ".gov.in,.nic.in,.nprocure.com")        # the Gujarat entry needs this one, and says so
    got = appsettings.clean_portal({"name": name, "state": state, "base_url": url}, set())
    assert got["key"] == catalogue.key_of(name) and got["base_url"] == url.rstrip("/") and urlparse(url).scheme == "https" and len(name) <= 80


def test_entries_marked_as_the_common_software_use_its_address_shape():
    for name, _state, url, group in catalogue.ENTRIES:
        if group == catalogue._GEPNIC:
            assert url.endswith("/nicgep"), name


def test_find_and_listing():
    assert catalogue.find("kerala-etenders")["base_url"] == "https://etenders.kerala.gov.in/nicgep" and catalogue.find("nowhere") is None
    rows = catalogue.listing({"https://ETENDERS.kerala.gov.in/nicgep/"})                  # case and a trailing slash do not hide a duplicate
    assert [r["added"] for r in rows if r["id"] == "kerala-etenders"] == [True] and sum(r["added"] for r in rows) == 1


# ---- over the web -------------------------------------------------------------------------------------------------

def test_login_is_required(ctx):
    ctx.client.post("/api/logout")
    assert ctx.client.get("/api/portals/catalogue").status_code == 401
    assert ctx.client.post("/api/portals/bulk", json={"ids": ["kerala-etenders"]}).status_code == 401


def test_the_catalogue_marks_what_is_already_added(ctx):
    rows = ctx.client.get("/api/portals/catalogue").json()["entries"]
    assert len(rows) == len(catalogue.ENTRIES) and not any(r["added"] for r in rows)
    ctx.client.post("/api/portals", json={"name": "Kerala", "base_url": "https://etenders.kerala.gov.in/nicgep"})
    rows = ctx.client.get("/api/portals/catalogue").json()["entries"]
    assert [r["id"] for r in rows if r["added"]] == ["kerala-etenders"]


def test_adding_many_saves_them_without_contacting_any_or_switching_them_on(ctx):
    ids = ["kerala-etenders", "tamil-nadu-tn-tenders", "haryana-etenders"]
    r = ctx.client.post("/api/portals/bulk", json={"ids": ids})
    assert r.status_code == 200 and len(r.json()["added"]) == 3 and r.json()["skipped"] == []
    assert ctx.prober.calls == []                                                          # a slow or dead portal cannot hold up this request
    assert ctx.client.get("/api/settings").json()["portals"] == ["maha"]                    # nothing runs until it has been checked
    assert len(ctx.client.get("/api/portals").json()["portals"]) == 4
    assert sum(r["added"] for r in ctx.client.get("/api/portals/catalogue").json()["entries"]) == 3


def test_repeats_unknown_ids_and_bad_requests_are_reported_not_fatal(ctx):
    ctx.client.post("/api/portals/bulk", json={"ids": ["kerala-etenders"]})
    r = ctx.client.post("/api/portals/bulk", json={"ids": ["kerala-etenders", "nowhere", "kerala-etenders", "haryana-etenders"]}).json()
    assert len(r["added"]) == 1 and {s["id"]: s["reason"] for s in r["skipped"]} == {"kerala-etenders": "Already added", "nowhere": "Not in the list of known portals"}
    for bad in ({}, {"ids": []}, {"ids": "kerala-etenders"}, {"ids": [1, 2]}, {"ids": ["x"] * 41}):
        assert ctx.client.post("/api/portals/bulk", json=bad).status_code == 422, bad


def test_a_portal_whose_address_is_not_allowed_is_skipped_with_the_reason(ctx):
    r = ctx.client.post("/api/portals/bulk", json={"ids": ["gujarat-nprocure"]}).json()        # .nprocure.com is not an allowed ending unless an administrator adds it
    assert r["added"] == [] and "Only addresses ending in" in r["skipped"][0]["reason"]


def test_checking_with_enable_switches_on_only_the_readable_ones(ctx):
    bad = "https://tntenders.gov.in/nicgep"
    ctx.prober.answers[bad] = {"kind": "blocked", "detail": "refused", "departments": 0, "tenders": 0}
    ctx.client.post("/api/portals/bulk", json={"ids": ["kerala-etenders", "tamil-nadu-tn-tenders"]})
    good_r = ctx.client.post("/api/portals/kerala-etenders/check?enable=true").json()
    bad_r = ctx.client.post("/api/portals/tamil-nadu-tn-tenders/check?enable=true").json()
    assert good_r["kind"] == "ok" and good_r["enabled"] and bad_r["kind"] == "blocked" and not bad_r["enabled"]
    assert ctx.client.get("/api/settings").json()["portals"] == ["maha", "kerala-etenders"]
    assert [m["key"] for m in ctx.client.get("/api/portals").json()["manual"]] == ["tamil-nadu-tn-tenders"]
    ctx.client.post("/api/portals/kerala-etenders/check?enable=true")                      # checking again does not list it twice
    assert ctx.client.get("/api/settings").json()["portals"] == ["maha", "kerala-etenders"]


def test_a_plain_check_still_does_not_switch_anything_on(ctx):
    ctx.client.post("/api/portals/bulk", json={"ids": ["kerala-etenders"]})
    assert ctx.client.post("/api/portals/kerala-etenders/check").json()["enabled"] is False


def test_adding_the_whole_catalogue_at_once(ctx, monkeypatch):
    monkeypatch.setenv("PORTAL_HOST_SUFFIXES", ".gov.in,.nic.in,.nprocure.com")
    ids = [r["id"] for r in ctx.client.get("/api/portals/catalogue").json()["entries"]][:40]
    r = ctx.client.post("/api/portals/bulk", json={"ids": ids}).json()
    assert len(r["added"]) == len(catalogue.ENTRIES) and r["skipped"] == []
