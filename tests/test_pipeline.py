from datetime import datetime, timedelta

import pytest

from conftest import fixture
from tender_digest import collector, digest, filters, mailer
from tender_digest.config import Portal, Settings, load_settings
from tender_digest.gepnic import PortalError, Tender, apply_detail, parse_detail
from tender_digest.store import SeenStore

NOW = datetime(2026, 11, 14, 8, 0)


def make_settings(**kw) -> Settings:
    base = dict(
        portals=[Portal("maharashtra", "Maharashtra", "Maharashtra", "https://x/nicgep")],
        recipients=["a@example.com", "b@example.com"],
        brevo_api_key="k", mail_from="from@example.com", mail_from_name="T", state_path=":memory:",
        request_delay=0, lookback_days=2,
    )
    base.update(kw)
    return Settings(**base)


def tender(**kw) -> Tender:
    base = dict(
        portal="maharashtra", tender_id="T1", title="Construction of CC road", reference="", organisation="Public Works Department||EE X",
        published=NOW - timedelta(hours=5), closing=NOW + timedelta(days=6), opening=NOW + timedelta(days=8),
    )
    base.update(kw)
    return Tender(**base)


# ---- filters ----------------------------------------------------------------------------------------------------

def test_civil_by_keyword_and_not_electrical():
    s = make_settings()
    assert filters.is_civil(tender(), s, True)
    elec = tender(title="Est No. 12", description="Est No. 12", category="Electrical Works", organisation="Public Works Department||CE-Electrical")
    assert not filters.is_civil(elec, s, True)  # a PWD department also issues electrical work


def test_civil_category_from_detail_page():
    s = make_settings()
    assert filters.is_civil(tender(title="Est No. 5", category="Civil Works - Others"), s, True)


def test_excluded_words_win():
    assert not filters.is_civil(tender(title="Civil work and housekeeping services"), make_settings(), True)


def test_without_detail_a_pwd_department_is_trusted_but_others_are_not():
    s = make_settings()
    assert filters.is_civil(tender(title="Est No. 9"), s, False)
    assert not filters.is_civil(tender(title="Est No. 9", organisation="Some Board||Unit"), s, False)
    assert not filters.is_civil(tender(title="Est No. 9"), s, True)  # detail known and says nothing civil


def test_value_filter_keeps_unknown_values():
    s = make_settings(min_value=500000)
    assert filters.meets_value(tender(value=None), s)
    assert filters.meets_value(tender(value=600000), s)
    assert not filters.meets_value(tender(value=100), s)


def test_recent_and_closing_soon():
    assert filters.is_recent(tender(published=NOW - timedelta(days=1)), NOW, 2)
    assert not filters.is_recent(tender(published=NOW - timedelta(days=9)), NOW, 2)
    assert filters.closing_soon(tender(closing=NOW + timedelta(days=2)), NOW, 3)
    assert not filters.closing_soon(tender(closing=NOW + timedelta(days=5)), NOW, 3)
    assert not filters.closing_soon(tender(closing=NOW - timedelta(days=1)), NOW, 3)


# ---- store ------------------------------------------------------------------------------------------------------

def test_store_round_trip_pending_and_emailed():
    store = SeenStore(":memory:")
    t = tender(value=851184.0, category="Civil Works")
    assert not store.is_seen(t.key)
    store.keep(t, NOW)
    assert store.is_seen(t.key)
    pending = store.pending(NOW)
    assert [p.tender_id for p in pending] == ["T1"] and pending[0].value == 851184.0 and pending[0].closing == t.closing
    store.mark_emailed([t.key])
    assert store.pending(NOW) == []
    assert [x.tender_id for x in store.closing_soon(NOW, 7)] == ["T1"]  # still listed while it closes soon


def test_store_ignores_closed_and_skipped():
    store = SeenStore(":memory:")
    store.keep(tender(tender_id="OLD", closing=NOW - timedelta(days=1)), NOW)
    store.skip("maharashtra:S", NOW)
    assert store.pending(NOW) == []
    assert store.is_seen("maharashtra:S")


# ---- collector (portal faked) -----------------------------------------------------------------------------------

class FakePortal:
    def __init__(self, orgs, lists, details, fail_detail=()):
        self._orgs, self._lists, self._details, self._fail = orgs, lists, details, set(fail_detail)
        self.detail_calls = 0
        self.search_url = "https://x/nicgep/app?page=FrontEndAdvancedSearch&service=page"

    def organisations(self):
        if self._orgs is None:
            raise PortalError("down")
        return self._orgs

    def tenders_of(self, link, portal):
        return [Tender(**{**t.__dict__}) for t in self._lists[link]]

    def detail(self, t):
        self.detail_calls += 1
        if t.tender_id in self._fail:
            raise PortalError("timeout")
        return self._details[t.tender_id]


def run(fake, store, settings=None, now=NOW):
    settings = settings or make_settings()
    return collector.collect(settings, store, now, lambda p, s: fake)


def detail(cat, value="1,00,000", desc="Work"):
    return {"Product Category": cat, "Tender Value in ₹": value, "Work Description": desc, "Location": "Pune"}


def test_collect_keeps_civil_skips_others_and_does_not_repeat():
    civil = tender(tender_id="C1", title="Est No. 1")
    elec = tender(tender_id="E1", title="Est No. 2")
    old = tender(tender_id="O1", title="Road", published=NOW - timedelta(days=30))
    fake = FakePortal(
        [("Public Works Department", 3, "L1"), ("Film Board", 5, "L2")],
        {"L1": [civil, elec, old], "L2": [tender(tender_id="X")]},
        {"C1": detail("Civil Works - Others"), "E1": detail("Electrical Works")},
    )
    store = SeenStore(":memory:")
    [report] = run(fake, store)
    assert [t.tender_id for t in store.pending(NOW)] == ["C1"]
    assert report.organisations_read == 1 and report.new_civil == 1  # Film Board is not a civil department
    assert fake.detail_calls == 2  # the 30-day-old tender is never fetched

    calls = fake.detail_calls
    run(fake, store)
    assert fake.detail_calls == calls  # everything already seen: no new requests for it


def test_detail_failure_is_retried_next_run_not_lost():
    t = tender(tender_id="C1", title="Est No. 1")
    fake = FakePortal([("Public Works Department", 1, "L1")], {"L1": [t]}, {"C1": detail("Civil Works")}, fail_detail=["C1"])
    store = SeenStore(":memory:")
    [report] = run(fake, store)
    assert store.pending(NOW) == [] and report.errors
    fake._fail.clear()
    run(fake, store)
    assert [x.tender_id for x in store.pending(NOW)] == ["C1"]


def test_portal_down_is_reported_and_other_portals_still_run():
    store = SeenStore(":memory:")
    reports = collector.collect(
        make_settings(portals=[Portal("a", "A", "A", "https://a"), Portal("b", "B", "B", "https://b")]),
        store, NOW,
        lambda p, s: FakePortal(None, {}, {}) if p.key == "a" else FakePortal([("Public Works", 1, "L")], {"L": [tender(tender_id="Z", portal="b", title="Road repair")]}, {"Z": detail("Civil Works")}),
    )
    assert reports[0].failed and "down" in reports[0].errors[0]
    assert not reports[1].failed and reports[1].new_civil == 1


def test_detail_fetch_limit_is_respected():
    ts = [tender(tender_id=f"T{i}", title=f"Road {i}") for i in range(5)]
    fake = FakePortal([("Public Works", 5, "L")], {"L": ts}, {t.tender_id: detail("Civil Works") for t in ts})
    run(fake, SeenStore(":memory:"), make_settings(max_detail_fetches=2))
    assert fake.detail_calls == 2


# ---- digest and mail --------------------------------------------------------------------------------------------

def test_inr_formatting():
    assert digest.format_inr(851184) == "₹8,51,184 (8.51 lakh)"
    assert digest.format_inr(20991250) == "₹2,09,91,250 (2.10 crore)"
    assert digest.format_inr(999) == "₹999"
    assert digest.format_inr(None) == "Not stated"


def test_digest_contents_and_escaping():
    report = collector.PortalReport(make_settings().portals[0], search_url="https://x/search")
    new = [tender(tender_id="N1", title="<script>alert(1)</script> road", value=851184)]
    soon = [tender(tender_id="S1", closing=NOW + timedelta(days=1))]
    d = digest.build(NOW, new, soon, [report])
    assert "1 new, 1 closing soon" in d.subject
    assert "<script>" not in d.html and "&lt;script&gt;" in d.html
    assert "N1" in d.html and "S1" in d.html and "₹8,51,184" in d.html and "https://x/search" in d.html
    assert "N1" in d.text


def test_digest_says_when_a_portal_failed():
    report = collector.PortalReport(make_settings().portals[0], errors=["could not read the portal: down"])
    d = digest.build(NOW, [], [], [report])
    assert "unavailable" in d.subject and "could not be read" in d.html and "No new civil tenders" in d.html


class FakeHttp:
    def __init__(self, statuses):
        self.statuses, self.sent = list(statuses), []

    def post(self, url, headers, json, timeout):
        self.sent.append((url, headers, json))

        class R:
            status_code = self.statuses.pop(0)
            text = "bad sender"
        return R()


def test_mail_sent_one_per_recipient_with_key_in_header_only():
    http = FakeHttp([201, 201])
    accepted = mailer.send("SECRET", "f@x.com", "T", ["a@x.com", "b@x.com"], "S", "<p>h</p>", "t", http)
    assert accepted == ["a@x.com", "b@x.com"]
    assert [m[2]["to"] for m in http.sent] == [[{"email": "a@x.com"}], [{"email": "b@x.com"}]]
    assert all(m[1]["api-key"] == "SECRET" and "SECRET" not in str(m[2]) for m in http.sent)


def test_mail_partial_failure_still_counts_but_total_failure_raises():
    assert mailer.send("k", "f@x.com", "T", ["a@x.com", "b@x.com"], "S", "h", "t", FakeHttp([400, 201])) == ["b@x.com"]
    with pytest.raises(mailer.MailError) as e:
        mailer.send("k", "f@x.com", "T", ["a@x.com"], "S", "h", "t", FakeHttp([401]))
    assert "401" in str(e.value)


# ---- config -----------------------------------------------------------------------------------------------------

def test_recipients_are_configurable(monkeypatch):
    monkeypatch.setenv("RECIPIENTS", "a@x.com, b@x.com;c@x.com")
    monkeypatch.setenv("BREVO_API_KEY", "k")
    monkeypatch.setenv("MAIL_FROM", "f@x.com")
    s = load_settings()
    assert s.recipients == ["a@x.com", "b@x.com", "c@x.com"]
    assert [p.key for p in s.portals] == ["maharashtra", "delhi", "up", "central"]


def test_missing_mail_settings_stop_with_a_clear_message(monkeypatch):
    for n in ("RECIPIENTS", "BREVO_API_KEY", "MAIL_FROM"):
        monkeypatch.delenv(n, raising=False)
    with pytest.raises(SystemExit) as e:
        load_settings()
    assert "RECIPIENTS" in str(e.value) and "BREVO_API_KEY" in str(e.value)
    load_settings(require_mail=False)  # dry runs need no mail settings


# ---- electrical leak, email size, spreadsheet --------------------------------------------------------------------

def test_electrical_category_is_not_civil_even_when_the_text_mentions_a_building():
    s = make_settings()
    t = tender(title="Electrification of new building", category="Electrical Works")
    assert not filters.is_civil(t, s, True)
    assert filters.is_civil(tender(title="Electrification of new building", category=""), s, True)  # no category: keywords decide
    assert filters.is_civil(tender(title="x", category="Civil Works - Electrical"), s, True)


def test_email_is_capped_and_spreadsheet_has_everything():
    import base64
    report = collector.PortalReport(make_settings().portals[0], search_url="https://x/s")
    many = [tender(tender_id=f"N{i}", title=f"Construction of road {i}", value=1000.0 * i) for i in range(300)]
    d = digest.build(NOW, many, [], [report], max_new_per_portal=25)
    assert len(d.html.encode()) < 100_000  # Gmail cuts messages off at about 102 KB
    assert "and 275 more, in the attached spreadsheet" in d.html
    assert "N299" in d.html and "N0<" not in d.html  # biggest value first, smallest left out of the body
    rows = base64.b64decode(d.csv_base64).decode("utf-8-sig").splitlines()
    assert len(rows) == 301 and rows[0].startswith("Status,Portal,Tender ID")


def test_attachment_goes_with_the_mail():
    http = FakeHttp([201])
    mailer.send("k", "f@x.com", "T", ["a@x.com"], "S", "h", "t", http, attachments=[{"name": "t.csv", "content": "QQ=="}])
    assert http.sent[0][2]["attachment"] == [{"name": "t.csv", "content": "QQ=="}]
