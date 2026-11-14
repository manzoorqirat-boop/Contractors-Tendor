"""Builds the email: HTML for reading, plain text as the fallback."""
from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass
from datetime import datetime
from html import escape

from . import access
from .collector import PortalReport
from .gepnic import Tender


def format_inr(value: float | None) -> str:
    """851184 -> '₹8,51,184 (8.51 lakh)'. Indian digit grouping, lakh and crore for the big ones."""
    if value is None:
        return "Not stated"
    whole = int(round(value))
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    note = ""
    if whole >= 10_000_000:
        note = f" ({whole / 10_000_000:.2f} crore)"
    elif whole >= 100_000:
        note = f" ({whole / 100_000:.2f} lakh)"
    return f"₹{digits}{note}"


def _date(d: datetime | None, with_time: bool = False) -> str:
    if not d:
        return "-"
    return d.strftime("%d %b %Y %H:%M" if with_time else "%d %b %Y")


STYLE = (
    "<style>body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111827}"
    ".c{max-width:900px;margin:0 auto;padding:8px}h1{font-size:20px;margin:0}h2{font-size:16px;margin:24px 0 6px}"
    "h3{font-size:14px;margin:14px 0 4px;color:#374151}table{border-collapse:collapse;width:100%;font-size:13px}"
    "th{text-align:left;color:#6b7280;font-size:12px;padding:6px 8px}td{padding:8px;border-bottom:1px solid #e5e7eb;vertical-align:top}"
    ".n{white-space:nowrap}.o{color:#6b7280;font-size:12px;margin-top:3px}.m{font-size:12px;margin-top:3px}a{color:#2563eb}"
    ".i{background:#eef4ff;border:1px solid #bfdbfe;border-radius:6px;padding:10px 12px;margin:16px 0;font-size:13px;line-height:1.5}.w{background:#fef3c7;border:1px solid #fcd34d;border-radius:6px;padding:10px 12px;margin:16px 0;font-size:13px}</style>"
)


def _headline(t: Tender) -> str:
    """The description is the useful text; many titles are only an estimate number."""
    text = t.description or t.title
    return text if len(text) <= 260 else text[:257] + "..."


@dataclass
class Digest:
    subject: str
    html: str
    text: str
    new_count: int
    closing_count: int
    csv_base64: str = ""  # the full list, for Excel
    csv_name: str = "tenders.csv"


CSV_COLUMNS = ["Status", "Portal", "Tender ID", "Work", "Department", "Category", "Location", "Estimated value (INR)", "EMD (INR)", "Published", "Closes"]


def safe_cell(value):
    """Spreadsheet programs run cells that start with = + - @ as formulas. Tender text comes from outside: defuse it."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def build_csv(rows: list[tuple[str, Tender, str]]) -> str:
    """rows are (status, tender, portal name). utf-8 with a BOM so Excel opens the rupee sign and Hindi/Marathi text correctly."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for status, t, portal in rows:
        writer.writerow([safe_cell(c) for c in [
            status, portal, t.tender_id, t.description or t.title, t.organisation.replace("||", " > "), t.category, t.location,
            "" if t.value is None else int(t.value), "" if t.emd is None else int(t.emd),
            _date(t.published, True), _date(t.closing, True),
        ]])
    return base64.b64encode(("\ufeff" + out.getvalue()).encode("utf-8")).decode("ascii")


def _top(tenders: list[Tender], limit: int) -> tuple[list[Tender], int]:
    """Biggest estimated value first, unknown values last, then soonest closing."""
    ordered = sorted(tenders, key=lambda x: (-(x.value or 0), x.closing or datetime.max))
    return ordered[:limit], max(0, len(ordered) - limit)


def build(
    now: datetime,
    new: list[Tender],
    closing_soon: list[Tender],
    reports: list[PortalReport],
    prefix: str = "Civil tenders",
    max_new_per_portal: int = 25,
    max_soon_per_portal: int = 10,
    intro: str = "",
) -> Digest:
    by_portal = {r.portal.key: r for r in reports}
    new_keys = {t.key for t in new}
    soon = [t for t in closing_soon if t.key not in new_keys]  # new ones show their closing date already

    problems = [r for r in reports if r.errors]
    subject = f"{prefix}, {now.strftime('%d %b %Y')}: {len(new)} new, {len(soon)} closing soon"
    if any(r.failed for r in reports):
        subject += " (some portals unavailable)"

    def rows(tenders: list[Tender]) -> str:
        out = []
        for t in tenders:
            search = by_portal[t.portal].search_url if t.portal in by_portal else ""
            meta = "".join(
                [
                    f"Tender ID <b>{escape(t.tender_id)}</b>",
                    f" · <a href='{escape(search)}'>find on portal</a>" if search else "",
                    f" · {escape(t.category)}" if t.category else "",
                    f" · {escape(t.location)}" if t.location else "",
                ]
            )
            out.append(
                f"<tr><td><b>{escape(_headline(t))}</b><div class=o>{escape(t.organisation.replace('||', ' › '))}</div>"
                f"<div class=m>{meta}</div></td><td class=n>{escape(format_inr(t.value))}</td><td class=n>{_date(t.closing, True)}</td></tr>"
            )
        return "".join(out)

    def section(title: str, tenders: list[Tender], empty: str, limit: int) -> str:
        if not tenders:
            return f"<h2>{escape(title)}</h2><p class=o>{escape(empty)}</p>"
        groups = ""
        for key in dict.fromkeys(t.portal for t in sorted(tenders, key=lambda x: x.portal)):
            portal_name = by_portal[key].portal.name if key in by_portal else key
            shown, more = _top([t for t in tenders if t.portal == key], limit)
            groups += (
                f"<h3>{escape(portal_name)} ({len(shown) + more})</h3>"
                "<table><tr><th>Work</th><th>Estimated value</th><th>Closes</th></tr>"
                f"{rows(shown)}</table>"
                + (f"<p class=o>and {more} more, in the attached spreadsheet.</p>" if more else "")
            )
        return f"<h2>{escape(title)} ({len(tenders)})</h2>{groups}"

    notes = ""
    if problems:
        lines = "".join(
            f"<li><b>{escape(r.portal.name)}</b>: {escape(r.errors[0])}"
            + (f" (and {len(r.errors) - 1} more)" if len(r.errors) > 1 else "")
            + "</li>"
            for r in problems
        )
        notes = (
            "<div class=w>"
            f"Some data could not be read today, so this list may be incomplete:<ul style='margin:6px 0 0 18px'>{lines}</ul></div>"
        )

    manual = [r for r in reports if r.access in access.NEEDS_MANUAL and r.access != "unreachable"]
    if manual:
        items = "".join(f"<li><b>{escape(r.portal.name)}</b>: {escape(access.describe(r.access)['label'])}</li>" for r in manual)
        notes += (
            "<div class=w>Check these portals by hand. The app cannot read them on its own, so nothing from them is in this list:"
            f"<ul style='margin:6px 0 0 18px'>{items}</ul></div>"
        )

    html = (
        f"<html><head><meta charset='utf-8'>{STYLE}</head><body><div class=c>"
        "<div class=o style='font-weight:600'>TendAI by QMSofts</div>"
        f"<h1>{escape(prefix)}</h1>"
        f"<div class=o>{now.strftime('%A, %d %B %Y')}</div>"
        f"{notes}"
        + (f"<div class=i><b>Today's highlights</b><br>{escape(intro)}</div>" if intro else "")
        + f"{section('New since the last digest', new, 'No new civil tenders.', max_new_per_portal)}"
        f"{section('Closing in the next few days', soon, 'Nothing else closes soon.', max_soon_per_portal)}"
        "<p class=o style='margin-top:28px'>Read from the public tender lists of the portals named above. "
        "Always confirm details, eligibility and dates on the portal itself before bidding. "
        "Tender links on these portals expire after a few minutes, so search by Tender ID.<br>Sent by TendAI by QMSofts.</p></div></body></html>"
    )

    text_lines = ["TendAI by QMSofts", f"{prefix} - {now.strftime('%d %b %Y')}", ""]
    if intro:
        text_lines += ["TODAY'S HIGHLIGHTS", intro, ""]
    for title, tenders in (("NEW", new), ("CLOSING SOON", soon)):
        text_lines.append(f"{title} ({len(tenders)})")
        for t in sorted(tenders, key=lambda x: (x.portal, x.closing or datetime.max)):
            text_lines += [
                f"- {_headline(t)}",
                f"  {t.organisation.replace('||', ' > ')}",
                f"  ID {t.tender_id} | {format_inr(t.value)} | closes {_date(t.closing, True)}",
            ]
        text_lines.append("")
    for r in problems:
        text_lines.append(f"Note: {r.portal.name}: {r.errors[0]}")
    for r in manual:
        text_lines.append(f"CHECK BY HAND: {r.portal.name}: {access.describe(r.access)['label']}")
    names = {k: r.portal.name for k, r in by_portal.items()}
    csv_rows = [("New", t, names.get(t.portal, t.portal)) for t in sorted(new, key=lambda x: (x.portal, -(x.value or 0)))]
    csv_rows += [("Closing soon", t, names.get(t.portal, t.portal)) for t in sorted(soon, key=lambda x: (x.portal, x.closing or datetime.max))]
    return Digest(subject, html, "\n".join(text_lines), len(new), len(soon),
                  csv_base64=build_csv(csv_rows) if csv_rows else "", csv_name=f"tenders-{now.strftime('%Y-%m-%d')}.csv")
