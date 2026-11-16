"""Sessions that really end, a login limiter nobody can use to lock the owner out, request size limits, a health check that
looks at storage, and checks on tenders typed in by hand."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import web
from tender_digest.runner import local_now
from tender_digest.store import SeenStore
from tests.test_bids import app_ctx  # noqa: F401  (a logged-in client, with one collected tender)
from tests.test_web import PASSWORD, ctx, login, settings  # noqa: F401


# ---- sessions ------------------------------------------------------------------------------------------------

def test_a_copied_cookie_stops_working_after_logout(ctx):
    login(ctx)
    cookie = ctx.client.cookies.get("td_session")
    assert ctx.client.get("/api/stats").status_code == 200
    ctx.client.post("/api/logout")
    stolen = TestClient(ctx.app, cookies={"td_session": cookie})
    assert stolen.get("/api/stats").status_code == 401
    assert stolen.get("/api/me").json() == {"authenticated": False}


def test_each_login_is_its_own_session(ctx):
    login(ctx)
    first = ctx.client.cookies.get("td_session")
    other = TestClient(ctx.app)
    assert other.post("/api/login", json={"password": PASSWORD}).status_code == 200
    ctx.client.post("/api/logout")  # ends the first session only
    assert TestClient(ctx.app, cookies={"td_session": first}).get("/api/stats").status_code == 401
    assert other.get("/api/stats").status_code == 200


def test_changing_the_password_signs_everyone_out(ctx):
    login(ctx)
    cookie = ctx.client.cookies.get("td_session")
    same_store_new_password = web.create_app(web.AppConfig("a different password", "s3cret", cookie_secure=False, run_scheduler=False), ctx.env, ctx.store, ctx.manager)
    assert TestClient(same_store_new_password, cookies={"td_session": cookie}).get("/api/stats").status_code == 401


def test_a_token_without_a_session_is_refused(ctx):
    old_style = web.make_token("s3cret")  # no session id: the kind issued before sessions were kept
    assert TestClient(ctx.app, cookies={"td_session": old_style}).get("/api/stats").status_code == 401


def test_an_idle_session_ends_but_use_keeps_it_alive():
    store = SeenStore(":memory:")
    store.session_start("a", 1000.0, 10_000)
    assert store.session_touch("a", 1000.0 + 50, 100)           # used within the idle window
    assert store.session_touch("a", 1000.0 + 140, 100)          # 90 s since the last use: still fine
    assert not store.session_touch("a", 1000.0 + 140 + 101, 100)  # idle too long
    assert not store.session_touch("a", 1000.0 + 140 + 102, 100)  # and it is gone, not just refused once
    assert not store.session_touch("never", 5.0, 100)


# ---- login limiter ---------------------------------------------------------------------------------------------

def test_strangers_cannot_lock_the_owner_out(ctx, monkeypatch):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    for i in range(60):  # more failures than the old global cap of 40, each from a different address
        r = ctx.client.post("/api/login", json={"password": "nope"}, headers={"X-Forwarded-For": f"10.1.{i}.1"})
        assert r.status_code == 401, i
    assert ctx.client.post("/api/login", json={"password": PASSWORD}, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200


def test_a_faked_forwarded_for_header_does_not_dodge_the_per_address_limit(ctx):
    # The real proxy appends the address it saw at the END; whatever the caller puts at the start must not change the bucket.
    codes = [ctx.client.post("/api/login", json={"password": "nope"}, headers={"X-Forwarded-For": f"{i}.2.2.2, 198.51.100.7"}).status_code for i in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]


def test_many_wrong_guesses_slow_logins_down_without_refusing_them():
    limiter = web.LoginLimiter(per_client=8, overall=5, window=600)
    for i in range(5):
        limiter.failed(f"c{i}", now=100.0)
    assert limiter.under_attack(now=101.0) and not limiter.blocked("someone-else", now=101.0)
    assert not limiter.under_attack(now=100.0 + 601)


# ---- request size ----------------------------------------------------------------------------------------------

def test_a_big_body_is_refused_before_it_is_read(ctx):
    login(ctx)
    big = "x" * (2 * 1024 * 1024)
    r = ctx.client.put("/api/views", json={"views": [], "pad": big})
    assert r.status_code == 413 and "too large" in r.json()["detail"]
    ok = ctx.client.put("/api/views", json={"views": []})
    assert ok.status_code == 200


def test_a_big_body_with_no_declared_length_is_cut_off_too(ctx):
    login(ctx)

    def chunks():
        for _ in range(3):
            yield b'{"views": [], "pad": "' + b"x" * (512 * 1024) + b'"}'

    r = ctx.client.put("/api/views", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_uploads_get_room_for_a_10mb_file_but_not_more(app_ctx):
    c = app_ctx.client
    r = c.post("/api/vault", content=b"{" + b" " * (17 * 1024 * 1024) + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert c.post("/api/vault", json={"name": "GST certificate", "kind": "other"}).status_code == 200


# ---- health and storage ----------------------------------------------------------------------------------------

def test_health_fails_when_the_database_cannot_be_read(ctx, monkeypatch):
    assert ctx.client.get("/api/health").json() == {"ok": True}
    monkeypatch.setattr(ctx.store, "ping", lambda: (_ for _ in ()).throw(RuntimeError("disk gone")))
    r = ctx.client.get("/api/health")
    assert r.status_code == 503 and r.json() == {"ok": False}  # no detail for the public


def test_health_fails_when_the_data_folder_is_not_writable(tmp_path, monkeypatch):
    store = SeenStore(str(tmp_path / "s.db"))
    assert web.storage_ok(store, str(tmp_path / "s.db"))
    monkeypatch.setattr(web.os, "access", lambda path, mode: False)  # as if the volume were mounted read-only
    assert not web.storage_ok(store, str(tmp_path / "s.db"))


def test_railway_without_a_volume_is_called_out():
    on_railway = {"RAILWAY_ENVIRONMENT_NAME": "production"}
    assert "No volume" in web.storage_warning("/data/tender_state.db", on_railway)
    assert web.storage_warning("/data/tender_state.db", {**on_railway, "RAILWAY_VOLUME_MOUNT_PATH": "/data"}) == ""
    assert "not stored on the volume" in web.storage_warning("/app/tender_state.db", {**on_railway, "RAILWAY_VOLUME_MOUNT_PATH": "/data"})
    assert web.storage_warning("/data/x.db", {**on_railway, "ALLOW_EPHEMERAL_STORAGE": "true"}) == ""
    assert web.storage_warning("./local.db", {}) == "" and web.storage_warning(":memory:", on_railway) == ""  # not on Railway: nothing to say


def test_the_warning_reaches_the_page(ctx):
    login(ctx)
    assert ctx.client.get("/api/config").json()["storage_warning"] == ""


# ---- tenders typed in by hand ----------------------------------------------------------------------------------

def listing(**kw):
    return {"tender_id": "NIT/HARD/1", "description": "Repair of internal roads", "organisation": "Municipal Corporation", "closing": "2099-12-02T15:00", "value": 5_000_000, "read_document": False, **kw}


def test_impossible_listings_are_refused_with_a_reason(app_ctx):
    c = app_ctx.client
    yesterday = (local_now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    cases = {
        "passed": (listing(closing=yesterday), "already passed"),
        "opens_before_close": (listing(opening="2099-12-01T10:00"), "cannot open before"),
        "published_after_close": (listing(published="2099-12-03T10:00"), "after the closing"),
        "emd_over_value": (listing(emd=9_000_000), "larger than the tender value"),
        "markup_in_id": (listing(tender_id="<img src=x onerror=alert(1)>"), "cannot contain"),
        "control_chars": (listing(tender_id="NIT\x07/1"), "cannot contain"),
    }
    for name, (body, why) in cases.items():
        r = c.post("/api/tenders/manual", json=body)
        assert r.status_code == 422 and why in r.json()["detail"], (name, r.status_code, r.text)
    assert c.get("/api/tenders/manual/check", params={"tender_id": "NIT/HARD/1"}).json()["existing"] == []  # nothing half-saved
    assert c.post("/api/tenders/manual", json=listing(opening="2099-12-03T11:00", published="2099-11-01T10:00", emd=100_000)).status_code == 200


def test_a_date_with_a_timezone_is_converted_not_cut_short(app_ctx):
    c = app_ctx.client
    key = c.post("/api/tenders/manual", json=listing(closing="2099-12-02T10:00:00Z", opening="2099-12-03T10:00:00+00:00")).json()["key"]
    d = c.get("/api/tenders/detail", params={"key": key}).json()
    assert d["closing"].startswith("2099-12-02T15:30"), d["closing"]  # 10:00 UTC is 15:30 in India
    assert d["opening"].startswith("2099-12-03T15:30")


def test_editing_checks_the_same_rules_but_lets_you_fix_a_closed_tender(app_ctx):
    c = app_ctx.client
    key = c.post("/api/tenders/manual", json=listing(emd=100_000)).json()["key"]
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"emd": 9_000_000}).status_code == 422
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"opening": "2099-12-01T09:00"}).status_code == 422
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"closing": "2001-01-01T10:00"}).status_code == 422
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"location": "Nagpur", "emd": 200_000}).status_code == 200
    # A tender that has closed can still be corrected, as long as its closing date is not moved to the past.
    past = app_ctx.store
    t = past.tender_object(key)
    t.closing = (local_now().replace(tzinfo=None) - timedelta(days=2)).replace(second=0, microsecond=0)
    past.keep(t, local_now())
    assert c.patch("/api/tenders/manual", params={"key": key}, json={"location": "Pune", "closing": t.closing.isoformat(timespec="minutes")}).status_code == 200
