"""A portal whose page changed is still read: by what each column contains, with the real saved portal page as the starting point."""
import re
from datetime import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from tender_digest import collector, gepnic, layout
from tender_digest.config import Portal, Settings
from tender_digest.gepnic import GepnicClient
from tender_digest.store import SeenStore

FIX = Path(__file__).parent / "fixtures"
LIST_HTML = (FIX / "org_tenders.html").read_text(encoding="latin-1")
ORGS_HTML = (FIX / "orgs.html").read_text(encoding="latin-1")


def table_of(soup):
    return soup.find("table", id="table")


def changed(transform, html=LIST_HTML):
    soup = BeautifulSoup(html, "lxml")
    transform(soup, table_of(soup))
    return str(soup)


def rename(soup, t):
    t["id"], t["class"] = "tenderResults", ["grid"]


def insert_column(at, text="X"):
    def go(soup, t):
        rename(soup, t)
        for i, tr in enumerate(t.find_all("tr")):
            cell = soup.new_tag("td")
            cell.string = "Estimated value" if i == 0 else "1,00,000"
            tds = tr.find_all("td")
            if not tds:
                continue
            (tds[at].insert_before if at < len(tds) else tds[-1].insert_after)(cell)
    return go


def swap_text(a, b):
    """Swap the contents of two columns in every row (header included), leaving the table named as the reader expects."""
    def go(soup, t):
        for tr in t.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            ta, tb = tds[a].decode_contents(), tds[b].decode_contents()
            tds[a].clear()
            tds[a].append(BeautifulSoup(tb, "lxml").body or "")
            tds[b].clear()
            tds[b].append(BeautifulSoup(ta, "lxml").body or "")
    return go


def reformat_dates(soup, t):
    for td in t.find_all("td"):
        txt = td.get_text(" ", strip=True)
        if re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2} [AP]M", txt):
            d = datetime.strptime(txt, "%d-%b-%Y %I:%M %p")
            td.string = d.strftime("%d/%m/%Y %H:%M")


def drop_brackets(soup, t):
    rename(soup, t)
    for tr in t.find_all("tr")[1:]:
        if len(tr.find_all("td")) < 6:
            continue
        td = tr.find_all("td")[4]
        ids = re.findall(r"\[([^\[\]]*)\]", td.get_text(" ", strip=True))
        a = td.find("a")
        text = f"Work as described {ids[-1]}"
        if a:
            a.string = text
            for extra in list(td.children):
                if extra is not a and getattr(extra, "name", None) != "a":
                    extra.extract()
        else:
            td.string = text


BASE = gepnic.parse_tender_list(LIST_HTML, "maha")


def same_tenders(got):
    assert [t.tender_id for t in got] == [t.tender_id for t in BASE]
    assert [t.closing for t in got] == [t.closing for t in BASE] and [t.published for t in got] == [t.published for t in BASE] and [t.opening for t in got] == [t.opening for t in BASE]
    assert [t.organisation for t in got] == [t.organisation for t in BASE] and all(t.detail_path.startswith("/nicgep/app") for t in got)


# ---- dates -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,when", [
    ("13-Nov-2026 05:00 PM", datetime(2026, 11, 13, 17, 0)), ("13/11/2026 17:00", datetime(2026, 11, 13, 17, 0)), ("13-11-2026", datetime(2026, 11, 13)), ("2026-11-13 09:05", datetime(2026, 11, 13, 9, 5)),
    ("13.11.2026 17:00", datetime(2026, 11, 13, 17, 0)), ("Closes 19 Nov 2026 5:30 pm sharp", datetime(2026, 11, 19, 17, 30)), ("13-Nov-2026 5:00 PM", datetime(2026, 11, 13, 17, 0)),
])
def test_dates_are_found_in_the_usual_portal_formats(text, when):
    assert layout.any_date(text) == when


@pytest.mark.parametrize("text", ["", None, "Estimated value", "Est No. 222/Notice No.11/2026-27", "99-99-9999", "13-Foo-2026"])
def test_text_without_a_date_has_none(text):
    assert layout.any_date(text) is None


# ---- the exact reader is untouched on the real page --------------------------------------------------------------------

def test_the_real_page_still_reads_exactly_and_the_fallback_agrees_with_it():
    assert len(BASE) == 38
    found, info = layout.adaptive_tender_list(LIST_HTML, "maha")
    same_tenders(found)
    assert info["how"] == "header words" and info["read"] == 38 and set(info["columns"]) == {"published", "closing", "opening", "title", "org"}


# ---- a changed page is still read --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,transform", [
    ("table renamed", rename),
    ("a column added at the start", insert_column(0)),
    ("a column added in the middle", insert_column(3)),
    ("a column added at the end", insert_column(99)),
    ("dates in another format", lambda s, t: (rename(s, t), reformat_dates(s, t))),
    ("titles without brackets", drop_brackets),
])
def test_a_changed_layout_gives_the_same_tenders(name, transform):
    html = changed(transform)
    found, info = layout.adaptive_tender_list(html, "maha")
    assert len(found) == 38, name
    assert [t.tender_id for t in found] == [t.tender_id for t in BASE] and [t.closing for t in found] == [t.closing for t in BASE], name
    assert all(t.detail_path for t in found) and info["read"] == 38


def test_the_reader_alone_finds_nothing_in_those_pages_which_is_why_the_fallback_matters():
    for transform in (rename, insert_column(0), insert_column(3), drop_brackets):
        assert gepnic.parse_tender_list(changed(transform), "maha") == []


def test_columns_that_trade_places_are_followed_by_their_contents():
    # published and closing swapped in place (header words and dates both move)
    html = changed(swap_text(1, 2))
    got, info = layout.adaptive_tender_list(html, "maha")
    assert [t.published for t in got] == [t.published for t in BASE] and [t.closing for t in got] == [t.closing for t in BASE]
    assert info["how"] == "header words"


def test_with_no_header_the_earliest_dates_are_the_publish_dates():
    soup = BeautifulSoup(changed(rename), "lxml")
    first = table_of(soup) or soup.find("table", id="tenderResults")
    first.find_all("tr")[0].extract()
    got, info = layout.adaptive_tender_list(str(soup), "maha")
    assert info["how"] == "content" and [t.published for t in got] == [t.published for t in BASE] and [t.closing for t in got] == [t.closing for t in BASE] and [t.opening for t in got] == [t.opening for t in BASE]


def test_a_table_nested_inside_a_layout_table_is_still_found():
    soup = BeautifulSoup(changed(rename), "lxml")
    inner = soup.find("table", id="tenderResults")
    outer = soup.new_tag("table")
    tr, td = soup.new_tag("tr"), soup.new_tag("td")
    inner.wrap(td)
    td.wrap(tr)
    tr.wrap(outer)
    got, _ = layout.adaptive_tender_list(str(soup), "maha")
    assert [t.tender_id for t in got] == [t.tender_id for t in BASE]


# ---- it does not invent tenders ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("html", [
    "", "<html><body><p>Enter the captcha shown below</p><form></form></body></html>", "<table><tr><td>a</td><td>b</td></tr></table>",
    "<table><tr><td>12-Nov-2026</td><td>19-Nov-2026</td><td>no id here</td></tr><tr><td>12-Nov-2026</td><td>19-Nov-2026</td><td>nor here</td></tr></table>",
])
def test_a_page_without_tenders_gives_none(html):
    assert layout.adaptive_tender_list(html, "maha") == ([], {})


def test_rows_without_a_tender_id_are_skipped_not_guessed():
    html = changed(drop_brackets)
    soup = BeautifulSoup(html, "lxml")
    row = soup.find("table", id="tenderResults").find_all("tr")[2]
    row.find_all("td")[4].string = "A work with no identifier at all"
    got, _ = layout.adaptive_tender_list(str(soup), "maha")
    assert len(got) == 37


def test_the_date_sanity_check_notices_swapped_columns():
    ok = BASE
    swapped = [gepnic.Tender(**{**t.__dict__, "published": t.closing, "closing": t.published}) for t in BASE]
    assert layout.dates_sane(ok) and not layout.dates_sane(swapped) and layout.dates_sane(swapped[:2]) and layout.dates_sane([])


# ---- the department list -----------------------------------------------------------------------------------------------

def test_the_department_list_is_found_by_its_links_and_numbers():
    strict = gepnic.parse_organisations(ORGS_HTML)
    soup = BeautifulSoup(ORGS_HTML, "lxml")
    soup.find("table", id="table")["id"] = "deptGrid"
    soup.find("table", class_="list_table")["class"] = ["grid"]
    html = str(soup)
    found, info = layout.adaptive_organisations(html)
    assert [(n, c) for n, c, _ in found] == [(n, c) for n, c, _ in strict] and all(link for _, _, link in found) and info["rows"] == len(strict)


def test_a_page_with_no_department_rows_gives_none():
    assert layout.adaptive_organisations("<table><tr><td>x</td></tr></table>") == ([], {})
    assert layout.adaptive_organisations("") == ([], {})


# ---- inside the reader and a run ---------------------------------------------------------------------------------------

class Page:
    def __init__(self, text):
        self.status_code, self.text, self.url, self.history, self.headers, self.content = 200, text, "https://x.gov.in/nicgep/app", [], {}, text.encode()


class Pages:
    """A stand-in portal: the department page and every tender list are whatever the test says."""

    def __init__(self, orgs, tenders):
        self.orgs, self.tenders, self.headers = orgs, tenders, {}

    def get(self, url, timeout=None, **kw):
        return Page(self.orgs if "FrontEndTendersByOrganisation&service=page" in url else self.tenders)


def renamed_orgs():
    soup = BeautifulSoup(ORGS_HTML, "lxml")
    soup.find("table", id="table")["id"] = "deptGrid"
    soup.find("table", class_="list_table")["class"] = ["grid"]
    return str(soup)


def make_client(orgs, tenders):
    return GepnicClient("https://x.gov.in/nicgep", delay=0, session=Pages(orgs, tenders))


def test_the_client_reads_a_changed_page_and_says_what_it_did():
    c = make_client(renamed_orgs(), changed(insert_column(3)))
    orgs = c.organisations()
    assert len(orgs) >= 3 and "department list" in c.adaptations["departments"]
    got = c.tenders_of(orgs[0][2], "maha")
    assert [t.tender_id for t in got] == [t.tender_id for t in BASE] and "column contents" in c.adaptations["tenders"] and "38 of 38" in c.adaptations["tenders"]


def test_the_client_does_not_adapt_when_the_exact_layout_works():
    c = make_client(ORGS_HTML, LIST_HTML)
    c.tenders_of(c.organisations()[0][2], "maha")
    assert c.adaptations == {}


def test_swapped_dates_that_the_exact_layout_reads_wrongly_are_caught_and_read_right():
    swapped = changed(swap_text(1, 2))
    assert not layout.dates_sane(gepnic.parse_tender_list(swapped, "maha"))                    # the exact reader happily reads closing before publish
    c = make_client(ORGS_HTML, swapped)
    got = c.tenders_of("x", "maha")
    assert [t.closing for t in got] == [t.closing for t in BASE] and "tenders" in c.adaptations


def test_a_page_that_is_really_empty_stays_empty():
    c = make_client(ORGS_HTML, "<html><body><table id='table'><tr class='list_header'><td>S.No</td><td>Date</td></tr></table></body></html>")
    assert c.tenders_of("x", "maha") == [] and c.adaptations == {}


def test_a_run_records_that_the_layout_changed_but_the_portal_is_still_fine():
    store, now = SeenStore(":memory:"), datetime(2026, 11, 18, 9, 0)
    settings = Settings(portals=[Portal("maha", "Maharashtra", "MH", "https://x.gov.in/nicgep")], recipients=["a@x.in"], brevo_api_key="k", mail_from="f@x.in", mail_from_name="T",
                        state_path=":memory:", request_delay=0, lookback_days=3650, max_detail_fetches=0)
    client = make_client(renamed_orgs(), changed(insert_column(3)))
    reports = collector.collect(settings, store, now, lambda p, s: client)
    assert reports[0].access == "ok" and reports[0].listed == 38 * reports[0].organisations_read and reports[0].organisations_read >= 1
    status = store.portal_statuses()["maha"]
    assert status["kind"] == "ok" and status["detail"].startswith("Layout changed: ") and "column contents" in status["detail"]


# ---- diagnosis ---------------------------------------------------------------------------------------------------------

def test_the_diagnosis_says_the_reader_works_when_it_does():
    out = make_client(ORGS_HTML, LIST_HTML).diagnose()
    assert out["tenders"]["exact_layout"] == 38 and out["verdict"] == "The reader works on this page as designed." and out["tenders"]["sample"]


def test_the_diagnosis_explains_a_changed_page_the_fallback_can_read():
    out = make_client(renamed_orgs(), changed(insert_column(3))).diagnose()
    assert out["tenders"]["exact_layout"] == 0 and out["tenders"]["by_content"] == 38
    assert "layout has changed" in out["verdict"] and "found 38 tenders" in out["verdict"] and "title = column" in out["verdict"] and out["tenders"]["sample"][0]["tender_id"] == BASE[0].tender_id


def test_the_diagnosis_describes_what_is_on_a_page_it_cannot_read():
    odd = "<html><body><table id='x'><tr><td>Notice</td><td>Maintenance on Sunday</td></tr><tr><td>Notice</td><td>Back on Monday</td></tr></table></body></html>"
    out = make_client(renamed_orgs(), odd).diagnose()
    assert out["tenders"]["by_content"] == 0 and "no table of tenders the reader recognises" in out["verdict"] and "table x" in out["verdict"] and out["tables"][0]["rows"] == 2


def test_the_diagnosis_of_a_captcha_page_points_at_the_wall():
    out = make_client("<html><body>Enter the captcha shown below <img src=c.png><input name=captcha></body></html>", "").diagnose()
    assert out["page_kind"] == "captcha" and "department list could not be found" in out["verdict"]


# ---- the diagnose endpoint ---------------------------------------------------------------------------------------------

def app_with(client_or_factory):
    from fastapi.testclient import TestClient

    from tender_digest import web
    from tender_digest.runner import RunManager
    from tests.test_portals import PASSWORD, env_settings
    store, env = SeenStore(":memory:"), env_settings()
    factory = client_or_factory if callable(client_or_factory) and not hasattr(client_or_factory, "diagnose") else (lambda p, s: client_or_factory)
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, factory, background=False))
    c = TestClient(app)
    c.post("/api/login", json={"password": PASSWORD})
    return c, app


def test_the_diagnose_endpoint_reports_what_the_reader_sees():
    c, _ = app_with(make_client(renamed_orgs(), changed(insert_column(3))))
    out = c.get("/api/portals/maha/diagnose").json()
    assert out["key"] == "maha" and out["tenders"]["by_content"] == 38 and "layout has changed" in out["verdict"] and out["name"]
    assert c.get("/api/portals/nowhere/diagnose").status_code == 404


def test_a_reader_that_cannot_be_diagnosed_or_a_portal_that_cannot_be_reached_says_so():
    c, _ = app_with(object())
    assert c.get("/api/portals/maha/diagnose").status_code == 422

    class Down:
        def diagnose(self):
            raise gepnic.PortalError("timed out", "unreachable")
    c2, _ = app_with(Down())
    r = c2.get("/api/portals/maha/diagnose")
    assert r.status_code == 502 and "timed out" in r.json()["detail"]


def test_only_an_administrator_may_make_the_app_look_at_a_portal():
    from fastapi.testclient import TestClient

    from tests.test_portals import PASSWORD
    c, app = app_with(make_client(ORGS_HTML, LIST_HTML))
    assert TestClient(app).get("/api/portals/maha/diagnose").status_code == 401
    c.post("/api/users", json={"username": "bina", "role": "bidder", "password": "temporary-pass-1"})
    b = TestClient(app)
    b.post("/api/login", json={"username": "bina", "password": "temporary-pass-1"})
    b.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
    assert b.get("/api/portals/maha/diagnose").status_code == 403 and PASSWORD
