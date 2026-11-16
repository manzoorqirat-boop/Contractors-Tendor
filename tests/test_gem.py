"""The GeM reader (experimental): what it sends, how it reads rows, and every way the portal can answer unexpectedly. Run against a fake session."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tender_digest import collector, gem, web
from tender_digest.config import Portal
from tender_digest.gem import GemClient, parse_docs
from tender_digest.gepnic import PortalError
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, settings

TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
PAGE = "<html><body>All Bids</body></html>"


def row(n, item="Construction of road, Nashik", **over):
    d = {"b_bid_number": [f"GEM/2026/B/{n}"], "b_category_name": [item], "final_start_date_sort": ["2026-11-10T04:30:00Z"], "final_end_date_sort": ["2026-11-24T10:30:00Z"],
         "ba_official_details_minName": ["Ministry of Road Transport"], "ba_official_details_deptName": ["PWD Nashik"], "b_id": [900000 + n], "b_total_quantity": [1]}
    d.update(over)
    return d


class Cookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class Resp:
    def __init__(self, status=200, body="", json_body=None, headers=None):
        self.status_code, self.text, self._json = status, body, json_body
        self.content = json.dumps(json_body).encode() if json_body is not None else (body.encode() if isinstance(body, str) else body)
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class Session:
    """A stand-in for the network: records every call and answers by rule."""
    def __init__(self, rows_by_page=None, page=None, cookies=(("csrf_gem_cookie", TOKEN),)):
        self.headers, self.calls = {}, []
        self.cookies = [Cookie(*c) for c in cookies]
        self.rows_by_page = rows_by_page if rows_by_page is not None else {1: [row(1), row(2)]}
        self.page = page or Resp(200, PAGE)
        self.search_answer = None

    def request(self, method, url, timeout=None, allow_redirects=None, **kw):
        self.calls.append((method, url, kw, allow_redirects))
        if url.endswith("/all-bids") and method == "GET":
            return self.page
        if url.endswith("/all-bids-data"):
            if self.search_answer is not None:
                return self.search_answer
            payload = json.loads(kw["data"]["payload"])
            docs = self.rows_by_page.get(payload["page"], [])
            return Resp(200, json.dumps({}), {"code": 200, "response": {"response": {"numFound": len(docs), "docs": docs}}}, {"Content-Type": "application/json"})
        if "/showbidDocument/" in url:
            return Resp(200, b"%PDF-1.4 bid document", headers={"Content-Type": "application/pdf"})
        return Resp(404)


def client(sess):
    return GemClient("https://bidplus.gem.gov.in", delay=0, session=sess)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(gem.time, "sleep", lambda s: None)


# ---- reading rows ---------------------------------------------------------------------------------------------------

def test_rows_become_tenders_with_india_time_and_a_department_chain():
    t = parse_docs([row(7)], "gem")[0]
    assert (t.portal, t.tender_id, t.key) == ("gem", "GEM/2026/B/7", "gem:GEM/2026/B/7")
    assert t.title == t.description == "Construction of road, Nashik" and t.organisation == "Ministry of Road Transport||PWD Nashik"
    assert t.published == datetime(2026, 11, 10, 10, 0) and t.closing == datetime(2026, 11, 24, 16, 0)      # UTC -> India time (+5:30)
    assert t.detail_path == "https://bidplus.gem.gov.in/showbidDocument/900007" and t.value is None


def test_odd_rows_are_skipped_or_tolerated():
    got = parse_docs([row(1), {"b_category_name": ["no number"]}, "junk", None, {"b_bid_number": "GEM/2026/B/9", "final_end_date_sort": "not a date"}, {"b_bid_number": [""]}], "gem")
    assert [t.tender_id for t in got] == ["GEM/2026/B/1", "GEM/2026/B/9"] and got[1].closing is None and got[1].title == "GEM/2026/B/9"
    assert parse_docs([], "gem") == [] and parse_docs(None, "gem") == []                                      # type: ignore[arg-type]


# ---- talking to the portal ------------------------------------------------------------------------------------------

def test_the_security_token_is_sent_back_and_search_words_stand_in_for_departments():
    s = Session()
    c = client(s)
    orgs = c.organisations()
    assert [o[0] for o in orgs] == gem.SEARCH_TERMS and all(o[1] == 1 for o in orgs) and [x[1] for x in s.calls] == ["https://bidplus.gem.gov.in/all-bids"]
    got = c.tenders_of("road", "gem")
    assert [t.tender_id for t in got] == ["GEM/2026/B/1", "GEM/2026/B/2"]
    method, url, kw, redirects = s.calls[-1]
    sent = kw["data"]
    assert method == "POST" and url == "https://bidplus.gem.gov.in/all-bids-data" and sent["csrf_bd_gem_nk"] == TOKEN and redirects is False
    assert json.loads(sent["payload"])["param"]["searchBid"] == "road" and kw["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert [call[1] for call in s.calls].count("https://bidplus.gem.gov.in/all-bids") == 1                   # the token is fetched once


def test_pages_are_followed_until_a_short_page_and_never_beyond_the_limit():
    full = [row(i) for i in range(10)]
    s = Session(rows_by_page={1: full, 2: full[:4]})
    assert len(client(s).tenders_of("road", "gem")) == 14
    s2 = Session(rows_by_page={p: [row(p * 100 + i) for i in range(10)] for p in range(1, 20)})
    assert len(client(s2).tenders_of("road", "gem")) == 10 * gem.MAX_PAGES_PER_TERM


def test_it_never_goes_faster_than_one_request_a_second():
    assert GemClient("https://bidplus.gem.gov.in", delay=0).delay >= 1.0


def test_the_token_can_come_from_the_page_instead_of_a_cookie():
    s = Session(cookies=(), page=Resp(200, f'<script>var x = {{ csrf_bd_gem_nk: "{TOKEN}" }}</script>'))
    assert client(s).organisations()


@pytest.mark.parametrize("page,kind", [
    (Resp(200, "<html>nothing useful</html>"), "different_software"),
    (Resp(200, "<html>Please enter the captcha shown</html>"), "captcha"),
    (Resp(200, "<html><input type=password>Please log in</html>"), "login"),
    (Resp(403), "blocked"), (Resp(401), "login"), (Resp(404), "different_software"), (Resp(503), "unreachable"),
    (Resp(302, headers={"Location": "https://bidplus.gem.gov.in/login"}), "login"),
    (Resp(302, headers={"Location": "https://evil.example.com/"}), "different_software"),
])
def test_a_page_that_is_not_what_was_expected_says_why(page, kind):
    with pytest.raises(PortalError) as e:
        client(Session(cookies=(), page=page)).organisations()
    assert e.value.kind == kind


@pytest.mark.parametrize("answer,kind", [
    (Resp(200, "<html>captcha</html>"), "captcha"),
    (Resp(200, "{}", {"unexpected": True}), "different_software"),
    (Resp(200, "{}", {"response": {"response": {"docs": "not a list"}}}), "different_software"),
    (Resp(403), "blocked"),
])
def test_an_answer_in_an_unknown_shape_is_reported_not_guessed(answer, kind):
    s = Session()
    s.search_answer = answer
    c = client(s)
    with pytest.raises(PortalError) as e:
        c.tenders_of("road", "gem")
    assert e.value.kind == kind


def test_it_only_ever_talks_to_gem(monkeypatch):
    s = Session()
    c = client(s)
    with pytest.raises(PortalError, match="away from GeM"):
        c.download("https://evil.example.com/steal")
    with pytest.raises(PortalError, match="away from GeM"):
        c.download("http://bidplus.gem.gov.in/plain")
    assert not [call for call in s.calls if "evil" in call[1] or call[1].startswith("http:")]
    monkeypatch.setattr(gem, "is_public_host", lambda h: False)
    with pytest.raises(PortalError, match="private network"):
        GemClient("https://bidplus.gem.gov.in", delay=0).organisations()          # a real session would be checked first


# ---- what the rest of the app asks of a client ---------------------------------------------------------------------

def test_find_detail_documents_download_and_sample():
    s = Session(rows_by_page={1: [row(5)]})
    c = client(s)
    t = c.find("gem", "GEM/2026/B/5")
    assert t and t.title.startswith("Construction")
    n = len(s.calls)
    assert c.find("gem", "GEM/2026/B/5") is t and len(s.calls) == n                 # remembered: no second request
    assert c.find("gem", "GEM/2026/B/404") is None
    pairs, corrigenda, docs = c.detail_full(t)
    assert pairs["Work Description"] == t.title and pairs["Bid Number"] == t.tender_id and pairs["Quantity"] == "1" and corrigenda == []
    assert docs == [{"name": "GeM bid document (tender notice)", "href": t.detail_path}]
    assert c.detail_page(t) == (pairs, [])
    data, ctype = c.download(t.detail_path)
    assert data.startswith(b"%PDF") and ctype == "application/pdf"
    ctype, body = c.sample()
    assert "json" in ctype and b"GEM/2026/B/5" in body and TOKEN.encode() not in body


def test_fetch_from_portal_finds_a_gem_bid_by_its_number():
    from tender_digest.bids import locate_on_portal
    s = Session(rows_by_page={1: [row(5)]})
    t = locate_on_portal(client(s), "gem", "Ministry||Dept", "GEM/2026/B/5")
    assert t and t.tender_id == "GEM/2026/B/5"


# ---- inside the collector and the portal list -----------------------------------------------------------------------

def test_the_collector_picks_the_gem_reader_by_address_and_keeps_only_civil_bids():
    portal = Portal("gem", "GeM", "Central", "https://bidplus.gem.gov.in")
    assert isinstance(collector.default_client(portal, settings()), GemClient)
    assert not isinstance(collector.default_client(Portal("m", "M", "MH", "https://mahatenders.gov.in/nicgep"), settings()), GemClient)
    assert gem.is_gem("https://www.bidplus.gem.gov.in/") and not gem.is_gem("https://gem.example.com")
    rows = [row(1), row(2, item="Supply of laptops and printers"), row(3, item="Repair and renovation of school building")]
    sess = Session(rows_by_page={1: rows})
    env = settings(portals=[portal], lookback_days=3650)
    store = SeenStore(":memory:")
    reports = collector.collect(env, store, datetime(2026, 11, 12, 12, 0), lambda p, s: client(sess))
    kept = {t.tender_id for t in store.pending(datetime(2026, 11, 12, 12, 0))}
    assert kept == {"GEM/2026/B/1", "GEM/2026/B/3"} and reports[0].access == "ok" and reports[0].organisations_read == len(gem.SEARCH_TERMS)


def test_a_gem_failure_is_reported_on_the_portal_list_not_as_a_crash():
    portal = Portal("gem", "GeM", "Central", "https://bidplus.gem.gov.in")
    store = SeenStore(":memory:")
    reports = collector.collect(settings(portals=[portal]), store, datetime(2026, 11, 12), lambda p, s: client(Session(cookies=(), page=Resp(403))))
    assert reports[0].failed and reports[0].access == "blocked"


def test_sample_download_endpoint(tmp_path, monkeypatch):
    class Reader:
        search_url = "https://x"

        def sample(self):
            return "text/html", b"<html>what the portal returned</html>"

        def organisations(self):
            return []

    store = SeenStore(":memory:")
    env = settings()
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store,
                         RunManager(env, store, lambda p, s: Reader(), FakeSender(), background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    r = c.get("/api/portals/maha/sample")
    assert r.status_code == 200 and r.content == b"<html>what the portal returned</html>" and 'sample-maha.html' in r.headers["content-disposition"]
    assert c.get("/api/portals/nope/sample").status_code == 404
    assert any(e["action"] == "Downloaded a sample of what a portal returns" for e in c.get("/api/audit").json()["entries"])
    c.post("/api/users", json={"username": "raj", "role": "bidder", "password": "temporary-pass-1"})
    raj = TestClient(app)
    raj.post("/api/login", json={"username": "raj", "password": "temporary-pass-1"})
    raj.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
    assert raj.get("/api/portals/maha/sample").status_code == 403


def test_a_reader_without_a_sample_function_is_explained(monkeypatch):
    class Plain:
        search_url = "https://x"

    store = SeenStore(":memory:")
    env = settings()
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store,
                         RunManager(env, store, lambda p, s: Plain(), FakeSender(), background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    assert c.get("/api/portals/maha/sample").status_code == 422
