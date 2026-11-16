"""The portal client only ever talks to the portal's own host, so a page (or a redirect) cannot aim this server at another address."""
import pytest

from tender_digest import gepnic
from tender_digest.gepnic import GepnicClient, PortalError

BASE = "https://x.gov.in/nicgep"


class Resp:
    def __init__(self, status=200, text="ok", location=None, body=b"%PDF-1.4 x"):
        self.status_code, self.text, self.headers = status, text, ({"Location": location} if location else {})
        self.history, self.content = [], body
        self.raw = type("Raw", (), {"read": lambda self, n, decode_content=True: body[:n]})()


class Http:
    def __init__(self, routes):
        self.routes, self.headers, self.asked = routes, {}, []

    def get(self, url, timeout, allow_redirects=True, stream=False):
        assert allow_redirects is False  # the client follows redirects itself, so it can check each hop
        self.asked.append(url)
        r = self.routes[url] if url in self.routes else Resp(404)
        r.url = url  # a real response knows the address it came from
        return r


def client(routes):
    http = Http(routes)
    return GepnicClient(BASE, delay=0, session=http), http


def test_a_document_link_to_another_host_is_not_fetched():
    c, http = client({})
    for href in ("http://169.254.169.254/latest/meta-data/", "https://evil.example/x.pdf", "//evil.example/x.pdf", "http://x.gov.in/nicgep/file.pdf"):
        with pytest.raises(PortalError) as e:
            c.download(href)
        assert e.value.kind == "different_software", href
    assert http.asked == []  # nothing was requested at all


def test_a_redirect_off_the_portal_is_not_followed():
    c, http = client({f"{BASE}/app?page=A": Resp(302, location="http://169.254.169.254/latest/meta-data/")})
    with pytest.raises(PortalError) as e:
        c._get("app?page=A", attempts=1)
    assert "another website" in str(e.value)
    assert http.asked == [f"{BASE}/app?page=A"]  # the second address was never requested


def test_a_redirect_that_stays_on_the_portal_still_works():
    c, http = client({
        f"{BASE}/app?page=A": Resp(302, location="/nicgep/app?page=B"),
        f"{BASE}/app?page=B": Resp(200, text="the page"),
    })
    assert c._get("app?page=A") == "the page"


def test_a_login_redirect_on_the_portal_is_still_recognised():
    c, _ = client({f"{BASE}/app?page=A": Resp(302, location="/nicgep/app?page=Login"), f"{BASE}/app?page=Login": Resp(200, text="sign in")})
    with pytest.raises(PortalError) as e:
        c._get("app?page=A", attempts=1)
    assert e.value.kind == "login"


def test_the_www_twin_of_the_portal_counts_as_the_same_site():
    c, _ = client({f"{BASE}/app?page=A": Resp(301, location="https://www.x.gov.in/nicgep/app?page=B"), "https://www.x.gov.in/nicgep/app?page=B": Resp(200, text="twin")})
    assert c._get("app?page=A") == "twin"


def test_redirect_loops_stop():
    c, _ = client({f"{BASE}/app?page=A": Resp(302, location="/nicgep/app?page=A")})
    with pytest.raises(PortalError):
        c._get("app?page=A", attempts=1)


def test_a_portal_name_that_points_at_a_private_network_is_not_contacted(monkeypatch):
    class Real:
        headers = {}

        def get(self, *a, **k):
            raise AssertionError("must not be requested")

    monkeypatch.setattr(gepnic, "is_public_host", lambda h: False)
    c = GepnicClient(BASE, delay=0)  # a real client checks where the name points before each request
    c.http = Real()
    with pytest.raises(PortalError) as e:
        c._get("app?page=A", attempts=1)
    assert e.value.kind == "blocked"


def test_a_document_that_does_download_comes_back_whole():
    c, _ = client({f"{BASE}/doc.pdf": Resp(200, body=b"%PDF-1.4 notice")})
    data, _ctype = c.download("/nicgep/doc.pdf")
    assert data == b"%PDF-1.4 notice"
