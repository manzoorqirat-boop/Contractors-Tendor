"""Reader for NIC "GePNIC" e-procurement portals (MahaTenders, Delhi, UP eTender, CPPP and others).

These portals ask for a captcha on their "Active Tenders" search, which we never try to get around. The public
"Tenders by Organisation" view lists every open tender of a department without one, so that is what is read here.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("tender_digest.gepnic")

USER_AGENT = "Mozilla/5.0 (compatible; TenderDigest/1.0; daily civil tender summary)"
DATE_FORMAT = "%d-%b-%Y %I:%M %p"


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
    pass


class GepnicClient:
    """One polite session against one portal. A portal that is down raises PortalError and is skipped for the day."""

    def __init__(self, base_url: str, delay: float = 0.6, session: requests.Session | None = None, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", USER_AGENT)
        self._last = 0.0

    @property
    def search_url(self) -> str:
        """Where a person pastes a Tender ID: tender links themselves expire with the portal session."""
        return f"{self.base}/app?page=FrontEndAdvancedSearch&service=page"

    def _get(self, url: str, attempts: int = 3) -> str:
        last: Exception | None = None
        for attempt in range(attempts):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.http.get(urljoin(self.base + "/", url.replace("&amp;", "&")), timeout=self.timeout)
                self._last = time.monotonic()
                if response.status_code == 200 and "Stale Session" not in response.text[:3000]:
                    return response.text
                last = PortalError(f"HTTP {response.status_code} from {url[:80]}")
            except requests.RequestException as e:
                self._last = time.monotonic()
                last = e
            time.sleep(min(2 ** attempt * 2, 10))
        raise PortalError(str(last))

    def organisations(self) -> list[tuple[str, int, str]]:
        orgs = parse_organisations(self._get("app?page=FrontEndTendersByOrganisation&service=page"))
        if not orgs:
            raise PortalError("the department list came back empty")
        return orgs

    def tenders_of(self, link: str, portal: str) -> list[Tender]:
        return parse_tender_list(self._get(link), portal)

    def detail(self, tender: Tender) -> dict[str, str]:
        return parse_detail(self._get(tender.detail_path))
