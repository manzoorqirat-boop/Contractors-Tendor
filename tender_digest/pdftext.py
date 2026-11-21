"""The text of a PDF, read locally. Used to check what the AI says about a tender document against the document's own words.

A scanned PDF has no text layer, so it gives "" and the caller knows nothing can be checked. Reading is bounded (pages, characters) so a huge file cannot tie
up the server, and it never raises: a damaged file is simply "no text".
"""
from __future__ import annotations

import base64
import binascii
import io
import logging

log = logging.getLogger("tender_digest")

MAX_PAGES = 120
MAX_CHARS = 400_000


def extract(pdf: bytes | str, max_pages: int = MAX_PAGES) -> str:
    """The text layer of a PDF given as bytes or base64, or "" when there is none (a scan), it is damaged, or pdfplumber is missing."""
    try:
        data = base64.b64decode(pdf, validate=True) if isinstance(pdf, str) else pdf
    except (binascii.Error, ValueError):
        return ""
    if not data.startswith(b"%PDF"):
        return ""
    try:
        import pdfplumber
    except ImportError:
        return ""
    out: list[str] = []
    size = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as doc:
            for page in doc.pages[:max_pages]:
                text = page.extract_text() or ""
                out.append(text)
                size += len(text)
                if size >= MAX_CHARS:
                    break
    except Exception:  # a damaged or odd PDF must never break the request that asked
        log.info("could not read the text of a PDF")
        return ""
    return "\n".join(out)[:MAX_CHARS]
