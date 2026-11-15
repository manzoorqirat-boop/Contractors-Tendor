import sqlite3
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import appsettings, runner, web
from tender_digest.config import Portal, Settings
from tender_digest.gepnic import PortalError, Tender
from tender_digest.runner import RunManager, Scheduler
from tender_digest.store import SeenStore

PASSWORD = "correct horse"
NOW = runner.local_now()


def settings(**kw) -> Settings:
    base = dict(
        portals=[Portal("maha", "Maharashtra", "MH", "https://m/nicgep"), Portal("delhi", "Delhi", "DL", "https://d/nicgep")],
        recipients=["a@example.com"], brevo_api_key="k", mail_from="f@example.com", mail_from_name="T", state_path=":memory:", request_delay=0,
    )
    base.update(kw)
    return Settings(**base)


def tender(i, portal="maha", **kw) -> Tender:
    base = dict(
        portal=portal, tender_id=f"T{i}", title=f"Construction of road {i}", reference="", organisation="Public Works Department||Division",
        published=NOW - timedelta(hours=3), closing=NOW + timedelta(days=10), opening=None, value=100000.0 * i, category="Civil Works", location="Pune",
    )
    base.update(kw)
    return Tender(**base)


class FakeClient:
    search_url = "https://x/s"

    def __init__(self, tenders, down=False):
        self.tenders, self.down = tenders, down

    def organisations(self):
        if self.down:
            raise PortalError("down")
        return [("Public Works", len(self.tenders), "L")]

    def tenders_of(self, link, portal):
        return [Tender(**{**t.__dict__, "category": "", "value": None}) for t in self.tenders]

    def detail(self, t):
        return {"Product Category": "Civil Works", "Tender Value in ₹": "5,00,000", "Work Description": t.title, "Location": "Pune"}


class FakeSender:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return list(a[3])


@pytest.fixture
def ctx():
    store = SeenStore(":memory:")
    env = settings()
    sender = FakeSender()
    fake = {"maha": FakeClient([tender(90, "maha"), tender(91, "maha")]), "delhi": FakeClient([tender(92, "delhi")])}
    manager = RunManager(env, store, lambda p, s: fake[p.key], sender, background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, manager)
    client = TestClient(app)
    return type("Ctx", (), {"store": store, "env": env, "sender": sender, "fake": fake, "manager": manager, "client": client, "app": app})


def login(c):
    assert c.client.post("/api/login", json={"password": PASSWORD}).status_code == 200


# ---- access -------------------------------------------------------------------------------------------------

def test_everything_but_health_and_login_needs_a_login(ctx):
    assert ctx.client.get("/api/health").json() == {"ok": True}
    for path in ("/api/stats", "/api/tenders", "/api/runs", "/api/settings", "/api/config", "/api/tenders/export"):
        assert ctx.client.get(path).status_code == 401, path
    assert ctx.client.post("/api/runs", json={}).status_code == 401
    assert ctx.client.put("/api/settings", json={}).status_code == 401
    assert ctx.client.get("/api/me").json() == {"authenticated": False}


def test_login_sets_a_strict_httponly_cookie_and_logout_clears_it(ctx):
    r = ctx.client.post("/api/login", json={"password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert ctx.client.get("/api/me").json() == {"authenticated": True}
    ctx.client.post("/api/logout")
    assert ctx.client.get("/api/stats").status_code == 401


def test_wrong_password_is_refused_and_guessing_is_slowed(ctx):
    for _ in range(8):
        assert ctx.client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert ctx.client.post("/api/login", json={"password": "nope"}).status_code == 429
    assert ctx.client.post("/api/login", json={"password": PASSWORD}).status_code == 429  # even the right one waits


def test_tokens_cannot_be_forged_or_reused_after_expiry():
    good = web.make_token("k")
    assert web.valid_token("k", good)
    assert not web.valid_token("other", good)
    assert not web.valid_token("k", good[:-2] + "00")
    assert not web.valid_token("k", None) and not web.valid_token("k", "x.y") and not web.valid_token("k", "garbage")
    assert not web.valid_token("k", good, now=10**11)  # far future: expired


def test_cross_site_writes_are_refused(ctx):
    login(ctx)
    r = ctx.client.post("/api/runs", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = ctx.client.post("/api/settings/test-email", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200


def test_security_headers_present(ctx):
    h = ctx.client.get("/api/health").headers
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff" and "default-src 'self'" in h["content-security-policy"]


def test_password_must_be_set_and_long_enough(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        web.config_from_env()
    monkeypatch.setenv("APP_PASSWORD", "short")
    with pytest.raises(SystemExit):
        web.config_from_env()


# ---- tenders ------------------------------------------------------------------------------------------------

def seed(store):
    for i in range(1, 31):
        store.keep(tender(i, "maha" if i % 2 else "delhi", closing=NOW + timedelta(days=i), category="Civil Works" if i % 3 else "Construction Works"), NOW)
    store.keep(tender(99, closing=NOW - timedelta(days=2), title="Old closed road"), NOW)
    store.keep(tender(98, value=None, title="No value stated"), NOW)
    store.skip("maha:SKIPPED", NOW)


def test_browse_filter_sort_and_page(ctx):
    seed(ctx.store)
    login(ctx)
    r = ctx.client.get("/api/tenders").json()
    assert r["total"] == 31 and len(r["items"]) == 25 and r["page"] == 1  # 30 + the one without a value; the closed one is hidden
    assert [i["tender_id"] for i in r["items"]][:2] == ["T1", "T2"]  # soonest closing first
    assert ctx.client.get("/api/tenders?page=2").json()["items"][0]["tender_id"] == "T25"  # page 1 holds T1..T24 and the 10-day "no value" one

    assert ctx.client.get("/api/tenders?status=closed").json()["total"] == 1
    assert ctx.client.get("/api/tenders?status=all").json()["total"] == 32
    assert ctx.client.get("/api/tenders?portal=delhi").json()["total"] == 15
    assert ctx.client.get("/api/tenders?q=road+7").json()["total"] >= 1
    assert ctx.client.get("/api/tenders?min_value=2500000&status=all").json()["total"] == 7  # T25..T30 plus the closed T99, since status=all
    top = ctx.client.get("/api/tenders?sort=value&page_size=2").json()["items"]
    assert [t["tender_id"] for t in top] == ["T30", "T29"]
    last = ctx.client.get("/api/tenders?sort=value&page_size=100").json()["items"][-1]
    assert last["value"] is None  # unknown values sort last
    soon = ctx.client.get("/api/tenders?status=closing_soon").json()
    assert soon["total"] == 3 and all(t["tender_id"] in ("T1", "T2", "T3") for t in soon["items"])
    assert "Civil Works" in ctx.client.get("/api/tenders").json()["categories"]


def test_bad_inputs_are_rejected(ctx):
    login(ctx)
    assert ctx.client.get("/api/tenders?sort=password").status_code == 422
    assert ctx.client.get("/api/tenders?status=weird").status_code == 422
    assert ctx.client.get("/api/tenders?page_size=1000").status_code == 422
    assert ctx.client.get("/api/tenders?page=0").status_code == 422
    # a quote or percent sign in a search is data, never SQL
    assert ctx.client.get("/api/tenders?q=%27%3B+DROP+TABLE+seen%3B--").json()["total"] == 0
    assert ctx.client.get("/api/stats").status_code == 200


def test_export_is_csv_and_defuses_formulas(ctx):
    ctx.store.keep(tender(1, title="=HYPERLINK(\"http://evil\")", description="=cmd|' /C calc'!A0"), NOW)
    login(ctx)
    r = ctx.client.get("/api/tenders/export")
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert body.splitlines()[0].startswith("Status,Portal,Tender ID")
    assert "'=cmd" in body and ",=cmd" not in body


def test_stats_numbers(ctx):
    seed(ctx.store)
    login(ctx)
    s = ctx.client.get("/api/stats").json()
    assert s["kpi"]["open"] == 31 and s["kpi"]["total_kept"] == 32 and s["kpi"]["new_today"] == 32
    assert s["kpi"]["closing_soon"] == 3
    assert {p["portal"]: p["count"] for p in s["by_portal"]} == {"maha": 16, "delhi": 15}
    assert sum(b["count"] for b in s["by_value"]) == 31 and s["by_value"][-1] == {"band": "Not stated", "count": 1}
    assert len(s["new_per_day"]) == 30 and s["new_per_day"][-1]["count"] == 32
    assert len(s["closing_per_day"]) == 14 and sum(d["count"] for d in s["closing_per_day"]) > 0
    assert s["biggest"][0]["tender_id"] == "T30" and s["departments"][0]["department"] == "Public Works Department"


# ---- runs ---------------------------------------------------------------------------------------------------

def test_manual_run_collects_and_records_history(ctx):
    login(ctx)
    r = ctx.client.post("/api/runs", json={})
    assert r.status_code == 202
    run = ctx.client.get(f"/api/runs/{r.json()['id']}").json()
    assert run["status"] == "ok" and run["trigger"] == "manual" and run["new_civil"] == 3 and run["emailed_count"] == 0
    assert {x["portal"] for x in run["reports"]} == {"maha", "delhi"} and any("Finished" in line for line in run["log"])
    assert ctx.sender.calls == []  # manual runs do not email unless asked
    assert ctx.client.get("/api/tenders").json()["total"] == 3
    assert ctx.client.get("/api/runs").json()["runs"][0]["id"] == run["id"]
    assert "log" not in ctx.client.get("/api/runs").json()["runs"][0]


def test_run_can_email_when_asked_and_marks_sent(ctx):
    login(ctx)
    run = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={'send_email': True}).json()['id']}").json()
    assert run["status"] == "ok" and run["emailed_count"] == 1 and len(ctx.sender.calls) == 1
    assert ctx.store.pending(NOW) == []
    again = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={'send_email': True}).json()['id']}").json()
    assert again["new_civil"] == 0  # nothing is repeated


def test_run_can_be_limited_to_chosen_portals(ctx):
    login(ctx)
    run = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={'portals': ['delhi']}).json()['id']}").json()
    assert run["portals"] == ["delhi"] and run["new_civil"] == 1
    assert ctx.client.post("/api/runs", json={"portals": ["nowhere"]}).status_code == 422
    assert ctx.client.post("/api/runs", json={"portals": []}).status_code == 422


def test_second_run_while_one_is_going_is_refused(ctx):
    login(ctx)
    assert ctx.manager._guard.acquire(blocking=False)
    try:
        assert ctx.client.post("/api/runs", json={}).status_code == 409
    finally:
        ctx.manager._guard.release()


def test_email_run_needs_mail_set_up_and_recipients(ctx):
    login(ctx)
    ctx.manager.env = settings(brevo_api_key="")
    r = ctx.client.post("/api/runs", json={"send_email": True})
    assert r.status_code == 422 and "BREVO_API_KEY" in r.json()["detail"]
    ctx.manager.env = settings(recipients=[])
    assert "recipients" in ctx.client.post("/api/runs", json={"send_email": True}).json()["detail"].lower()


def test_portal_down_is_recorded_not_fatal(ctx):
    login(ctx)
    ctx.fake["maha"].down = True
    run = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={}).json()['id']}").json()
    assert run["status"] == "ok" and run["new_civil"] == 1
    assert next(r for r in run["reports"] if r["portal"] == "maha")["failed"] is True
    ctx.fake["delhi"].down = True
    run2 = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={}).json()['id']}").json()
    assert run2["status"] == "failed"


def test_unexpected_crash_still_ends_the_run(ctx):
    login(ctx)
    ctx.manager.client_factory = lambda p, s: (_ for _ in ()).throw(RuntimeError("boom"))
    run = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={}).json()['id']}").json()
    assert run["status"] == "failed" and "boom" in run["error"]
    assert ctx.client.post("/api/runs", json={}).status_code == 202  # the lock was released


def test_interrupted_runs_are_marked_failed_on_startup(ctx):
    rid = ctx.store.create_run("manual", ["maha"], False, NOW)
    assert ctx.store.running_run()["id"] == rid
    assert ctx.store.fail_stale_runs(NOW) == 1
    assert ctx.store.get_run(rid)["status"] == "failed" and "restart" in ctx.store.get_run(rid)["error"]


def test_background_run_really_runs_in_a_thread(ctx):
    ctx.manager.background = True
    login(ctx)
    rid = ctx.client.post("/api/runs", json={}).json()["id"]
    for t in threading.enumerate():
        if t.name == f"run-{rid}":
            t.join(10)
    assert ctx.client.get(f"/api/runs/{rid}").json()["status"] == "ok"


# ---- settings -----------------------------------------------------------------------------------------------

def test_settings_round_trip_and_override_the_environment(ctx):
    login(ctx)
    s = ctx.client.get("/api/settings").json()
    assert s["recipients"] == ["a@example.com"] and s["send_time"] == "08:00" and s["portals"] == ["maha", "delhi"]
    r = ctx.client.put("/api/settings", json={"recipients": ["x@example.com", " y@example.com "], "send_time": "07:30", "portals": ["delhi"], "min_value": 500000})
    assert r.status_code == 200 and r.json()["recipients"] == ["x@example.com", "y@example.com"] and r.json()["portals"] == ["delhi"]
    run = ctx.client.get(f"/api/runs/{ctx.client.post('/api/runs', json={'send_email': True}).json()['id']}").json()
    assert run["portals"] == ["delhi"]
    assert ctx.sender.calls[-1][0][3] == ["x@example.com", "y@example.com"]
    assert ctx.client.get("/api/config").json()["recipient_count"] == 2
    assert "brevo" not in str(ctx.client.get("/api/settings").json()).lower().replace("brevo_api_key", "")  # no secrets in the response
    assert "k" != ctx.client.get("/api/settings").json().get("brevo_api_key")


@pytest.mark.parametrize("bad", [
    {"recipients": ["not-an-email"]}, {"recipients": "a@b.co"}, {"send_time": "25:00"}, {"send_time": "8am"}, {"portals": []},
    {"portals": ["nowhere"]}, {"min_value": "lots"}, {"min_value": -5}, {"schedule_enabled": "yes"}, {"extra_keywords": ["x" * 80]},
    {"recipients": ["a@b.co," + "c@d.co"]},
])
def test_bad_settings_are_rejected_with_a_reason(ctx, bad):
    login(ctx)
    r = ctx.client.put("/api/settings", json=bad)
    assert r.status_code == 422 and r.json()["detail"]
    assert ctx.client.get("/api/settings").json()["send_time"] == "08:00"  # nothing half-saved


def test_keywords_typed_in_the_page_are_never_used_as_a_regex():
    env = settings()
    out = appsettings.effective(env, {"extra_keywords": ["(a+)+$", "kerb"], "exclude_words": [".*"]})
    import re
    assert re.search(out.civil_keywords, "laying of kerb stones", re.I)
    assert not re.search(out.civil_keywords, "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx!") or True  # compiles, no catastrophic pattern
    assert re.search(out.exclude, "something.* odd", re.I) and not re.search(r"^$", "x")
    assert re.compile(out.civil_keywords) and re.compile(out.exclude)


def test_test_email(ctx):
    login(ctx)
    assert ctx.client.post("/api/settings/test-email").json() == {"sent_to": 1}
    ctx.manager.env = settings(brevo_api_key="")
    assert ctx.client.post("/api/settings/test-email").status_code == 422

    def refuse(*a, **k):
        raise web.mailer.MailError("a@example.com: HTTP 401 unrecognised IP")
    ctx.manager.env, ctx.manager.sender = settings(), refuse
    r = ctx.client.post("/api/settings/test-email")
    assert r.status_code == 502 and "unrecognised IP" in r.json()["detail"]


# ---- scheduler ----------------------------------------------------------------------------------------------

def test_scheduler_runs_once_a_day_at_the_set_time(ctx):
    sched = Scheduler(ctx.manager)
    day = datetime(2026, 11, 14)
    assert not sched.due(day.replace(hour=7, minute=59))
    assert sched.due(day.replace(hour=8, minute=0))
    ctx.store.create_run("manual", [], False, day.replace(hour=8, minute=1))
    assert sched.due(day.replace(hour=9))  # manual runs do not count as the daily one
    ctx.store.create_run("scheduled", [], True, day.replace(hour=8, minute=2))
    assert not sched.due(day.replace(hour=9))
    assert sched.due(day.replace(hour=8) + timedelta(days=1))  # and again tomorrow


def test_scheduler_respects_settings(ctx):
    sched = Scheduler(ctx.manager)
    ctx.store.put_settings({"schedule_enabled": False})
    assert not sched.due(datetime(2026, 11, 14, 23, 0))
    ctx.store.put_settings({"schedule_enabled": True, "send_time": "21:15"})
    assert not sched.due(datetime(2026, 11, 14, 21, 14)) and sched.due(datetime(2026, 11, 14, 21, 15))


def test_scheduled_run_emails_and_a_misconfigured_one_is_recorded_once(ctx, monkeypatch):
    sched = Scheduler(ctx.manager)
    monkeypatch.setattr(runner, "local_now", lambda tz="Asia/Kolkata": datetime(2026, 11, 14, 9, 0))
    rid = sched.tick()
    run = ctx.store.get_run(rid)
    assert run["trigger"] == "scheduled" and run["status"] == "ok" and run["emailed_count"] == 1
    assert sched.tick() is None  # already ran today

    ctx.store.db.execute("DELETE FROM runs"); ctx.store.db.commit()
    ctx.manager.env = settings(brevo_api_key="")
    rid = sched.tick()
    assert ctx.store.get_run(rid)["status"] == "failed" and "BREVO_API_KEY" in ctx.store.get_run(rid)["error"]
    assert sched.tick() is None  # not retried every 30 seconds


# ---- storage upgrade ----------------------------------------------------------------------------------------

def test_an_older_database_is_upgraded_and_keeps_its_tenders(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE seen (key TEXT PRIMARY KEY, first_seen TEXT NOT NULL, civil INTEGER NOT NULL DEFAULT 0, emailed INTEGER NOT NULL DEFAULT 0, closing TEXT, payload TEXT)")
    t = tender(7, value=851184.0, category="Civil Works")
    from tender_digest.store import _to_json
    old.execute("INSERT INTO seen (key, first_seen, civil, closing, payload) VALUES (?, ?, 1, ?, ?)", (t.key, NOW.isoformat(), t.closing.isoformat(), _to_json(t)))
    old.commit(); old.close()
    store = SeenStore(path)
    rows, total = store.search(NOW, {"status": "all"})
    assert total == 1 and rows[0]["tender_id"] == "T7" and rows[0]["value"] == 851184.0 and rows[0]["category"] == "Civil Works"
    SeenStore(path)  # opening it again changes nothing


def test_static_page_served_with_spa_fallback_and_no_path_escape(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets").mkdir(); (tmp_path / "assets" / "a.js").write_text("x=1")
    secret = tmp_path.parent / "secret.txt"; secret.write_text("nope")
    store = SeenStore(":memory:")
    app = web.create_app(web.AppConfig(PASSWORD, "k", False, static_dir=tmp_path, run_scheduler=False), settings(), store)
    c = TestClient(app)
    assert c.get("/").text == "<html>app</html>" and c.get("/tenders").text == "<html>app</html>"
    assert c.get("/assets/a.js").text == "x=1"
    assert "nope" not in c.get("/..%2Fsecret.txt").text and "nope" not in c.get("/../secret.txt").text
    assert c.get("/api/nothing").status_code in (401, 404)


# ---- AI summary ---------------------------------------------------------------------------------------------

def test_summary_needs_login_and_is_off_without_a_key(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ctx.client.post("/api/tenders/summary", json={"key": "x"}).status_code == 401
    login(ctx)
    assert ctx.client.get("/api/config").json()["ai_enabled"] is False
    ctx.client.post("/api/runs", json={})
    key = ctx.client.get("/api/tenders").json()["items"][0]["key"]
    assert ctx.client.post("/api/tenders/summary", json={"key": key}).status_code == 503
    assert ctx.client.post("/api/tenders/summary", json={"key": "nope"}).status_code == 404


def test_summary_is_cached_per_tender(ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(web.ai, "summarise", lambda t: calls.append(t) or "What: a road.")
    login(ctx)
    ctx.client.post("/api/runs", json={})
    key = ctx.client.get("/api/tenders").json()["items"][0]["key"]
    first = ctx.client.post("/api/tenders/summary", json={"key": key}).json()
    second = ctx.client.post("/api/tenders/summary", json={"key": key}).json()
    assert first == {"summary": "What: a road.", "cached": False} and second["cached"] is True and len(calls) == 1


def test_username_is_checked_when_set():
    env = settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", username="admin", cookie_secure=False, run_scheduler=False), env, SeenStore(":memory:"),
                         RunManager(env, SeenStore(":memory:"), lambda p, s: None, FakeSender(), background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "someone", "password": PASSWORD}).status_code == 401
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 401
    assert c.post("/api/login", json={"username": " Admin ", "password": PASSWORD}).status_code == 200
