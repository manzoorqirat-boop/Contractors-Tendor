"""Bring a tender's BOQ and its risks in from the portal (Layer 1 of the blueprint, for tenders you are bidding on).

Only what the portal gives without a login. A response that is a login, OTP or captcha page is reported as such and never worked
around. Each step returns {"state", "message", ...} so the page can say exactly what happened.

States for the BOQ:  read | kept | no_boq | login | failed | unreadable | unsupported
States for risks:    read | kept | no_notice | ai_off | ai_failed
"""
from __future__ import annotations

import base64
import re
from typing import Any

from .. import ai
from ..gepnic import PortalError, pick_boq
from .service import BoqError, BoqService

LOGIN_MESSAGE = ("The portal asks for a login or captcha before it gives documents, which this app never gets past. "
                 "Download the file yourself and upload it here.")
_TABLE = re.compile(rb"<table|<ss:table|<worksheet", re.I)


def sniff(data: bytes) -> str:
    """What a downloaded file really is, whatever the portal called it: xlsx, xls, pdf, html (a table or a page), csv or other."""
    if data.startswith(b"PK\x03\x04"):
        return "xlsx"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if data.startswith(b"%PDF"):
        return "pdf"
    head = data[:4096].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<?xml", b"<table", b"<head", b"<body")) or b"<html" in head:
        return "html"
    return "csv" if b"," in head or b"\t" in head else "other"


def _filename(name: str, kind: str) -> str:
    stem = re.sub(r"\.(xlsx|xlsm|xls|csv)\s*$", "", name.strip(), flags=re.I)
    stem = re.sub(r"[^\w .-]+", "_", stem)[:80].strip() or "BOQ"
    return stem + {"xlsx": ".xlsx", "xls": ".xls", "html": ".xls", "csv": ".csv"}.get(kind, ".csv")


def fetch_boq(client: Any, service: BoqService, tender_key: str, links: list[dict[str, str]], exclude_href: str | None = None) -> dict[str, Any]:
    """Download the tender's BOQ file and load it. A BOQ already loaded for this tender is left alone: re-reading would drop your choices."""
    if service.store.latest_boq_id(tender_key):
        return {"state": "kept", "message": "This bid already has a BOQ loaded. Upload a file in the BOQ card if you want to replace it.", "lines": None}
    link = pick_boq(links, exclude_href)
    if not link:
        return {"state": "no_boq", "message": "The portal lists no BOQ file for this tender. It is often inside the tender document: upload it in the BOQ card.", "lines": None}
    if not hasattr(client, "download"):
        return {"state": "unsupported", "message": "This portal cannot give documents. Upload the BOQ yourself.", "lines": None}
    try:
        data, _ctype = client.download(link["href"])
    except PortalError as e:
        return {"state": "failed", "message": f"The portal would not give the BOQ: {e}", "lines": None}
    kind = sniff(data)
    if kind == "html" and not _TABLE.search(data[:200_000]):
        return {"state": "login", "message": LOGIN_MESSAGE, "lines": None}
    if kind in ("pdf", "other"):
        return {"state": "unreadable", "message": f"'{link['name']}' is a {'PDF' if kind == 'pdf' else 'file this app cannot read'}, not a spreadsheet. "
                                                  "Upload the BOQ as .xlsx or .csv.", "lines": None}
    try:
        view = service.analyse(tender_key, _filename(link["name"], kind), base64.b64encode(data).decode())
    except BoqError as e:
        return {"state": "unreadable", "message": f"Downloaded '{link['name']}' but could not read it as a BOQ: {e}", "lines": None}
    boq = view["boq"] or {}
    counts = boq.get("counts", {})
    return {"state": "read", "lines": len(boq.get("lines", [])), "counts": counts,
            "message": f"Read the BOQ '{link['name']}': {len(boq.get('lines', []))} items, {counts.get('AUTO_MATCHED', 0)} matched, "
                       f"{counts.get('NEEDS_REVIEW', 0)} to check, {counts.get('NEW_NON_SOR_ITEM', 0)} not in your catalogue."}


def fetch_risks(service: BoqService, tender_key: str, tender: dict, notice_pdf: bytes | None) -> dict[str, Any]:
    """Have Claude read the tender notice for the 12 risk drivers, once. Findings already saved are kept."""
    if service.store.get_risk(tender_key):
        return {"state": "kept", "message": "Risks were already read for this bid. Use the Risk and margin card to read again."}
    if not notice_pdf:
        return {"state": "no_notice", "message": "No tender notice was available to read for risks."}
    if not ai.configured():
        return {"state": "ai_off", "message": "The AI assistant is off, so the notice was not read for risks."}
    try:
        service.read_risks(tender_key, tender, pdf_base64=base64.b64encode(notice_pdf).decode())
    except (ai.AIError, ai.NotConfigured) as e:
        return {"state": "ai_failed", "message": str(e)}
    return {"state": "read", "message": "Read the notice for risks. See Risk and margin."}
