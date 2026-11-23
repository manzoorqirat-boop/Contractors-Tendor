"""Fixes from the code review: dates that cannot be right, text that is too long is refused (not quietly cut), a misspelt setting is an error, and a mistake in the
WhatsApp number list is reported."""
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tender_digest import appsettings, web, whatsapp
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD, env_settings


@pytest.fixture
def app_(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED", "")
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    real = datetime.now()
    store.keep(Tender(portal="maha", tender_id="T1", title="Road work", reference="", description="Road", organisation="PWD||Nashik", category="Works", location="Nashik", value=5e6,
                      emd=5e4, published=real, closing=real + timedelta(days=5), opening=real + timedelta(days=7)), real)
    key = store.search(real, {}, "closing", 1, 0)[0][0]["key"]
    bid = c.post("/api/bids", json={"key": key}).json()["id"]
    return type("A", (), {"c": c, "bid": f"/api/bids/{bid}", "store": store})


def test_a_date_that_cannot_be_right_is_refused_on_a_bid_as_it_is_on_a_hand_added_tender(app_):
    for bad in ("1900-01-01", "2999-12-31", "0001-01-01"):
        r = app_.c.patch(app_.bid, json={"closing": bad})
        assert r.status_code == 422 and "year" in r.json()["detail"], bad
    assert app_.c.patch(app_.bid, json={"closing": "2027-03-01"}).status_code == 200
    assert app_.c.patch(app_.bid, json={"closing": ""}).status_code == 200            # clearing a date is still allowed


def test_text_that_is_too_long_is_refused_with_the_limit_and_nothing_is_cut(app_):
    r = app_.c.patch(app_.bid, json={"notes": "n" * 4001})
    assert r.status_code == 422 and "at most 4000" in r.json()["detail"] and "4001" in r.json()["detail"]
    assert app_.c.patch(app_.bid, json={"notes": "n" * 4000}).status_code == 200      # exactly the limit is fine
    assert len(app_.c.get(app_.bid).json()["notes"]) == 4000
    assert app_.c.patch(app_.bid, json={"owner": "o" * 61}).status_code == 422
    assert app_.c.post(app_.bid + "/items", json={"text": "x" * 301}).status_code == 422
    assert app_.c.post(app_.bid + "/items", json={"text": "x" * 300}).status_code == 200


def test_a_checklist_line_made_from_the_portal_is_still_trimmed_not_refused(app_):
    from tender_digest.bids import BidBook         # the generated lines go through add_item(source != "manual")
    book = BidBook(app_.store)
    bid_id = int(app_.bid.rsplit("/", 1)[1])
    made = book.add_item(bid_id, "p" * 500, "task", datetime.now(), source="portal")
    assert len(made["text"]) == 300


def test_a_hand_added_tender_keeps_long_real_titles_and_refuses_absurd_ones(app_):
    body = {"tender_id": "M1", "title": "t" * 300, "description": "d", "closing": (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d")}
    assert app_.c.post("/api/tenders/manual", json=body).status_code == 200
    r = app_.c.post("/api/tenders/manual", json={**body, "tender_id": "M2", "title": "t" * 301})
    assert r.status_code == 422 and "at most 300" in r.json()["detail"]
    r = app_.c.post("/api/tenders/manual", json={**body, "tender_id": "M3", "description": "d" * 1001})
    assert r.status_code == 422 and "at most 1000" in r.json()["detail"]


def test_a_misspelt_setting_is_an_error_not_a_silent_save(app_):
    r = app_.c.put("/api/settings", json={"send_hour": 7})
    assert r.status_code == 422 and "send_hour" in r.json()["detail"]
    assert app_.c.put("/api/settings", json={"send_time": "07:30"}).status_code == 200
    assert app_.c.put("/api/settings", json={"send_time": "07:30", "zzz": 1}).status_code == 422       # one bad name spoils the whole save, so nothing is half-applied
    assert app_.c.get("/api/settings").json()["send_time"] == "07:30"


def test_every_setting_the_validator_reads_can_be_changed():
    """Adding a setting to the validator without adding it to the allowed names would make it impossible to save: this keeps the two lists in step."""
    source = Path(appsettings.__file__).read_text()
    body = source[source.index("def clean(payload"):]
    read = set(re.findall(r'"(\w+)" in payload', body)) | set(re.findall(r'\(\s*"(\w+)",\s*"[A-Z][^"]*"', body)) | {"schedule_enabled", "send_empty", "ai_digest_intro", "ai_hide_noncivil"}
    assert read <= appsettings.CHANGEABLE, read - appsettings.CHANGEABLE


@pytest.mark.parametrize("entry,expect", [
    ("919876543210:admin", []),
    ("919876543210:owner", ["Ignored …3210", "owner"]),
    ("no-number:viewer", ["Ignored", "no phone number"]),
    ("9876543210:viewer", ["…3210", "10 digits", "country code"]),
    ("12345:viewer", ["…2345", "does not look like a full phone number"]),
])
def test_a_mistake_in_the_number_list_is_reported_without_showing_the_whole_number(entry, expect):
    cfg = whatsapp.Config.from_env({"WHATSAPP_ALLOWED": entry})
    text = " | ".join(cfg.problems)
    assert all(e in text for e in expect) and (not expect) == (not cfg.problems)
    assert "919876543210" not in text and "9876543210" not in text.replace("…3210", "")


def test_the_status_page_lists_the_problems(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED", "919876543210:admin, 9811111111:viewer, 919822222222:boss")
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    c.post("/api/login", json={"username": "admin", "password": PASSWORD})
    s = c.get("/api/whatsapp/status").json()
    assert len(s["problems"]) == 2 and any("boss" in p for p in s["problems"]) and any("10 digits" in p for p in s["problems"])
    assert [n["ending"] for n in s["numbers"]] == ["3210", "1111"]
