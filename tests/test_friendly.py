"""Small things that make the app easier for someone new: it says whether nothing has been collected or only nothing matches, and the first thing to do is first."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import web
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD, env_settings


@pytest.fixture
def app_():
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    return type("A", (), {"c": c, "store": store})


def tender(n, days):
    real = datetime.now()
    return Tender(portal="maha", tender_id=f"T{n}", title=f"Road work {n}", reference="", description=f"Road work {n}", organisation="PWD||Nashik", category="Works", location="Nashik",
                  value=5e6, emd=5e4, published=real - timedelta(days=30), closing=real + timedelta(days=days), opening=None)


def test_an_empty_installation_says_nothing_has_been_collected(app_):
    r = app_.c.get("/api/tenders").json()
    assert r["total"] == 0 and r["kept"] == 0


def test_when_everything_has_closed_it_says_tenders_exist_but_none_are_open(app_):
    app_.store.keep(tender(1, -3), datetime.now())                          # closed three days ago
    r = app_.c.get("/api/tenders").json()                                    # the usual list shows open tenders only
    assert r["total"] == 0 and r["kept"] == 1
    assert app_.c.get("/api/tenders?status=all").json()["total"] == 1


def test_when_tenders_exist_but_the_filters_match_none_the_count_still_says_so(app_):
    app_.store.keep(tender(2, 5), datetime.now())
    r = app_.c.get("/api/tenders?q=zzzzzz").json()
    assert r["total"] == 0 and r["kept"] == 1
    assert "kept" not in app_.c.get("/api/tenders").json()                  # not sent when there is a list to show


def test_the_count_follows_new_tenders(app_):
    assert app_.store.kept_count() == 0
    app_.store.keep(tender(3, 5), datetime.now())
    assert app_.store.kept_count() == 1


def test_the_first_thing_to_do_on_the_checklist_is_to_collect_tenders(app_):
    steps = app_.c.get("/api/setup").json()["steps"]
    assert steps[0]["key"] == "run" and "20 to 40 minutes" in steps[0]["why"]
    assert {s["key"] for s in steps} == {"run", "profile", "facts", "vault", "email", "bid", "catalogue"}
