"""Proving which portals this installation can really read: the deep test, its report, the page's endpoints and the command line."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tender_digest import coverage, web
from tender_digest.config import Portal, Settings
from tender_digest.gepnic import PortalError, Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore

PORTAL = Portal("kerala", "Kerala", "Kerala", "https://etenders.kerala.gov.in/nicgep")
NOW = datetime(2026, 11, 17, 9, 0)


def tender(i, closing=True, tid=True):
    return Tender("kerala", f"2026_X_{i}" if tid else "", f"Road work {i}", "", "PWD", datetime(2026, 11, 10), datetime(2026, 11, 30) if closing else None, None, detail_path=f"/d/{i}")


class Client:
    def __init__(self, orgs=None, tenders=None, detail=None, org_error=None):
        self._orgs = orgs if orgs is not None else [("PWD", 12, "l1"), ("Water", 3, "l2")]
        self._tenders = tenders if tenders is not None else [tender(i) for i in range(10)]
        self._detail = detail if detail is not None else {"Work Description": "Widening", "Tender Value": "5,00,000"}
        self.org_error, self.asked = org_error, []

    def organisations(self, attempts=1):
        if self.org_error:
            raise self.org_error
        return self._orgs

    def tenders_of(self, link, portal):
        self.asked.append(link)
        return self._tenders

    def detail(self, t):
        if isinstance(self._detail, Exception):
            raise self._detail
        return self._detail


def probe(client, portal=PORTAL):
    return coverage.deep_probe(portal, client_factory=lambda p: client)


# ---- the deep test -----------------------------------------------------------------------------------------------

def test_a_portal_that_lists_tenders_with_dates_and_detail_pages_is_proven():
    c = Client()
    r = probe(c)
    assert r["verdict"] == "proven" and r["kind"] == "ok"
    assert r["departments"] == 2 and r["advertised"] == 15 and r["listed"] == 10
    assert r["department"] == "PWD" and c.asked == ["l1"]                         # the busiest department is the one sampled
    assert r["with_id_pct"] == 100 and r["with_closing_pct"] == 100 and r["details_read"] == 3 and r["with_value_pct"] == 100


def test_only_a_few_detail_pages_are_read_so_the_test_stays_polite():
    assert probe(Client())["details_tried"] == coverage.SAMPLE_TENDERS


def test_tenders_without_closing_dates_make_it_partial_not_proven():
    r = probe(Client(tenders=[tender(i, closing=False) for i in range(10)]))
    assert r["verdict"] == "partial" and r["with_closing_pct"] == 0 and "layout may have changed" in r["detail"]


def test_a_few_missing_dates_are_tolerated():
    ts = [tender(i) for i in range(19)] + [tender(99, closing=False)]
    assert probe(Client(tenders=ts))["verdict"] == "proven"                       # 95% have one


def test_no_readable_detail_page_makes_it_partial():
    r = probe(Client(detail=PortalError("gone", "unreachable")))
    assert r["verdict"] == "partial" and r["details_read"] == 0 and "detail page" in r["detail"]


def test_a_portal_that_answers_with_no_tenders_is_empty_not_failed():
    assert probe(Client(orgs=[("PWD", 0, "l1")]))["verdict"] == "empty"
    r = probe(Client(tenders=[]))
    assert r["verdict"] == "empty" and "none could be read" in r["detail"]


@pytest.mark.parametrize("kind", ["captcha", "login", "otp", "blocked", "different_software", "unreachable"])
def test_a_portal_that_cannot_be_read_fails_with_the_reason(kind):
    r = probe(Client(org_error=PortalError("walled", kind)))
    assert r["verdict"] == "failed" and r["kind"] == kind and r["detail"] == "walled"


def test_any_other_error_is_still_an_answer_never_a_crash():
    r = probe(Client(org_error=ValueError("boom")))
    assert r["verdict"] == "failed" and r["kind"] == "unreachable" and "ValueError" in r["detail"]


def test_an_address_on_a_private_network_is_never_contacted():
    called = []
    r = coverage.deep_probe(Portal("x", "X", "X", "https://127.0.0.1/nicgep"), client_factory=lambda p: called.append(p))
    assert r["verdict"] == "failed" and r["kind"] == "blocked" and called == []


# ---- the report --------------------------------------------------------------------------------------------------

def test_the_report_shows_untested_portals_and_counts_each_result():
    store = SeenStore(":memory:")
    other = Portal("delhi", "Delhi", "Delhi", "https://d.gov.in/nicgep")
    third = Portal("up", "UP", "UP", "https://u.gov.in/nicgep")
    coverage.record(store, PORTAL, probe(Client()), NOW)
    coverage.record(store, other, probe(Client(org_error=PortalError("walled", "otp"))), NOW)
    rep = coverage.report(store.portal_coverages(), [PORTAL, other, third], {"kerala"})
    assert rep["summary"] == {"portals": 3, "proven": 1, "partial": 0, "empty": 0, "failed": 1, "untested": 1, "tested": 2}
    by = {r["key"]: r for r in rep["rows"]}
    assert by["kerala"]["verdict_text"].startswith("Proven") and by["kerala"]["enabled"] is True and by["kerala"]["checked"] == "2026-11-17T09:00:00"
    assert by["delhi"]["label"] == "OTP login needed" and by["delhi"]["enabled"] is False
    assert by["up"]["verdict"] == "untested" and by["up"]["checked"] is None


def test_a_test_also_updates_what_the_rest_of_the_app_knows_about_the_portal():
    store = SeenStore(":memory:")
    coverage.record(store, PORTAL, probe(Client()), NOW)
    st = store.portal_statuses()["kerala"]
    assert st["kind"] == "ok" and st["departments"] == 2
    assert store.portal_history("kerala")[0]["source"] == "check"
    coverage.record(store, PORTAL, probe(Client(org_error=PortalError("walled", "captcha"))), NOW)
    assert store.portal_statuses()["kerala"]["kind"] == "captcha"
    assert store.portal_coverages()["kerala"]["result"]["verdict"] == "failed"


def test_removing_a_portal_removes_its_test_result():
    store = SeenStore(":memory:")
    coverage.record(store, PORTAL, probe(Client()), NOW)
    store.delete_portal_status("kerala")
    assert store.portal_coverages() == {}


def test_the_csv_is_safe_to_open_in_a_spreadsheet_and_says_the_result_in_words():
    store = SeenStore(":memory:")
    evil = Portal("evil", "=HYPERLINK(\"http://x\")", "S", "https://e.gov.in/nicgep")
    coverage.record(store, PORTAL, probe(Client()), NOW)
    coverage.record(store, evil, probe(Client(org_error=PortalError("walled", "captcha"))), NOW)
    text = coverage.to_csv(coverage.report(store.portal_coverages(), [PORTAL, evil], {"kerala"}))
    assert text.startswith("﻿Portal,") and "Proven: tenders and their closing dates were read" in text and "Captcha" in text
    assert "'=HYPERLINK" in text and "\n=HYPERLINK" not in text


# ---- the page's endpoints ----------------------------------------------------------------------------------------

PASSWORD = "correct horse"


@pytest.fixture
def app():
    store = SeenStore(":memory:")
    env = Settings(portals=[PORTAL], recipients=["a@example.com"], brevo_api_key="k", mail_from="f@example.com", mail_from_name="T", state_path=":memory:", request_delay=0)
    seen = []

    def deep(portal):
        seen.append(portal.key)
        return probe(Client())

    created = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False), deep_prober=deep)
    client = TestClient(created)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return type("App", (), {"store": store, "client": client, "seen": seen})


def test_testing_a_portal_in_depth_keeps_the_result_for_the_report(app):
    assert app.client.get("/api/portals/coverage").json()["summary"]["untested"] == 1
    r = app.client.post("/api/portals/kerala/coverage")
    assert r.status_code == 200 and r.json()["verdict"] == "proven" and app.seen == ["kerala"]
    rep = app.client.get("/api/portals/coverage").json()
    assert rep["summary"]["proven"] == 1 and rep["rows"][0]["listed"] == 10
    assert app.client.get("/api/portals").json()["portals"][0]["kind"] == "ok"        # and the plain status moved with it


def test_testing_an_unknown_portal_is_not_found(app):
    assert app.client.post("/api/portals/nowhere/coverage").status_code == 404


def test_the_report_can_be_downloaded_as_csv(app):
    app.client.post("/api/portals/kerala/coverage")
    r = app.client.get("/api/portals/coverage.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and "Kerala" in r.text


def test_the_coverage_endpoints_need_a_sign_in(app):
    fresh = TestClient(app.client.app)
    assert fresh.get("/api/portals/coverage").status_code == 401
    assert fresh.get("/api/portals/coverage.csv").status_code == 401
    assert fresh.post("/api/portals/kerala/coverage").status_code == 401


# ---- the command line --------------------------------------------------------------------------------------------

@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.db"))
    monkeypatch.delenv("PORTALS", raising=False)
    return tmp_path


def test_the_command_tests_each_portal_prints_progress_and_writes_the_csv(cli_env):
    lines = []
    asked = []

    def run(portal):
        asked.append(portal.key)
        return probe(Client()) if portal.key == "maharashtra" else probe(Client(org_error=PortalError("walled", "captcha")))

    code = coverage.main(["--csv", str(cli_env / "r.csv")], probe=run, out=lines.append)
    assert code == 0 and asked == ["maharashtra", "delhi", "up", "central"]
    assert "[1/4] Maharashtra" in lines[0] and "Proven" in lines[0] and "[2/4]" in lines[1]
    assert any("1 of 4 portals proven readable from this server" in x for x in lines)
    assert "Captcha" in (cli_env / "r.csv").read_text(encoding="utf-8")
    store = SeenStore(str(cli_env / "state.db"))                              # saved, so the Settings page shows it
    assert store.portal_coverages()["maharashtra"]["result"]["verdict"] == "proven" and len(store.portal_coverages()) == 4


def test_the_command_can_test_a_chosen_portal_without_saving(cli_env):
    lines = []
    assert coverage.main(["--only", "delhi", "--no-save"], probe=lambda p: probe(Client()), out=lines.append) == 0
    assert sum(1 for x in lines if x.startswith("[")) == 1
    assert SeenStore(str(cli_env / "state.db")).portal_coverages() == {}


def test_the_command_can_test_known_portals_that_are_not_added_without_adding_them(cli_env):
    asked = []
    coverage.main(["--catalogue", "--no-save"], probe=lambda p: asked.append(p.key) or probe(Client()), out=lambda t: None)
    assert len(asked) > 10 and "kerala-etenders" in asked
    store = SeenStore(str(cli_env / "state.db"))
    assert store.get_settings().get("custom_portals", []) == [] and store.portal_coverages() == {}


def test_the_command_says_so_when_there_is_nothing_to_test(cli_env):
    lines = []
    assert coverage.main(["--only", "nonesuch"], probe=lambda p: {}, out=lines.append) == 1 and lines == ["No portals to test."]
