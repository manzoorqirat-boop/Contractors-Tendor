"""EXPERIMENTAL reader for GeM (Government e-Marketplace) bids, at bidplus.gem.gov.in.

GeM is not NIC GePNIC software: it has no department list. Its public bid page ("All Bids") loads its rows from a JSON endpoint that a
browser calls with the page's own security token. This client does what that page does, for a handful of civil-works search words, and fits
the same shape as the GePNIC client so the collector, the access check and "Fetch from portal" need no special cases.

It was written from how the public page behaves, WITHOUT being able to run against the live site from the build environment, so treat it as
unproven: the field names below are the ones that page has used, every read is defensive, and when the answer does not look as expected the
client says so ("different software") instead of guessing. Settings > Portals can download what the portal actually returned
("Download a sample"), which is what is needed to correct this file if GeM has changed.

What it never does: log in, solve or get around a captcha, or send more than a few polite requests a day. GeM lists goods and services as
well as works, so the usual civil filter still decides what is kept. GeM bids carry no estimated value, so those tenders show "Not stated".
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

from .gepnic import USER_AGENT, PortalError, Tender, _bare_host, classify_page, is_public_host

HOST = "bidplus.gem.gov.in"
IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 10
# What is searched for each day: a few plain words that civil-works bids use. Each one is shown in the run log like a "department".
SEARCH_TERMS = ["civil work", "construction", "road", "building", "bridge", "drain", "pipeline", "repair and renovation", "boundary wall", "water supply"]
MAX_PAGES_PER_TERM = 3


def is_gem(base_url: str) -> bool:
    return _bare_host(base_url) == HOST


def _first(doc: dict[str, Any], *names: str) -> Any:
    """The first non-empty value among the named fields. GeM wraps most values in one-item lists."""
    for n in names:
        v = doc.get(n)
        if isinstance(v, list):
            v = next((x for x in v if x not in (None, "")), None)
        if v not in (None, ""):
            return v
    return None


def _when(value: Any) -> datetime | None:
    """GeM gives UTC times like 2025-01-31T10:30:00Z; the app shows India time without a zone."""
    if not value:
        return None
    text = str(value).strip()
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(IST).replace(tzinfo=None)
    return d


def parse_docs(docs: list[Any], portal: str, base: str = f"https://{HOST}") -> list[Tender]:
    """Tenders from the rows of one answer. A row without a bid number is ignored."""
    out: list[Tender] = []
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        number = str(_first(doc, "b_bid_number", "bid_number", "bidNumber") or "").strip()
        if not number:
            continue
        item = str(_first(doc, "b_category_name", "bd_category_name", "b_item_name", "category_name") or "").strip()
        ministry = str(_first(doc, "ba_official_details_minName", "ministry") or "").strip()
        dept = str(_first(doc, "ba_official_details_deptName", "department") or "").strip()
        bid_id = _first(doc, "b_id", "id")
        closing = _when(_first(doc, "final_end_date_sort", "b_end_date"))
        t = Tender(portal=portal, tender_id=number, title=item or number, reference="", organisation="||".join(x for x in (ministry, dept) if x),
                   published=_when(_first(doc, "final_start_date_sort", "b_start_date")), closing=closing, opening=closing,
                   detail_path=f"{base}/showbidDocument/{bid_id}" if bid_id else "", description=item)
        qty = _first(doc, "b_total_quantity", "quantity")
        t.extra = {"gem_id": bid_id, "quantity": qty}
        out.append(t)
    return out


class GemClient:
    """The same calls the collector makes on a GePNIC client. One polite session; a page that is not what was expected raises PortalError."""

    def __init__(self, base_url: str, delay: float = 1.0, session: requests.Session | None = None, timeout: int = 30):
        self.base = f"https://{HOST}"
        self.delay = max(delay, 1.0)  # GeM is a busy public service: never faster than one request a second
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", USER_AGENT)
        self._last = 0.0
        self._token = ""
        self._check_dns = session is None
        self._known: dict[str, Tender] = {}

    @property
    def search_url(self) -> str:
        return f"{self.base}/all-bids"

    # -- one request at a time, to GeM's own https address only --

    def _wait(self) -> None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def _send(self, method: str, url: str, **kw: Any) -> Any:
        parts = urlparse(url)
        if parts.scheme != "https" or _bare_host(url) != HOST:
            raise PortalError("a link pointed away from GeM, which this app does not follow", "different_software")
        if self._check_dns and not is_public_host(parts.hostname or ""):
            raise PortalError("the portal's address points to a private network, so it was not contacted", "blocked")
        self._wait()
        try:
            r = self.http.request(method, url, timeout=self.timeout, allow_redirects=False, **kw)
        except requests.RequestException as e:
            raise PortalError(str(e)[:200], "unreachable") from None
        finally:
            self._last = time.monotonic()
        if r.status_code in (301, 302, 303, 307, 308):
            loc = urljoin(url, r.headers.get("Location", ""))
            if re.search(r"log-?in|signin|sso", loc, re.I):
                raise PortalError("the portal sent us to a login page: it needs an account", "login")
            raise PortalError("the portal redirected to another page, which this app does not follow", "different_software")
        if r.status_code == 401:
            raise PortalError("the portal asks for a login (HTTP 401)", "login")
        if r.status_code == 403:
            raise PortalError("the portal refuses this server (HTTP 403), often because it blocks data-centre addresses", "blocked")
        if r.status_code == 404:
            raise PortalError("that page does not exist on GeM (HTTP 404): the site may have changed", "different_software")
        if r.status_code != 200:
            raise PortalError(f"HTTP {r.status_code} from GeM", "unreachable")
        return r

    def _security_token(self) -> str:
        """The page's own anti-forgery value, which its JSON calls send back. Found where the page puts it (a cookie, or a field in the page)."""
        if self._token:
            return self._token
        r = self._send("GET", self.search_url)
        html = r.text or ""
        found = ""
        for c in self.http.cookies:
            if "csrf" in c.name.lower() and c.value:
                found = c.value
                break
        if not found:
            m = re.search(r"csrf_bd_gem_nk['\"]?\s*[:=,]\s*['\"]([0-9A-Za-z]{16,})['\"]", html) or re.search(r"name=[\"']csrf[^\"']*[\"'][^>]*value=[\"']([0-9A-Za-z]{16,})", html)
            found = m.group(1) if m else ""
        if not found:
            kind = classify_page(html)
            raise PortalError({"captcha": "the portal asks for a captcha", "otp": "the portal asks for an OTP login", "login": "the portal asks for a login"}.get(
                kind, "the bid page did not contain the security token it normally has: GeM may have changed"), kind if kind != "ok" else "different_software")
        self._token = found
        return found

    def _search(self, term: str, page: int) -> list[Any]:
        payload = {"page": page, "param": {"searchBid": term, "searchType": "fullText"},
                   "filter": {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "", "byEndDate": {"from": "", "to": ""}, "sort": "Bid-End-Date-Oldest"}}
        r = self._send("POST", f"{self.base}/all-bids-data", data={"payload": json.dumps(payload), "csrf_bd_gem_nk": self._security_token()},
                       headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.search_url})
        try:
            body = r.json()
            docs = body["response"]["response"]["docs"]
        except (ValueError, KeyError, TypeError):
            kind = classify_page(r.text or "")
            raise PortalError("the bid list came back in a form this reader does not know" + (f" ({kind} page)" if kind != "ok" else ": GeM may have changed"),
                              kind if kind != "ok" else "different_software") from None
        if not isinstance(docs, list):
            raise PortalError("the bid list came back in a form this reader does not know: GeM may have changed", "different_software")
        return docs

    # -- the calls the collector makes --

    def organisations(self, attempts: int = 1) -> list[tuple[str, int, str]]:
        """GeM has no department list, so each search word stands in for one: (word, 1, word). Checks the page and the security token first,
        so a portal this reader cannot use fails here with its reason."""
        self._security_token()
        return [(term, 1, term) for term in SEARCH_TERMS]

    def tenders_of(self, link: str, portal: str) -> list[Tender]:
        out: list[Tender] = []
        for page in range(1, MAX_PAGES_PER_TERM + 1):
            docs = self._search(link, page)
            batch = parse_docs(docs, portal, self.base)
            for t in batch:
                self._known[t.tender_id] = t
            out += batch
            if len(docs) < PAGE_SIZE:
                break
        return out

    def find(self, portal: str, tender_id: str) -> Tender | None:
        """One bid by its number (used by "Fetch from portal"), without paging through the lists."""
        if tender_id in self._known:
            return self._known[tender_id]
        for t in parse_docs(self._search(tender_id, 1), portal, self.base):
            self._known[t.tender_id] = t
        return self._known.get(tender_id)

    def detail(self, tender: Tender) -> dict[str, str]:
        """GeM's list rows already hold what the collector needs, so there is no second page to read."""
        pairs = {"Work Description": tender.title, "Bid Number": tender.tender_id}
        if tender.extra.get("quantity") not in (None, ""):
            pairs["Quantity"] = str(tender.extra["quantity"])
        return pairs

    def detail_full(self, tender: Tender) -> tuple[dict[str, str], list[str], list[dict[str, str]]]:
        docs = [{"name": "GeM bid document (tender notice)", "href": tender.detail_path}] if tender.detail_path else []
        return self.detail(tender), [], docs

    def detail_page(self, tender: Tender) -> tuple[dict[str, str], list[str]]:
        return self.detail(tender), []  # GeM publishes no corrigendum list this reader can see

    def download(self, href: str, max_bytes: int = 10 * 1024 * 1024) -> tuple[bytes, str]:
        r = self._send("GET", href)
        data = r.content[: max_bytes + 1]
        if len(data) > max_bytes:
            raise PortalError("the document is over 10 MB", "unreachable")
        return data, r.headers.get("Content-Type", "")

    def sample(self) -> tuple[str, bytes]:
        """What the portal actually returns, for fixing this reader: the first rows of one search, exactly as received. Never includes cookies."""
        r = self._send("POST", f"{self.base}/all-bids-data", data={"payload": json.dumps({"page": 1, "param": {"searchBid": "road", "searchType": "fullText"},
                       "filter": {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "", "byEndDate": {"from": "", "to": ""}, "sort": "Bid-End-Date-Oldest"}}),
                       "csrf_bd_gem_nk": self._security_token()}, headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.search_url})
        return r.headers.get("Content-Type", "application/json"), r.content[:200_000]
