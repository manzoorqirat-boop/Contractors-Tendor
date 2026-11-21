"""Reading a portal whose page has changed, without anyone editing code.

The portal readers expect each page laid out as it was when they were written: a table with a known id, tenders in columns 1 to 5. Portals are redesigned without notice, and then
the reader finds nothing and the portal looks quiet. Most such changes are small (a column added or moved, a different date format, a renamed table) and the data is all still there.

This module is the fallback the readers use when the exact layout finds nothing: it works out what each column IS from what it contains, not where it sits.

  * A date column is one whose cells parse as dates; of the date columns, the one whose dates are earliest is the publish date, then closing, then opening (a header such as
    "Closing Date" decides first when there is one).
  * The title column is the one whose cells carry the bracketed `[title] [reference] [tender id]` text (or a tender-ID-looking code), the organisation column the longest remaining text.
  * A department list is a table of rows that each carry a link and a number.

It never guesses a tender into existence: a row needs real dates and a tender ID, and the result is accepted only when rows pass those tests. When it works, the run says the layout
changed and the portal keeps being read. When it cannot, `diagnose` shows a person what the page contains, so the reader can be corrected from a sample.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

from .gepnic import Tender

_DATE_FORMATS = ("%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M %p", "%d/%m/%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y %I:%M %p", "%d-%m-%Y",
                 "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d %b %Y %H:%M", "%d %b %Y %I:%M %p", "%d %b %Y", "%d %B %Y %H:%M", "%d %B %Y %I:%M %p", "%d %B %Y",
                 "%d-%B-%Y %I:%M %p", "%d-%B-%Y")
_DATE_FIND = re.compile(r"\d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ]\d{2,4}(?:[ ,T]+\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)?|\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?")
_BRACKET = re.compile(r"\[([^\[\]]*)\]")
_ID = re.compile(r"\b20\d{2}_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b")
MIN_ROWS = 1
HEADER_WORDS = {"published": ("publish",), "closing": ("closing", "submission", "last date", "close"), "opening": ("opening", "open date", "bid open")}


def any_date(text: str) -> datetime | None:
    """A date in any of the usual Indian portal formats, found anywhere in the text. None when there is none."""
    found = _DATE_FIND.search(text or "")
    if not found:
        return None
    raw = re.sub(r"\s+", " ", found.group(0).replace(",", " ").replace("T", " ")).strip()
    raw = re.sub(r"\s?([APap][Mm])$", lambda m: " " + m.group(1).upper(), raw)
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if 1990 <= d.year <= 2100:
            return d
    return None


def _clean(node: Any) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _own_rows(table: Tag) -> list[list[Tag]]:
    """The rows that belong to this table (not to a table nested inside it), each as its cells."""
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cells = [c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]
        if cells:
            rows.append(cells)
    return rows


def _describe(table: Tag) -> dict[str, Any]:
    rows = _own_rows(table)
    return {"id": table.get("id") or "", "class": " ".join(table.get("class") or []), "rows": len(rows), "columns": max((len(r) for r in rows), default=0)}


def _tender_table(soup: BeautifulSoup) -> tuple[Tag | None, list[list[Tag]], list[Tag] | None]:
    """The table whose rows look most like tenders (two or more dates in a row), its data rows, and its header row when it has one."""
    best: tuple[int, Tag, list[list[Tag]], list[Tag] | None] | None = None
    for table in soup.find_all("table"):
        rows = _own_rows(table)
        data = [r for r in rows if sum(1 for c in r if any_date(_clean(c))) >= 2]
        if len(data) < MIN_ROWS:
            continue
        width = statistics.mode(len(r) for r in data)
        data = [r for r in data if len(r) == width]
        first = rows.index(data[0])
        header = rows[first - 1] if first > 0 and len(rows[first - 1]) == width and not any(any_date(_clean(c)) for c in rows[first - 1]) else None
        if best is None or len(data) > best[0]:
            best = (len(data), table, data, header)
    return (best[1], best[2], best[3]) if best else (None, [], None)


def _columns(data: list[list[Tag]], header: list[Tag] | None) -> dict[str, Any]:
    """What each column is, from its contents (and its header words where they are clear): {published, closing, opening, title, org} as column indexes, and how it was decided."""
    width = len(data[0])
    texts = [[_clean(c) for c in row] for row in data]
    date_cols = [j for j in range(width) if sum(1 for r in texts if any_date(r[j])) >= 0.7 * len(texts)]
    roles: dict[str, int] = {}
    how = "content"
    if header:
        heads = [_clean(c).lower() for c in header]
        by_header = {}
        for role, words in HEADER_WORDS.items():
            hits = [j for j in date_cols if any(w in heads[j] for w in words)]
            if len(hits) == 1:
                by_header[role] = hits[0]
        if len(set(by_header.values())) == len(by_header) and len(by_header) == len(date_cols) and by_header:
            roles, how = by_header, "header words"
    if not roles and date_cols:
        order = sorted(date_cols, key=lambda j: statistics.median(d.timestamp() for r in texts if (d := any_date(r[j]))))
        names = ["published", "closing", "opening"] if len(order) >= 3 else ["published", "closing"]
        roles = dict(zip(names, order[:len(names)], strict=False))

    def bracket_share(j: int) -> float:
        return sum(1 for r in texts if _BRACKET.search(r[j]) or _ID.search(r[j])) / len(texts)

    others = [j for j in range(width) if j not in roles.values()]
    title = max(others, key=lambda j: (bracket_share(j), sum(len(r[j]) for r in texts)), default=None)
    if title is not None and bracket_share(title) == 0:
        linked = [j for j in others if any(data[i][j].find("a", href=True) for i in range(len(data)))]
        title = max(linked or others, key=lambda j: sum(len(r[j]) for r in texts), default=None)
    rest = [j for j in others if j != title and statistics.mean(len(r[j]) for r in texts) > 3 and not all(re.fullmatch(r"[\d.,\s]*", r[j]) for r in texts)]
    org = max(rest, key=lambda j: statistics.mean(len(r[j]) for r in texts), default=None)
    return {**roles, "title": title, "org": org, "how": how}


def dates_sane(tenders: list[Tender]) -> bool:
    """Do the dates make sense? A tender closes after it was published. When most rows say the opposite, the columns are not what the reader thinks (two were swapped):
    the exact layout still "works", so only this check notices that the data it reads is wrong."""
    pairs = [(t.published, t.closing) for t in tenders if t.published and t.closing]
    if len(pairs) < 3:
        return True
    in_order = [1 for published, closing in pairs if published and closing and closing >= published]
    return len(in_order) / len(pairs) >= 0.8


def adaptive_tender_list(html: str, portal: str) -> tuple[list[Tender], dict[str, Any]]:
    """The tenders on a department's list found by what the columns contain. Returns ([], {}) when nothing there passes the tests."""
    soup = BeautifulSoup(html or "", "lxml")
    table, data, header = _tender_table(soup)
    if table is None:
        return [], {}
    cols = _columns(data, header)
    if cols.get("title") is None or "published" not in cols or "closing" not in cols:
        return [], {}
    tenders: list[Tender] = []
    for row in data:
        cell = row[cols["title"]]
        text = _clean(cell)
        brackets = _BRACKET.findall(text)
        if brackets:
            tender_id, title, reference = brackets[-1].strip(), brackets[0].strip(), (brackets[1].strip() if len(brackets) > 2 else "")
        else:
            m = _ID.search(text)
            if not m:
                continue
            tender_id, title, reference = m.group(0), text.replace(m.group(0), "").strip(" -:|"), ""
        if not tender_id:
            continue
        link = cell.find("a", href=True) or next((r.find("a", href=True) for r in row if r.find("a", href=True)), None)
        tenders.append(Tender(
            portal=portal, tender_id=tender_id, title=title, reference=reference, organisation=_clean(row[cols["org"]]) if cols.get("org") is not None else "",
            published=any_date(_clean(row[cols["published"]])), closing=any_date(_clean(row[cols["closing"]])), opening=any_date(_clean(row[cols["opening"]])) if "opening" in cols else None,
            detail_path=str(link["href"]) if link else ""))
    if not tenders:
        return [], {}
    info = {"table": _describe(table), "columns": {k: v for k, v in cols.items() if k != "how" and v is not None}, "how": cols["how"], "rows": len(data), "read": len(tenders)}
    return tenders, info


def adaptive_organisations(html: str) -> tuple[list[tuple[str, int, str]], dict[str, Any]]:
    """The department list found as rows that each carry a link and a number, the name being the longest text in the row."""
    soup = BeautifulSoup(html or "", "lxml")
    best: tuple[int, Tag, list[tuple[str, int, str]]] | None = None
    for table in soup.find_all("table"):
        out: list[tuple[str, int, str]] = []
        for cells in _own_rows(table):
            link = next((c.find("a", href=True) for c in cells if c.find("a", href=True)), None)
            if link is None:
                continue
            texts = [_clean(c) for c in cells]
            numbers = [t for t in texts if re.fullmatch(r"\d[\d,]*", t)]
            names = [t for t in texts if t and not re.fullmatch(r"[\d,\s]*", t)]
            if not names or not numbers:
                continue
            count = int(re.sub(r"\D", "", numbers[-1]))
            out.append((max(names, key=len), count, str(link["href"])))
        if out and (best is None or len(out) > best[0]):
            best = (len(out), table, out)
    if not best:
        return [], {}
    return best[2], {"table": _describe(best[1]), "rows": len(best[2])}


def _verdict(strict_orgs: int, adaptive_orgs: int, strict_t: int, adaptive_t: int, tables: list[dict[str, Any]], info: dict[str, Any]) -> str:
    if strict_t > 0:
        return "The reader works on this page as designed."
    if adaptive_t > 0:
        cols = ", ".join(f"{k} = column {v + 1}" for k, v in info.get("columns", {}).items())
        return (f"The portal's page layout has changed, but the app can still read it by working out what each column contains ({cols}). It found {adaptive_t} tenders. "
                "Daily runs read it this way automatically; a fixed reader is still best, so download a sample (Settings > Portals) if you want it corrected.")
    if strict_orgs == 0 and adaptive_orgs == 0:
        return "The department list could not be found on the page. Open the portal in a browser: a captcha, a login or a notice may now be in the way, or it is not this kind of portal."
    seen = "; ".join(f"table {t['id'] or t['class'] or '(unnamed)'} with {t['rows']} rows and up to {t['columns']} columns" for t in tables[:4]) or "no tables"
    return f"The page has no table of tenders the reader recognises ({seen}). Download a sample (Settings > Portals) so the reader can be corrected from it."


def diagnose(orgs_html: str, list_html: str, strict_orgs: int, strict_tenders: int) -> dict[str, Any]:
    """A report for a person: what the reader sees on a department page and on a tender list, whether the exact layout or the fallback can read them, and a verdict in words."""
    adaptive_orgs, org_info = adaptive_organisations(orgs_html)
    tenders, info = adaptive_tender_list(list_html, "diagnose") if list_html else ([], {})
    tables = sorted((_describe(t) for t in BeautifulSoup(list_html or orgs_html or "", "lxml").find_all("table")), key=lambda d: -d["rows"])[:6]
    sample = [{"tender_id": t.tender_id, "title": t.title[:100], "closing": t.closing.isoformat() if t.closing else None, "organisation": t.organisation[:80]} for t in tenders[:3]]
    return {"departments": {"exact_layout": strict_orgs, "by_content": len(adaptive_orgs)}, "tenders": {"exact_layout": strict_tenders, "by_content": len(tenders), "sample": sample},
            "layout": info, "tables": tables, "verdict": _verdict(strict_orgs, len(adaptive_orgs), strict_tenders, len(tenders), tables, info)}
