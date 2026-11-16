"""The bid pack: one zip with the papers a bid needs, in checklist order, and an index saying what is in it and what is not.

Each document line on the checklist is matched to a paper in the Vault (the one chosen for it, or the best match by kind). The zip holds those
files numbered in checklist order, the tender notice if the app kept it, and the drafts written for the bid. The index lists every checklist
line, so a missing or expiring paper is visible before the zip is uploaded to a portal.
"""
from __future__ import annotations

import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

STATUS_TEXT = {
    "ok": "OK",
    "expires_before_close": "CHECK: expires before the bid closes",
    "expired": "CHECK: expired",
    "missing": "MISSING: nothing in the Vault for this",
    "unknown": "No paper chosen",
}


def safe_name(text: str, limit: int = 60) -> str:
    return re.sub(r"[^\w.\- ()]+", "_", re.sub(r"\s+", " ", text)).strip(" ._")[:limit] or "document"


def build(bid: dict[str, Any], items: list[dict[str, Any]], book: Any, now: datetime) -> tuple[Path, dict[str, Any]]:
    """Writes the zip to a temporary file (the caller deletes it) and returns (path, summary of what went in)."""
    out = Path(tempfile.mkstemp(prefix="tendai-pack-", suffix=".zip")[1])
    included, problems, lines = 0, 0, []
    used_docs: dict[int, str] = {}
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            n = 0
            for it in items:
                m = it.get("match")
                label = f"[{'x' if it['done'] else ' '}] {it['text']}"
                if it["kind"] != "document":
                    lines.append(f"{label}  (task, nothing to attach)")
                    continue
                status = (m or {}).get("status", "unknown")
                note = STATUS_TEXT.get(status, status)
                if status in ("missing", "unknown") or not m or not m.get("vault_id"):
                    problems += 1
                    lines.append(f"{label}\n      -> {note}")
                    continue
                found = book.vault_bytes(m["vault_id"])
                if found is None:
                    problems += 1
                    lines.append(f"{label}\n      -> '{m['name']}' is in the Vault without a file attached, so it is not in the pack")
                    continue
                content, shown = found
                if m["vault_id"] in used_docs:  # the same paper answers two lines: one copy in the zip
                    lines.append(f"{label}\n      -> {used_docs[m['vault_id']]} (already in the pack)")
                    continue
                n += 1
                arc = f"{n:02d}-{safe_name(it['text'])}{Path(shown).suffix.lower()}"
                z.writestr(arc, content)
                used_docs[m["vault_id"]] = arc
                included += 1
                if status != "ok":
                    problems += 1
                exp = f", expires {m['expiry']}" if m.get("expiry") else ""
                lines.append(f"{label}\n      -> {arc}  ({m['name']}{exp}; {note})")
            source = book.source_bytes(bid["key"])
            if source:
                z.writestr(f"tender-notice{source[0]}", source[1])
            for kind, text in sorted((bid.get("drafts") or {}).items()):
                if isinstance(text, str) and text.strip():
                    z.writestr(f"drafts/{safe_name(kind)}.txt", text)
            head = [f"BID PACK: {bid.get('title') or bid.get('description') or ''}".strip(), f"Tender ID: {bid.get('tender_id') or '-'}",
                    f"Department: {bid.get('organisation') or '-'}", f"Closes: {bid.get('closing') or '-'}", f"Pack made: {now.strftime('%d %b %Y %H:%M')}", "",
                    f"{included} paper(s) in the pack, {problems} line(s) need attention.", "Every line below is a checklist line; the file is the paper the Vault holds for it.",
                    "Check each file against the tender before you upload anything.", ""]
            z.writestr("00-index.txt", "\n".join(head + lines) + "\n")
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return out, {"files": included, "needs_attention": problems, "lines": len(lines)}
