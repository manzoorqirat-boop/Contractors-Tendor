"""Reader for NIC "GePNIC" e-procurement portals (MahaTenders, Delhi, UP eTender, CPPP and others).

These portals ask for a captcha on their "Active Tenders" search, which we never try to get around. The public
"Tenders by Organisation" view lists every open tender of a department without one, so that is what is read here.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("tender_digest.gepnic")

USER_AGENT = "Mozilla/5.0 (compatible; TenderDigest/1.0; daily civil tender summary)"
DATE_FORMAT = "%d-%b-%Y %I:%M %p"
MAX_REDIRECTS = 5


def is_public_host(host: str) -> bool:
    """False when the name points at this machine or a private network. A name that does not resolve is left to fail normally."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


@dataclass
class Tender:
    portal: str
    tender_id: str
    title: str
    reference: str
    organisation: str
    published: datetime | None
    closing: datetime | None
    opening: datetime | None
    detail_path: str = ""  # session-bound portal link; only valid inside the live session that produced it
    # filled from the detail page
    description: str = ""
    category: str = ""
    sub_category: str = ""
    value: float | None = None
    location: str = ""
    emd: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.portal}:{self.tender_id}"


def parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in (DATE_FORMAT, "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_money(text: str) -> float | None:
    """'8,51,184' -> 851184.0. 'NA', '' and anything else that is not a number -> None."""
    cleaned = re.sub(r"[^\d.]", "", text or "")
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _results_table(soup: BeautifulSoup):
    return soup.find("table", id="table") or soup.find("table", class_="list_table")


def parse_organisations(html: str) -> list[tuple[str, int, str]]:
    """Returns (organisation name, open tender count, link) for every department on the portal."""
    table = _results_table(BeautifulSoup(html, "lxml"))
    out: list[tuple[str, int, str]] = []
    for row in table.find_all("tr") if table else []:
        cells = row.find_all("td")
        link = row.find("a", href=True)
        if len(cells) < 3 or not link:
            continue
        count = re.sub(r"\D", "", _text(cells[2]))
        out.append((_text(cells[1]), int(count) if count else 0, link["href"]))
    return out


_BRACKET = re.compile(r"\[([^\[\]]*)\]")


def parse_tender_list(html: str, portal: str) -> list[Tender]:
    """Parses one department's tender list. The title cell reads '[title] [reference][tender id]'."""
    table = _results_table(BeautifulSoup(html, "lxml"))
    tenders: list[Tender] = []
    for row in table.find_all("tr") if table else []:
        cells = row.find_all("td")
        if len(cells) < 6 or not parse_date(_text(cells[1])):
            continue
        brackets = _BRACKET.findall(_text(cells[4]))
        if not brackets:
            continue
        link = cells[4].find("a", href=True)
        tenders.append(
            Tender(
                portal=portal,
                tender_id=brackets[-1].strip(),
                title=brackets[0].strip(),
                reference=brackets[1].strip() if len(brackets) > 2 else "",
                organisation=_text(cells[5]),
                published=parse_date(_text(cells[1])),
                closing=parse_date(_text(cells[2])),
                opening=parse_date(_text(cells[3])),
                detail_path=link["href"] if link else "",
            )
        )
    return tenders


def parse_detail(html: str) -> dict[str, str]:
    """Label -> value pairs from a tender's detail page (first occurrence wins)."""
    soup = BeautifulSoup(html, "lxml")
    pairs: dict[str, str] = {}
    for caption in soup.find_all("td", class_=re.compile("caption")):
        value = caption.find_next_sibling("td")
        label = _text(caption)
        if value is not None and label and label not in pairs:
            pairs[label] = _text(value)
    return pairs


def parse_corrigenda(html: str) -> list[str]:
    """'Title (type)' for every corrigendum or addendum listed on a tender's detail page. Empty when there are none."""
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    for table in soup.find_all("table"):
        header = table.find("tr", class_="list_header")
        if header is None or header.find_parent("table") is not table or "corrigendum title" not in _text(header).lower():
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if row is header or row.find_parent("table") is not table or len(cells) < 3:
                continue
            title, kind = _text(cells[1]), _text(cells[2])
            if title:
                out.append(f"{title} ({kind})" if kind else title)
    return list(dict.fromkeys(out))


def parse_doc_links(html: str) -> list[dict[str, str]]:
    """The downloadable documents on a tender's detail page: [{name, href}]. The links are session-bound and expire within minutes."""
    out = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        if re.search(r"docDownoad|docDownload", a["href"], re.I):
            out.append({"name": _text(a), "href": a["href"]})
    return out


_BOQ_NAME = re.compile(r"(?<![a-z])boq(?![a-z])|b\.o\.q|bill[\s_-]*of[\s_-]*quantit|price[\s_-]*bid|financial[\s_-]*bid|schedule[\s_-]*(b\b|of[\s_-]*rates)", re.I)
_SHEET_EXT = re.compile(r"\.(xlsx|xlsm|xls|csv)\s*$", re.I)


def boq_score(link: dict[str, str]) -> int:
    """How BOQ-like a document is by its name: 3 a spreadsheet named like a BOQ, 2 any spreadsheet, 1 named like a BOQ, 0 neither."""
    name = link.get("name", "")
    sheet, named = bool(_SHEET_EXT.search(name)), bool(_BOQ_NAME.search(name))
    return 3 if sheet and named else 2 if sheet else 1 if named else 0


def pick_boq(links: list[dict[str, str]], exclude_href: str | None = None) -> dict[str, str] | None:
    """The bill of quantities among the documents: the most BOQ-like name, spreadsheets first. None when nothing looks like one."""
    best = max((x for x in links if x["href"] != exclude_href), key=boq_score, default=None)
    return best if best and boq_score(best) > 0 else None


def pick_nit(links: list[dict[str, str]]) -> dict[str, str] | None:
    """The tender notice (NIT) among the documents: named like a notice, else the first one that is not a BOQ."""
    named = next((x for x in links if re.search(r"notice|nit\b|tender.?doc", x["name"], re.I) and boq_score(x) < 2), None)
    return named or next((x for x in links if boq_score(x) == 0), None)


def apply_detail(tender: Tender, pairs: dict[str, str]) -> None:
    def pick(*labels: str) -> str:
        for label in labels:
            for key, value in pairs.items():
                if key.lower().startswith(label.lower()) and value and value.upper() != "NA":
                    return value
        return ""

    tender.description = pick("Work Description") or pick("Title")
    tender.category = pick("Product Category")
    tender.sub_category = pick("Sub category")
    tender.location = pick("Location")
    tender.value = parse_money(pick("Tender Value"))
    tender.emd = parse_money(pick("EMD Amount"))


class PortalError(RuntimeError):
    """A portal could not be read. `kind` says why, so the page can tell a captcha or login wall from an outage.

    kinds: unreachable (default, usually temporary), blocked, captcha, login, otp, different_software.
    """

    def __init__(self, message: str = "", kind: str = "unreachable"):
        super().__init__(message)
        self.kind = kind


_OTP = re.compile(r"\botp\b|one[\s-]?time[\s-]?(password|pin)|mobile\s+verification", re.I)
_CAPTCHA = re.compile(r"captcha|enter the (text|characters) (shown|in the image)", re.I)
_LOGIN = re.compile(r"type=[\"']?password|please (log ?in|sign ?in)|login (is )?required|user ?id.{0,40}password", re.I | re.S)


def classify_page(html: str) -> str:
    """Why a page that loaded fine has no department list. A guess from the page's wording, never a certainty."""
    if _OTP.search(html):
        return "otp"
    if _CAPTCHA.search(html):
        return "captcha"
    if _LOGIN.search(html):
        return "login"
    return "different_software"


def _bare_host(url: str) -> str:
    """The host of an address, lower case, without a leading www. (a portal and its www. twin are the same site)."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class GepnicClient:
    """One polite session against one portal. A portal that is down raises PortalError and is skipped for the day."""

    def __init__(self, base_url: str, delay: float = 0.6, session: requests.Session | None = None, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", USER_AGENT)
        self._last = 0.0
        self._host = _bare_host(self.base)
        self._check_dns = session is None  # a session handed in (tests) is not a real network

    @property
    def search_url(self) -> str:
        """Where a person pastes a Tender ID: tender links themselves expire with the portal session."""
        return f"{self.base}/app?page=FrontEndAdvancedSearch&service=page"

    def _request(self, url: str, stream: bool = False):
        """One GET that follows redirects itself. A portal page can contain any link, and a redirect can point anywhere, so every
        address (the first and each hop) must be https on the portal's own host and must not resolve to a private network."""
        hops: list = []
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlparse(url)
            if parts.scheme != "https" or _bare_host(url) != self._host:
                raise PortalError(f"the portal pointed us to another website ({(parts.hostname or url)[:60]}), which this app does not follow", "different_software")
            if self._check_dns and not is_public_host(parts.hostname or ""):
                raise PortalError("the portal's address points to a private network, so it was not contacted", "blocked")
            kwargs = {"timeout": self.timeout, "allow_redirects": False}
            response = self.http.get(url, stream=True, **kwargs) if stream else self.http.get(url, **kwargs)
            location = response.headers.get("Location") if getattr(response, "status_code", 0) in (301, 302, 303, 307, 308) and hasattr(response, "headers") else None
            if not location:
                if hops:
                    response.history = hops + list(getattr(response, "history", None) or [])
                return response
            hops.append(response)
            url = urljoin(url, location)
        raise PortalError("the portal redirected too many times", "unreachable")

    def _get(self, url: str, attempts: int = 3) -> str:
        last: PortalError | None = None
        for attempt in range(attempts):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._request(urljoin(self.base + "/", url.replace("&amp;", "&")))
                self._last = time.monotonic()
                status = response.status_code
                if status == 200 and "Stale Session" not in response.text[:3000]:
                    final = str(getattr(response, "url", "") or "").lower()
                    if getattr(response, "history", None) and re.search(r"log-?in|signin|sso", final):
                        raise PortalError("the portal sent us to a login page: it needs an account", "login")
                    return response.text
                if status == 401:
                    raise PortalError("the portal asks for a login (HTTP 401)", "login")
                if status == 403:
                    raise PortalError("the portal refuses this server (HTTP 403), often because it blocks data-centre addresses", "blocked")
                if status == 404:
                    raise PortalError("that page does not exist on this portal (HTTP 404): wrong address or different software", "different_software")
                last = PortalError(f"HTTP {status} from {url[:80]}", "unreachable")
            except PortalError as e:
                if e.kind != "unreachable":
                    raise  # a permanent answer: retrying only annoys the portal
                last = e
            except requests.RequestException as e:
                self._last = time.monotonic()
                last = PortalError(str(e)[:200], "unreachable")
            time.sleep(min(2 ** attempt * 2, 10) if attempt + 1 < attempts else 0)
        raise last or PortalError("no answer", "unreachable")

    def organisations(self, attempts: int = 3) -> list[tuple[str, int, str]]:
        html = self._get("app?page=FrontEndTendersByOrganisation&service=page", attempts)
        orgs = parse_organisations(html)
        if not orgs:
            kind = classify_page(html)
            why = {
                "otp": "the portal asks for an OTP login",
                "captcha": "the portal asks for a captcha",
                "login": "the portal asks for a login",
            }.get(kind, "the department list came back empty: not a portal this app can read")
            raise PortalError(why, kind)
        return orgs

    def tenders_of(self, link: str, portal: str) -> list[Tender]:
        return parse_tender_list(self._get(link), portal)

    def detail(self, tender: Tender) -> dict[str, str]:
        return parse_detail(self._get(tender.detail_path))

    def detail_full(self, tender: Tender) -> tuple[dict[str, str], list[str], list[dict[str, str]]]:
        """Detail pairs, corrigenda and document links from one fetch."""
        html = self._get(tender.detail_path)
        return parse_detail(html), parse_corrigenda(html), parse_doc_links(html)

    def download(self, href: str, max_bytes: int = 10 * 1024 * 1024) -> tuple[bytes, str]:
        """One document, politely. Returns (bytes, content type); a portal that wants a login or captcha usually answers with an HTML page instead."""
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self._request(urljoin(self.base + "/", href.replace("&amp;", "&")), stream=True)
            self._last = time.monotonic()
        except requests.RequestException as e:
            raise PortalError(str(e)[:200], "unreachable") from None
        if r.status_code != 200:
            raise PortalError(f"HTTP {r.status_code} on the document link", "blocked" if r.status_code in (401, 403) else "unreachable")
        data = r.raw.read(max_bytes + 1, decode_content=True) if hasattr(r, "raw") else r.content[: max_bytes + 1]
        if len(data) > max_bytes:
            raise PortalError("the document is over 10 MB", "unreachable")
        return data, r.headers.get("Content-Type", "")

    def detail_page(self, tender: Tender) -> tuple[dict[str, str], list[str]]:
        """One fetch of the detail page: its label/value pairs and its corrigenda. Used to watch tenders you are bidding for."""
        html = self._get(tender.detail_path)
        return parse_detail(html), parse_corrigenda(html)
