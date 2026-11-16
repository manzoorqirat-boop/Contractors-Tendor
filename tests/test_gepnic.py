from datetime import datetime

from conftest import fixture
from tender_digest import gepnic


def test_parse_money_handles_indian_grouping_and_junk():
    assert gepnic.parse_money("8,51,184") == 851184
    assert gepnic.parse_money("20,99,125.50") == 2099125.5
    assert gepnic.parse_money("NA") is None
    assert gepnic.parse_money("") is None
    assert gepnic.parse_money("1.2.3") is None


def test_parse_date():
    assert gepnic.parse_date("19-Nov-2026 05:30 PM") == datetime(2026, 11, 19, 17, 30)
    assert gepnic.parse_date("not a date") is None


def test_organisations_listed_with_counts_and_links():
    orgs = gepnic.parse_organisations(fixture("orgs.html"))
    names = {n: c for n, c, _ in orgs}
    assert names["Public Works Department Mantralaya"] == 38
    assert all(link for _, _, link in orgs)


def test_tender_list_from_real_page():
    tenders = gepnic.parse_tender_list(fixture("org_tenders.html"), "maharashtra")
    assert len(tenders) == 38  # the portal itself lists this department as 38
    first = tenders[0]
    assert first.tender_id == "2026_PWDM_1354069_1"
    assert first.reference.startswith("Est No.")
    assert first.published == datetime(2026, 11, 13, 17, 0)
    assert first.closing == datetime(2026, 11, 19, 17, 30)
    assert first.organisation.startswith("Public Works Department Mantralaya||")
    assert first.detail_path.startswith("/nicgep/app")
    assert len({t.key for t in tenders}) == 38  # ids are unique


def test_detail_page_gives_value_and_category():
    t = gepnic.parse_tender_list(fixture("org_tenders.html"), "maharashtra")[0]
    gepnic.apply_detail(t, gepnic.parse_detail(fixture("tender_detail.html")))
    assert t.value == 851184
    assert t.category == "Electrical Works"
    assert t.location == "Chh Sambhajinagar"
    assert t.emd == 8600
    assert t.description.startswith("Est No.")


def test_empty_or_unrelated_html_gives_nothing_instead_of_crashing():
    assert gepnic.parse_organisations("<html></html>") == []
    assert gepnic.parse_tender_list("<html><body>captcha</body></html>", "x") == []
    assert gepnic.parse_detail("<html></html>") == {}


def test_addresses_resolve_under_the_portal_path():
    """Our own paths are relative to the portal's base; links taken from its pages are absolute paths on the host."""
    seen = []

    class Http:
        headers = {}

        def get(self, url, timeout, **kw):
            seen.append(url)

            class R:
                status_code = 200
                text = fixture("orgs.html")
            return R()

    client = gepnic.GepnicClient("https://mahatenders.gov.in/nicgep", delay=0, session=Http())
    client.organisations()
    client._get("/nicgep/app?component=%24DirectLink&amp;page=X&amp;service=direct&amp;sp=abc")
    assert seen[0] == "https://mahatenders.gov.in/nicgep/app?page=FrontEndTendersByOrganisation&service=page"
    assert seen[1] == "https://mahatenders.gov.in/nicgep/app?component=%24DirectLink&page=X&service=direct&sp=abc"
