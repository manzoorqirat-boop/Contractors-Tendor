"""Deadline reminders: when they are due (once each), what they say, the webhook, and the calendar feed."""
from datetime import datetime, timedelta

import pytest
import requests
from fastapi.testclient import TestClient

from tender_digest import appsettings, reminders, web
from tender_digest.appsettings import SettingsError
from tender_digest.runner import local_now
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, ctx, settings, tender  # noqa: F401

NOON = datetime(2026, 11, 20, 12, 0)


def bid(id=1, stage="preparing", closing=None, emd=None, prebid=None, query=None, opening=None, title="Road repair, Ward 12"):
    return {"id": id, "stage": stage, "closing": closing, "emd_due": emd, "prebid_date": prebid, "query_deadline": query, "opening": opening,
            "title": title, "description": title, "tender_id": f"T{id}"}


def iso(dt):
    return dt.isoformat(timespec="seconds")


# ---- which dates count ------------------------------------------------------------------------------------------------

def test_deadlines_come_from_bids_in_progress_and_skip_the_past():
    bids = [
        bid(1, closing=iso(NOON + timedelta(days=2)), emd="2026-11-21", prebid=iso(NOON - timedelta(days=1)), query="2026-11-22"),
        bid(2, stage="submitted", opening=iso(NOON + timedelta(days=5)), closing=iso(NOON + timedelta(days=1))),
        bid(3, stage="won", closing=iso(NOON + timedelta(days=1))), bid(4, stage="dropped", closing=iso(NOON + timedelta(days=1))),
    ]
    got = reminders.deadlines(bids, NOON)
    assert [(d["bid_id"], d["kind"]) for d in got] == [(1, "emd"), (1, "query"), (1, "closing"), (2, "opening")] or {(d["bid_id"], d["kind"]) for d in got} == {(1, "emd"), (1, "query"), (1, "closing"), (2, "opening")}
    emd = next(d for d in got if d["kind"] == "emd")
    assert emd["when"] == datetime(2026, 11, 21, 23, 59) and emd["all_day"] is True  # a date with no time counts as the end of that day
    assert not any(d["kind"] == "prebid" for d in got)  # yesterday's meeting is over
    assert [d["when"] for d in got] == sorted(d["when"] for d in got)


# ---- when a reminder is due -------------------------------------------------------------------------------------------------

def run_due(items, now, sent):
    fire, handled = reminders.due(items, now, sent)
    sent |= set(handled)
    return fire


def test_each_threshold_fires_once_as_time_passes():
    closing = datetime(2026, 11, 23, 17, 0)
    items = reminders.deadlines([bid(1, closing=iso(closing))], NOON)
    sent: set[str] = set()
    assert run_due(items, closing - timedelta(hours=80), sent) == []                       # too early
    first = run_due(items, closing - timedelta(hours=71), sent)
    assert [f["threshold"] for f in first] == [72]
    assert run_due(items, closing - timedelta(hours=70), sent) == []                       # not again
    assert [f["threshold"] for f in run_due(items, closing - timedelta(hours=23), sent)] == [24]
    assert [f["threshold"] for f in run_due(items, closing - timedelta(hours=5), sent)] == [6]
    assert run_due(items, closing - timedelta(hours=1), sent) == []
    assert run_due(items, closing + timedelta(hours=1), sent) == []                        # after it closed: nothing


def test_a_bid_started_late_gets_one_reminder_not_a_burst():
    closing = NOON + timedelta(hours=10)
    items = reminders.deadlines([bid(1, closing=iso(closing))], NOON)
    sent: set[str] = set()
    fire = run_due(items, NOON, sent)
    assert [f["threshold"] for f in fire] == [24]            # one reminder, saying 10 h
    assert len(sent) == 2                                     # and the 72 h one is recorded as passed
    assert run_due(items, NOON + timedelta(minutes=1), sent) == []


def test_overnight_only_the_last_call_before_closing_is_sent():
    closing = datetime(2026, 11, 21, 17, 0)
    items = reminders.deadlines([bid(1, closing=iso(closing), emd=iso(datetime(2026, 11, 21, 12, 0)))], datetime(2026, 11, 20, 12, 0))
    sent: set[str] = set()
    night = datetime(2026, 11, 20, 23, 0)                    # 18 h before closing
    assert run_due(items, night, sent) == []                  # the 24 h reminders wait for morning
    assert {f["kind"] for f in run_due(items, datetime(2026, 11, 21, 7, 0), sent)} == {"closing", "emd"}
    late = reminders.deadlines([bid(2, closing=iso(datetime(2026, 11, 21, 3, 0)))], datetime(2026, 11, 20, 12, 0))
    assert [f["threshold"] for f in run_due(late, datetime(2026, 11, 21, 0, 0), set())] == [6]   # 3 h before closing, at midnight: sent


def test_a_moved_closing_date_gets_its_own_reminders():
    sent: set[str] = set()
    old = reminders.deadlines([bid(1, closing=iso(NOON + timedelta(hours=20)))], NOON)
    assert run_due(old, NOON, sent)
    moved = reminders.deadlines([bid(1, closing=iso(NOON + timedelta(hours=44)))], NOON)   # a corrigendum pushed it out
    assert [f["threshold"] for f in run_due(moved, NOON + timedelta(hours=21), sent)] == [24]


# ---- the message --------------------------------------------------------------------------------------------------------------

def test_the_message_says_what_when_and_where_and_escapes_tender_text():
    items = reminders.deadlines([bid(7, closing=iso(NOON + timedelta(hours=20)), title="<script>alert(1)</script> Road")], NOON)
    fire, _ = reminders.due(items, NOON, set())
    subject, html, text = reminders.build_message(fire, "https://tend.example.com/")
    assert subject.startswith("Reminder: bid closes in 20 h")
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "https://tend.example.com/#/bids/7" in html and "https://tend.example.com/#/bids/7" in text
    many = reminders.build_message(fire * 3, "")[0]
    assert many == "Reminder: 3 bid deadlines coming up"
    assert "http" not in reminders.build_message(fire, "")[2]  # no link when the site address is not set


@pytest.mark.parametrize("hours,text", [(0.4, "24 min"), (1.0, "1 h"), (23.9, "23 h"), (47.9, "47 h"), (48.0, "2 days"), (100, "4 days")])
def test_time_left_reads_naturally(hours, text):
    assert reminders.left_text(hours) == text


# ---- webhook -------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://hooks.example.com/x", "https://127.0.0.1/x", "https://10.0.0.5/hook", "ftp://x.com", "https://user:pw@hooks.example.com/x", "javascript:alert(1)", "https:///x"])
def test_unsafe_webhook_addresses_are_refused(url):
    with pytest.raises(ValueError):
        reminders.check_webhook_url(url)


def test_a_good_webhook_address_is_kept_and_blank_means_off():
    assert reminders.check_webhook_url(" https://hooks.slack.com/services/T/B/x ") == "https://hooks.slack.com/services/T/B/x"
    assert reminders.check_webhook_url("") == ""


class FakeHttp:
    def __init__(self, status=200, boom=None):
        self.calls, self.status, self.boom = [], status, boom

    def post(self, url, json, timeout, allow_redirects):
        self.calls.append((url, json, allow_redirects))
        if self.boom:
            raise self.boom
        return type("R", (), {"status_code": self.status})()


def test_posting_to_the_webhook(monkeypatch):
    monkeypatch.setattr(reminders, "is_public_host", lambda h: True)
    items, _ = reminders.due(reminders.deadlines([bid(1, closing=iso(NOON + timedelta(hours=5)))], NOON), NOON, set())
    http = FakeHttp()
    reminders.post_webhook("https://hooks.example.com/x", "Bid closes soon", items, session=http)
    url, payload, follow = http.calls[0]
    assert payload["text"] == "Bid closes soon" and payload["reminders"][0]["kind"] == "closing" and follow is False  # redirects are never followed
    with pytest.raises(reminders.WebhookError, match="500"):
        reminders.post_webhook("https://hooks.example.com/x", "t", session=FakeHttp(500))
    with pytest.raises(reminders.WebhookError, match="could not be reached"):
        reminders.post_webhook("https://hooks.example.com/x", "t", session=FakeHttp(boom=requests.ConnectionError("down")))
    monkeypatch.setattr(reminders, "is_public_host", lambda h: False)
    with pytest.raises(reminders.WebhookError, match="private network"):
        reminders.post_webhook("https://hooks.example.com/x", "t", session=http)


# ---- settings -----------------------------------------------------------------------------------------------------------------

def test_reminder_settings_are_checked():
    assert appsettings.clean({"reminders_on": False, "webhook_url": "https://hooks.example.com/x"}, [])["webhook_url"] == "https://hooks.example.com/x"
    assert appsettings.clean({"webhook_url": ""}, [])["webhook_url"] == ""
    for bad in ({"reminders_on": "yes"}, {"webhook_url": "http://hooks.example.com"}, {"webhook_url": "https://192.168.1.5/x"}):
        with pytest.raises(SettingsError):
            appsettings.clean(bad, [])


# ---- sending, end to end through the run manager ---------------------------------------------------------------------------------

def start_bid(ctx, n=1, hours=20):
    now = local_now()
    t = tender(n, closing=now + timedelta(hours=hours))
    ctx.store.keep(t, now)
    b, _ = ctx.manager.bids.create_from_tender(t.key, now)
    return b, now


def test_reminders_are_emailed_once(ctx):
    b, now = start_bid(ctx)
    now = now.replace(hour=12, minute=0)  # daytime, so it is not overnight
    t = ctx.manager.bids.get(b["id"])
    ctx.manager.bids.update(b["id"], {"closing": iso(now + timedelta(hours=20))}, now)
    assert ctx.manager.send_reminders(now) == 1
    html = ctx.sender.calls[-1][0][5]
    assert "Bid closes" in html and t["tender_id"] in html
    assert ctx.manager.send_reminders(now + timedelta(minutes=1)) == 0 and len(ctx.sender.calls) == 1   # not again
    ctx.manager.bids.update(b["id"], {"closing": iso(now + timedelta(hours=3))}, now)
    assert ctx.manager.send_reminders(now + timedelta(minutes=2)) == 1                                    # a new date, a new reminder


def test_switching_reminders_off_stops_them(ctx):
    b, now = start_bid(ctx)
    now = now.replace(hour=12, minute=0)
    ctx.manager.bids.update(b["id"], {"closing": iso(now + timedelta(hours=20))}, now)
    ctx.store.put_settings({"reminders_on": False})
    assert ctx.manager.send_reminders(now) == 0 and ctx.sender.calls == []


def test_with_nowhere_to_send_nothing_is_used_up(ctx):
    b, now = start_bid(ctx)
    now = now.replace(hour=12, minute=0)
    ctx.manager.bids.update(b["id"], {"closing": iso(now + timedelta(hours=20))}, now)
    ctx.store.put_settings({"recipients": []})
    assert ctx.manager.send_reminders(now) == 0
    ctx.store.put_settings({"recipients": ["a@example.com"]})  # an address is added later: the reminder still goes out
    assert ctx.manager.send_reminders(now + timedelta(minutes=11)) == 1


def test_a_failed_send_is_retried_later_not_every_half_minute(ctx):
    b, now = start_bid(ctx)
    now = now.replace(hour=12, minute=0)
    ctx.manager.bids.update(b["id"], {"closing": iso(now + timedelta(hours=20))}, now)
    boom = {"on": True}
    real = ctx.manager.sender

    def flaky(*a, **k):
        if boom["on"]:
            raise RuntimeError("Brevo is down")
        return real(*a, **k)

    ctx.manager.sender = flaky
    assert ctx.manager.send_reminders(now) == 0
    boom["on"] = False
    assert ctx.manager.send_reminders(now + timedelta(minutes=1)) == 0     # too soon to try again
    assert ctx.manager.send_reminders(now + timedelta(minutes=11)) == 1


def test_the_webhook_gets_the_reminder_too(ctx, monkeypatch):
    posted = []
    monkeypatch.setattr(reminders, "post_webhook", lambda url, text, items=None, **k: posted.append((url, text, len(items or []))))
    b, now = start_bid(ctx)
    now = now.replace(hour=12, minute=0)
    ctx.manager.bids.update(b["id"], {"closing": iso(now + timedelta(hours=20))}, now)
    ctx.store.put_settings({"webhook_url": "https://hooks.example.com/x", "recipients": []})
    assert ctx.manager.send_reminders(now) == 1
    assert posted and posted[0][0] == "https://hooks.example.com/x" and "Reminder" in posted[0][1] and posted[0][2] == 1 and ctx.sender.calls == []


# ---- the calendar feed -----------------------------------------------------------------------------------------------------------

def test_the_calendar_feed_is_valid_and_correct():
    items = [bid(1, closing="2026-12-02T17:30:00", emd="2026-12-01", title="Road, Ward 12; phase 2\nsecond line " + "x" * 120)]
    ics = reminders.build_ics(items, [{"id": 9, "name": "GST certificate", "expiry": "2026-12-31"}], NOON, app_url="https://tend.example.com")
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n") and "\r\n" in ics and "\n" not in ics.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))                       # folded as the standard requires
    unfolded = ics.replace("\r\n ", "")
    assert "DTSTART:20261202T120000Z" in unfolded                                            # 17:30 India time is 12:00 UTC
    assert "DTSTART;VALUE=DATE:20261201" in unfolded and "DTEND;VALUE=DATE:20261202" in unfolded  # a date with no time is an all-day event
    assert r"Road\, Ward 12\; phase 2\nsecond line" in unfolded                            # commas, semicolons and newlines escaped
    assert "UID:bid-1-closing@tendai" in unfolded and "UID:vault-9@tendai" in unfolded and "URL:https://tend.example.com/#/bids/1" in unfolded
    assert unfolded.count("BEGIN:VALARM") == unfolded.count("BEGIN:VEVENT") == 3


def test_a_tender_title_cannot_inject_calendar_lines():
    ics = reminders.build_ics([bid(1, closing="2026-12-02T17:30:00", title="x\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:fake")], [], NOON)
    lines = ics.split("\r\n")
    assert lines.count("BEGIN:VEVENT") == 1 and lines.count("END:VEVENT") == 1 and not any(l.startswith("SUMMARY:fake") for l in lines)  # the newline became text


def test_the_calendar_address_flow(ctx):
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin", app_url="https://tend.example.com"), settings(), ctx.store)
    owner = TestClient(app)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert owner.get("/api/calendar").json() == {"enabled": False, "url": ""}
    url = owner.post("/api/calendar/token").json()["url"]
    assert url.startswith("https://tend.example.com/api/calendar/") and url.endswith(".ics")
    path = url.replace("https://tend.example.com", "")
    anonymous = TestClient(app)  # a calendar app: no cookie
    feed = anonymous.get(path)
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar") and feed.text.startswith("BEGIN:VCALENDAR")
    assert anonymous.get("/api/calendar/not-the-token.ics").status_code == 404
    new = owner.post("/api/calendar/token").json()["url"]
    assert new != url and anonymous.get(path).status_code == 404 and anonymous.get(new.replace("https://tend.example.com", "")).status_code == 200  # the old address stops working
    owner.delete("/api/calendar")
    assert owner.get("/api/calendar").json()["enabled"] is False and anonymous.get(new.replace("https://tend.example.com", "")).status_code == 404


def test_only_admins_manage_the_calendar_and_webhook(ctx):
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), settings(), ctx.store)
    owner = TestClient(app)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    owner.post("/api/users", json={"username": "bob", "role": "bidder", "password": "temporary-pass-1"})
    bob = TestClient(app)
    bob.post("/api/login", json={"username": "bob", "password": "temporary-pass-1"})
    bob.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
    assert bob.get("/api/calendar").status_code == 403 and bob.post("/api/calendar/token").status_code == 403
    assert bob.post("/api/settings/test-webhook", json={"url": "https://hooks.example.com/x"}).status_code == 403
    assert owner.post("/api/settings/test-webhook", json={}).status_code == 422          # nothing typed and nothing saved
    assert owner.post("/api/settings/test-webhook", json={"url": "http://hooks.example.com"}).status_code == 502
