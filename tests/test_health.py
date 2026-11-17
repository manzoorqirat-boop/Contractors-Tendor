"""Telling a portal that has quietly stopped working from one that is having a bad day."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import collector, digest, health, runner, web
from tender_digest.config import Portal, Settings
from tender_digest.gepnic import PortalError
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore

NOW = datetime(2026, 11, 17, 8, 0)


def read(days_ago, kind="ok", tenders=80, source="run"):
    return {"at": (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"), "source": source, "kind": kind, "departments": 9, "tenders": tenders}


def state(*rows, enabled=True, now=NOW):
    return health.assess(list(rows), now, enabled)["state"]


# ---- the rules ---------------------------------------------------------------------------------------------------

def test_a_portal_never_read_is_unknown_and_a_switched_off_one_is_off():
    assert state() == "unknown"
    assert state(read(0), enabled=False) == "off"


def test_a_portal_reading_normally_is_ok():
    assert state(*[read(d) for d in range(0, 6)]) == "ok"


def test_one_failed_read_is_noted_but_is_not_an_alarm():
    h = health.assess([read(0, "unreachable", 0), *[read(d) for d in range(1, 6)]], NOW)
    assert h["state"] == "ok" and "watched" in h["text"] and h["failures_in_a_row"] == 1


def test_it_is_broken_after_two_failed_reads_in_a_row_once_it_had_worked():
    h = health.assess([read(0, "unreachable", 0), read(1, "unreachable", 0), *[read(d) for d in range(2, 6)]], NOW)
    assert h["state"] == "broken" and h["failures_in_a_row"] == 2
    assert "2 reads" in h["text"] and "last worked on 2026-11-15" in h["text"]     # says since when it last worked


def test_a_failure_in_between_does_not_count_once_a_read_succeeded():
    assert state(read(0), read(1, "unreachable", 0), read(2, "unreachable", 0), read(3)) == "ok"


def test_a_portal_that_never_worked_and_needs_a_person_belongs_on_the_check_by_hand_list_not_here():
    assert state(read(0, "captcha", 0), read(1, "captcha", 0), read(2, "captcha", 0)) == "manual"
    assert state(read(0, "otp", 0)) == "manual"
    assert state(read(0, "unreachable", 0)) == "unknown"                  # one bad first read: give it a chance
    assert state(read(0, "unreachable", 0), read(1, "unreachable", 0)) == "manual"


def test_a_portal_that_used_to_need_nothing_and_now_wants_a_captcha_is_broken():
    assert state(read(0, "captcha", 0), read(1, "captcha", 0), read(2), read(3)) == "broken"


def test_it_is_dried_up_when_it_answers_but_lists_nothing_two_reads_running():
    rows = [read(0, tenders=0), read(1, tenders=0), *[read(d, tenders=90) for d in range(2, 8)]]
    h = health.assess(rows, NOW)
    assert h["state"] == "dried_up" and "no tenders" in h["text"] and h["usual"] == 90


def test_it_is_dried_up_when_it_lists_a_small_fraction_of_the_usual():
    assert state(read(0, tenders=5), read(1, tenders=3), *[read(d, tenders=100) for d in range(2, 8)]) == "dried_up"


def test_one_empty_read_is_a_quiet_day_not_a_dead_portal():
    assert state(read(0, tenders=0), *[read(d, tenders=90) for d in range(1, 8)]) == "ok"


def test_a_small_portal_that_always_lists_few_is_never_dried_up():
    assert state(*[read(d, tenders=2) for d in range(0, 8)]) == "ok"
    assert state(read(0, tenders=0), read(1, tenders=0), *[read(d, tenders=3) for d in range(2, 8)]) == "ok"


def test_a_normal_swing_is_not_dried_up():
    assert state(read(0, tenders=40), read(1, tenders=35), *[read(d, tenders=90) for d in range(2, 8)]) == "ok"


def test_a_manual_check_does_not_count_towards_what_a_portal_usually_lists():
    # a check reports the portal's own department counts, which are a different number from what a run lists
    rows = [read(0, tenders=9000, source="check"), read(1, tenders=0), read(2, tenders=0), *[read(d, tenders=90) for d in range(3, 9)]]
    assert state(*rows) == "dried_up"


def test_it_is_stale_when_the_daily_run_has_not_read_it_for_days():
    h = health.assess([read(5), read(6), read(7)], NOW)
    assert h["state"] == "stale" and "5 days" in h["text"]
    assert state(read(2), read(3)) == "ok"                               # within three days is fine (a weekend, a restart)


def test_a_manual_check_does_not_hide_a_daily_run_that_stopped():
    assert state(read(0, source="check"), read(5), read(6)) == "stale"


def test_the_problem_list_puts_problems_first_and_leaves_off_ones_out():
    store = SeenStore(":memory:")
    ps = [Portal("a", "Alpha", "A", "https://a"), Portal("b", "Beta", "B", "https://b"), Portal("c", "Gamma", "C", "https://c")]
    for d in range(5, 1, -1):                                             # oldest first, as the reads happen
        store.put_portal_status("a", "ok", "", NOW - timedelta(days=d), 9, 80)
        store.put_portal_status("b", "ok", "", NOW - timedelta(days=d), 9, 80)
    store.put_portal_status("b", "unreachable", "x", NOW - timedelta(days=1), 0, 0)
    store.put_portal_status("b", "unreachable", "x", NOW, 0, 0)
    items = health.assess_all(store, ps, {"a", "b"}, NOW)
    assert [h["key"] for h in items][0] == "b" and items[0]["health"] == "broken"
    assert [h["health"] for h in items if h["key"] == "c"] == ["off"]
    assert [h["key"] for h in health.problems(items)] == ["b"]


# ---- the history -------------------------------------------------------------------------------------------------

def test_every_read_is_remembered_newest_first_and_the_history_stays_short():
    store = SeenStore(":memory:")
    for i in range(SeenStore.HISTORY_KEPT + 15):
        store.put_portal_status("a", "ok", "", NOW + timedelta(minutes=i), 3, i)
    rows = store.portal_history("a", 1000)
    assert len(rows) == SeenStore.HISTORY_KEPT and rows[0]["tenders"] == SeenStore.HISTORY_KEPT + 14
    assert store.portal_history("nobody") == []


def test_removing_a_portal_removes_its_history_and_its_health_note():
    store = SeenStore(":memory:")
    store.put_portal_status("a", "ok", "", NOW, 1, 1)
    store.put_portal_health("a", "ok", NOW)
    store.delete_portal_status("a")
    assert store.portal_history("a") == [] and store.portal_health_states() == {}


def test_a_run_adds_to_the_history_of_every_portal_it_reads():
    store = SeenStore(":memory:")
    settings = Settings(portals=[Portal("a", "A", "A", "https://a.gov.in/n")], recipients=["a@example.com"], brevo_api_key="k", mail_from="f@example.com",
                        mail_from_name="T", state_path=":memory:", request_delay=0)

    class Down:
        search_url = "https://x/s"

        def organisations(self):
            raise PortalError("unreachable", "unreachable")

    for i in range(2):
        collector.collect(settings, store, NOW + timedelta(days=i), lambda p, s: Down())
    assert [r["kind"] for r in store.portal_history("a")] == ["unreachable", "unreachable"]
    assert {r["source"] for r in store.portal_history("a")} == {"run"}


# ---- telling people ----------------------------------------------------------------------------------------------

class Bus:
    def __init__(self):
        self.sent = []

    def __call__(self, event, data):
        self.sent.append((event, data["portal"]))


def feed(store, key, *reads):
    for r in reads:
        store.put_portal_status(key, r["kind"], "", datetime.fromisoformat(r["at"]), r["departments"], r["tenders"], r["source"])


def run_announce(store, ps, bus, at=NOW):
    items = health.assess_all(store, ps, {p.key for p in ps}, at)
    return health.announce(store, items, bus, at)


def test_an_alert_goes_out_once_when_a_portal_breaks_and_once_when_it_recovers():
    store, bus, ps = SeenStore(":memory:"), Bus(), [Portal("a", "Alpha", "A", "https://a")]
    feed(store, "a", *[read(d) for d in range(6, 2, -1)])
    assert run_announce(store, ps, bus) == [] and bus.sent == []                       # healthy: nothing to say
    feed(store, "a", read(2, "unreachable", 0), read(1, "unreachable", 0))
    assert run_announce(store, ps, bus) == ["a"] and bus.sent == [("portal.unhealthy", "a")]
    assert run_announce(store, ps, bus) == [] and len(bus.sent) == 1                   # still broken an hour later: no second alert
    feed(store, "a", read(0))
    assert run_announce(store, ps, bus) == ["a"] and bus.sent[-1] == ("portal.recovered", "a")
    assert run_announce(store, ps, bus) == [] and len(bus.sent) == 2


def test_a_portal_that_never_worked_does_not_raise_an_alert_when_it_is_first_seen():
    store, bus, ps = SeenStore(":memory:"), Bus(), [Portal("a", "Alpha", "A", "https://a")]
    feed(store, "a", read(1, "captcha", 0), read(0, "captcha", 0))
    assert run_announce(store, ps, bus) == [] and bus.sent == []


def test_a_change_from_one_problem_to_another_is_announced_again():
    store, bus, ps = SeenStore(":memory:"), Bus(), [Portal("a", "Alpha", "A", "https://a")]
    feed(store, "a", *[read(d) for d in range(10, 5, -1)], read(2, "unreachable", 0), read(1, "unreachable", 0))
    run_announce(store, ps, bus)
    assert bus.sent == [("portal.unhealthy", "a")]
    feed(store, "a", read(0, tenders=0), read(0, tenders=0))                           # answers again, but lists nothing
    run_announce(store, ps, bus)
    assert [e for e, _ in bus.sent] == ["portal.unhealthy", "portal.unhealthy"]


def test_the_email_says_which_portals_may_have_stopped_working():
    bad = [{"key": "a", "name": "Alpha Portal", "health": "broken", "text": "Could not be read in the last 2 reads (Not reachable). It last worked on 2026-11-10."}]
    built = digest.build(runner.local_now(), [], [], [], unhealthy=bad)
    assert "may have stopped working" in built.html and "Alpha Portal" in built.html and "last worked on 2026-11-10" in built.html
    assert "PORTAL MAY HAVE STOPPED WORKING: Alpha Portal" in built.text
    assert "may have stopped working" not in digest.build(runner.local_now(), [], [], []).html


# ---- the page's view of it ---------------------------------------------------------------------------------------

PASSWORD = "correct horse"


@pytest.fixture
def app():
    store = SeenStore(":memory:")
    env = Settings(portals=[Portal("maha", "Maharashtra", "MH", "https://m.gov.in/nicgep")], recipients=["a@example.com"], brevo_api_key="k",
                   mail_from="f@example.com", mail_from_name="T", state_path=":memory:", request_delay=0)
    manager = RunManager(env, store, background=False)
    created = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, manager)
    client = TestClient(created)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return type("App", (), {"store": store, "client": client})


def test_the_health_report_lists_problems_and_is_on_each_portal(app):
    now = runner.local_now()
    for d in range(6, 2, -1):
        app.store.put_portal_status("maha", "ok", "", now - timedelta(days=d), 9, 80)
    app.store.put_portal_status("maha", "unreachable", "timed out", now - timedelta(days=1), 0, 0)
    app.store.put_portal_status("maha", "unreachable", "timed out", now, 0, 0)
    body = app.client.get("/api/portal-health").json()
    assert [p["key"] for p in body["problems"]] == ["maha"] and body["items"][0]["health"] == "broken"
    entry = app.client.get("/api/portals").json()["portals"][0]
    assert entry["health"] == "broken" and "last worked" in entry["health_text"] and entry["last_ok"]


def test_a_healthy_install_reports_no_problems(app):
    assert app.client.get("/api/portal-health").json()["problems"] == []


def test_the_health_report_needs_a_sign_in():
    store = SeenStore(":memory:")
    env = Settings(portals=[], recipients=[], brevo_api_key="k", mail_from="f@example.com", mail_from_name="T", state_path=":memory:", request_delay=0)
    created = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    assert TestClient(created).get("/api/portal-health").status_code == 401
