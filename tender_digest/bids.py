"""Bid workspace: the pipeline of tenders you are bidding for, each one's checklist, a vault of reusable company papers,
a watch for changes (new corrigenda, moved dates), deadline alerts and a record of results.

This never submits anything to a portal. Submission needs your DSC and the portal's own login, which the app does not touch.
Everything lives in the same SQLite file as the tenders, in tables of its own, so the 120-day clean-up of old tenders never deletes a bid:
each bid keeps its own copy of the tender's facts.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .gepnic import parse_date
from .store import SeenStore, _from_json, _iso, _to_json

log = logging.getLogger("tender_digest.bids")

STAGES = ["interested", "preparing", "submitted", "opened", "won", "lost", "dropped"]
STAGE_LABELS = {"interested": "Interested", "preparing": "Preparing", "submitted": "Submitted", "opened": "Bid opened", "won": "Won", "lost": "Lost", "dropped": "Dropped"}
ACTIVE = ("interested", "preparing", "submitted", "opened")
PREPARING = ("interested", "preparing")
WATCHED = ("interested", "preparing", "submitted")  # corrigenda still matter until the bids are opened
CLOSED = ("won", "lost", "dropped")

MAX_ITEMS = 150
MAX_VAULT_DOCS = 300
MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".zip"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")

# What each kind of paper is called, and the words in a tender's "documents to submit" list that point to it.
VAULT_KINDS: dict[str, tuple[str, str]] = {
    "gst": ("GST registration", r"\bgst(in)?\b|goods and services tax"),
    "pan": ("PAN card", r"\bpan\b|permanent account"),
    "labour": ("Labour / EPF / ESI", r"labou?r|\bepf\b|\besic?\b|provident fund|\bpf\b|workmen"),
    "itr": ("Income tax returns", r"income tax|\bitr\b"),
    "networth": ("Net worth certificate", r"net\s*worth"),
    "solvency": ("Solvency certificate", r"solvency"),
    "turnover": ("Turnover / balance sheets", r"turnover|balance sheet|financial statement|audited|annual report|profit and loss|p\s*&\s*l|chartered accountant|\bca\b certificate"),
    "emd": ("EMD / bid security proof", r"\bemd\b|earnest|bid security|bank guarantee|demand draft|tender fee|processing fee"),
    "affidavit": ("Affidavit / undertaking", r"affidavit|undertaking|declaration|notari[sz]ed|blacklist|stamp paper"),
    "bank": ("Bank details", r"bank (account|details|certificate|statement)|cancelled cheque|cancelled check|\bifsc\b"),
    "poa": ("Power of attorney / authorisation", r"power of attorney|authori[sz]ation|authority letter|\bpoa\b"),
    "dsc": ("Digital signature", r"digital signature|\bdsc\b"),
    "machinery": ("Machinery / equipment", r"machiner|equipment|\bplant\b"),
    "staff": ("Technical staff", r"technical (staff|personnel|capab)|key personnel|engineer|site staff"),
    "experience": ("Past work / experience", r"experience|similar work|work order|completion certificate|completed works?|performance certificate|executed"),
    "registration": ("Contractor registration / licence", r"registration|enlist|contractor'?s? (class|licen)|class\s*[a-e]\b|licen[cs]e|incorporation|partnership deed"),
    "other": ("Other", r"$^"),
}
_KIND_RE = [(k, re.compile(rx, re.I)) for k, (_, rx) in VAULT_KINDS.items() if k != "other"]

STANDARD_TASKS = [
    "Download the tender document and BOQ from the portal",
    "Confirm your DSC is valid and registered on the portal",
    "Read the conditions and list any queries",
    "Send pre-bid queries, if any",
    "Arrange the EMD / bid security and keep the proof",
    "Fill the price bid (BOQ rates) and cross-check the totals",
    "Collect and scan every required document",
    "Upload everything, check the acknowledgement and save a copy",
]


def infer_kind(text: str) -> str:
    """Which kind of vault paper a checklist line is asking for ('other' when it is none of them)."""
    for kind, rx in _KIND_RE:
        if rx.search(text or ""):
            return kind
    return "other"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _day(value: str | None) -> str:
    return (value or "")[:10]


def _when(value: str | None, end_of_day: bool = True) -> datetime | None:
    """A stored date or date-time as a naive datetime. A bare date means the end of that day."""
    if not value or not _DATE.match(value):
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        return None
    return d.replace(hour=23, minute=59) if end_of_day and len(value) == 10 else d


def l1_outcomes_from(rows: list[dict], work_type: str | None, value: float | None) -> tuple[list[float], str]:
    """How the lowest bidder priced tenders you have already seen: each as % above (+) or below (-) the tender's estimate.

    A won bid counts at your own price when no L1 price was recorded. Narrows to the same work type, then the same size band,
    when that leaves at least 5 results; otherwise uses every decided bid.
    """
    pts: list[tuple[dict, float]] = []
    for r in rows:
        est = r.get("value")
        low = r.get("l1_value") or (r.get("my_value") if r.get("stage") == "won" else None)
        if r.get("stage") in ("won", "lost") and est and est > 0 and low and low > 0:
            pts.append((r, (low / est - 1) * 100))
    same_type = [p for r, p in pts if work_type and r.get("work_type") == work_type]
    if len(same_type) >= 5:
        return same_type, f"{work_type} tenders"
    same_size = [p for r, p in pts if value and _band(r.get("value")) == _band(value)]
    if len(same_size) >= 5:
        return same_size, f"tenders of size {_band(value)}"
    return [p for _, p in pts], "all your decided bids" if pts else ""


def _band(v: float | None) -> str:
    if v is None:
        return "Not stated"
    return "Under 1 lakh" if v < 1e5 else "1 to 10 lakh" if v < 1e6 else "10 lakh to 1 crore" if v < 1e7 else "1 to 10 crore" if v < 1e8 else "Over 10 crore"


def _dept(org: str | None) -> str:
    return ((org or "").split("||")[0].strip()) or "Not stated"


# Fields on a tender's public detail page that matter when filing, in the order shown. Matched on the start of the portal's label.
FACT_LABELS = ["Tender Fee in", "Processing Fee in", "EMD Amount", "EMD Fee Type", "EMD Percentage", "Tender Fee Exemption", "EMD Exemption", "Tenderer Class", "Form Of Contract", "No. of Covers",
               "Bid Validity", "Period Of Work", "NDA/Pre Qualification", "Document Download / Sale End Date", "Bid Submission End Date", "Bid Opening Date", "Clarification End Date",
               "Pre Bid Meeting Date", "Pre Bid Meeting Place", "Payment Mode"]


def portal_facts(pairs: dict[str, str]) -> list[dict[str, str]]:
    out = []
    for want in FACT_LABELS:
        for k, v in pairs.items():
            if k.lower().startswith(want.lower()) and v and v.strip().upper() not in ("NA", "NIL", "-"):
                out.append({"label": k, "value": v.strip()[:300]})
                break
    return out


def tasks_from_facts(facts: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Checklist lines that follow directly from the portal's own fields. No AI, nothing guessed."""
    f = {x["label"].lower(): x["value"] for x in facts}
    pick = lambda start: next((v for k, v in f.items() if k.startswith(start.lower())), "")  # noqa: E731
    out: list[tuple[str, str]] = []
    if pick("Tender Fee in"):
        out.append((f"Pay the tender fee (₹{pick('Tender Fee in')}) online", "task"))
    if pick("Processing Fee in"):
        out.append((f"Pay the processing fee (₹{pick('Processing Fee in')}) online", "task"))
    if pick("EMD Amount"):
        out.append((f"Arrange the EMD of ₹{pick('EMD Amount')}" + (f" ({pick('EMD Fee Type')})" if pick("EMD Fee Type") else "") + " and keep the proof", "task"))
    if pick("Tenderer Class"):
        out.append((f"Registration class required: {pick('Tenderer Class')}", "condition"))
    if pick("Bid Validity"):
        out.append((f"Offer must stay valid for {pick('Bid Validity')} days", "condition"))
    if pick("Period Of Work"):
        out.append((f"Period of work: {pick('Period Of Work')} days", "condition"))
    nda = pick("NDA/Pre Qualification")
    if nda and not re.search(r"refer|tender doc", nda, re.I):
        out.append((f"Pre-qualification: {nda}", "condition"))
    if pick("Document Download"):
        out.append((f"Download the tender documents before {pick('Document Download')}", "task"))
    if pick("Bid Submission End"):
        out.append((f"Upload the bid before {pick('Bid Submission End')}", "task"))
    return out


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def locate_on_portal(client: Any, portal_key: str, organisation: str, tender_id: str) -> Any | None:
    """Finds a tender again in a fresh portal session (its old links expired). Reads only the departments whose name matches the bid's."""
    chain = [c for c in (organisation or "").split("||") if c.strip()]
    first = _compact(chain[0]) if chain else ""
    whole = _compact(" ".join(chain))
    orgs = client.organisations()
    ranked = [o for o in orgs if first and _compact(o[0]) == first] + [o for o in orgs if _compact(o[0]) and _compact(o[0]) in whole and _compact(o[0]) != first]
    for _name, _count, link in ranked[:4]:
        for t in client.tenders_of(link, portal_key):
            if t.tender_id == tender_id:
                return t
    return None


class BidError(ValueError):
    pass


EDITABLE = {
    "stage": "stage", "owner": 60, "notes": 4000, "emd_due": "date", "prebid_date": "date", "query_deadline": "date", "portal_checked": "date", "closing": "date", "opening": "date",
    "my_value": "money", "l1_value": "money", "l1_bidder": 120, "result_note": 1000,
}
_BID_LIST_COLUMNS = ("id, key, tender_id, portal, title, description, organisation, category, location, work_type, value, emd, closing, opening, stage, owner, "
                     "emd_due, prebid_date, query_deadline, portal_checked, my_value, l1_value, created, updated, stage_changed, (doc IS NOT NULL AND doc <> '') AS has_doc")


def clean_patch(raw: Any) -> dict[str, Any]:
    """Validates the fields a person may change on a bid. Raises BidError with a message fit to show."""
    if not isinstance(raw, dict):
        raise BidError("Nothing to change")
    out: dict[str, Any] = {}
    for k, v in raw.items():
        rule = EDITABLE.get(k)
        if rule is None:
            raise BidError(f"'{k}' cannot be changed")
        if rule == "stage":
            if v not in STAGES:
                raise BidError("Unknown stage")
            out[k] = v
        elif rule == "date":
            v = (str(v).strip() if v is not None else "")
            if v and (not _DATE.match(v) or _when(v) is None):
                raise BidError("Dates look like 2026-11-20, and must be real dates")
            out[k] = v or None
        elif rule == "money":
            if v in (None, ""):
                out[k] = None
            else:
                try:
                    n = float(v)
                except (TypeError, ValueError):
                    raise BidError("Amounts must be numbers") from None
                if isinstance(v, bool) or not 0 <= n < 1e13:
                    raise BidError("Amounts must be numbers")
                out[k] = n
        else:
            out[k] = str(v if v is not None else "").strip()[: int(rule)]
    return out


# The first bytes of each kind of file. A file whose contents do not match its name is refused: it is either a mistake or a disguise.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",), ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06"), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0",), ".xls": (b"\xd0\xcf\x11\xe0",),
}


def looks_like(ext: str, data: bytes) -> bool:
    if ext == ".txt":
        return b"\x00" not in data[:4096]
    sigs = _SIGNATURES.get(ext)
    return bool(sigs) and data.startswith(sigs)


class BidBook:
    def __init__(self, store: SeenStore, vault_dir: str | Path | None = None):
        self.store = store
        self.db, self.lock = store.db, store.lock
        self.vault_dir = Path(vault_dir) if vault_dir else None
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS bids (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, tender_id TEXT, portal TEXT, title TEXT, description TEXT,
                  organisation TEXT, category TEXT, location TEXT, work_type TEXT, value REAL, emd REAL, published TEXT, closing TEXT, opening TEXT,
                  stage TEXT NOT NULL DEFAULT 'interested', owner TEXT, notes TEXT, emd_due TEXT, prebid_date TEXT, query_deadline TEXT,
                  my_value REAL, l1_value REAL, l1_bidder TEXT, result_note TEXT,
                  doc TEXT, brief TEXT, eligibility TEXT, drafts TEXT, corrigenda TEXT,
                  created TEXT NOT NULL, updated TEXT NOT NULL, stage_changed TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bid_items (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, bid_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'task', text TEXT NOT NULL,
                  done INTEGER NOT NULL DEFAULT 0, owner TEXT, vault_id INTEGER, note TEXT, position INTEGER NOT NULL DEFAULT 0, source TEXT, created TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS bid_items_bid ON bid_items (bid_id);
                CREATE TABLE IF NOT EXISTS bid_changes (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, bid_id INTEGER NOT NULL, at TEXT NOT NULL, what TEXT NOT NULL, old TEXT, new TEXT,
                  seen INTEGER NOT NULL DEFAULT 0, notified INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS bid_changes_bid ON bid_changes (bid_id);
                CREATE TABLE IF NOT EXISTS vault (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'other', issued TEXT, expiry TEXT, notes TEXT,
                  filename TEXT, stored TEXT, size INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
                """
            )
            have = {r["name"] for r in self.db.execute("PRAGMA table_info(bids)")}
            for col in ("portal_facts", "portal_checked", "doc_checked"):
                if col not in have:
                    self.db.execute(f"ALTER TABLE bids ADD COLUMN {col} TEXT")  # added after the first release
            self.db.commit()

    # ---- bids ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _bid(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for f in ("doc", "brief", "eligibility", "drafts", "corrigenda", "portal_facts", "doc_checked"):
            if f in d:
                try:
                    d[f] = json.loads(d[f]) if d[f] else None
                except ValueError:
                    d[f] = None
        return d

    def id_for_key(self, key: str) -> int | None:
        with self.lock:
            r = self.db.execute("SELECT id FROM bids WHERE key = ?", (key,)).fetchone()
        return r["id"] if r else None

    def create_from_tender(self, key: str, now: datetime) -> tuple[dict, bool]:
        """Starts a bid from a collected tender. Returns (bid, created); a tender already in the pipeline is returned as it is."""
        existing = self.id_for_key(key)
        if existing:
            return self.get(existing) or {}, False
        t = self.store.get_tender(key)
        if not t:
            raise BidError("That tender is no longer in the list")
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO bids (key, tender_id, portal, title, description, organisation, category, location, work_type, value, emd, published, closing, opening, "
                "doc, brief, created, updated, stage_changed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, t["tender_id"], t["portal"], t["title"], t["description"], t["organisation"], t["category"], t["location"], t.get("work_type"), t["value"], t["emd"],
                 t["published"], t["closing"], t.get("opening"), t.get("doc"), t.get("brief"), _iso(now), _iso(now), _iso(now)),
            )
            self.db.commit()
            bid_id = cur.lastrowid
        return self.get(bid_id) or {}, True

    def get(self, bid_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()
        bid = self._bid(r)
        if bid and not bid["doc"]:
            # A document read from the Tenders page after the bid was started is picked up here.
            with self.lock:
                s = self.db.execute("SELECT doc, brief FROM seen WHERE key = ?", (bid["key"],)).fetchone()
            if s and s["doc"]:
                with self.lock:
                    self.db.execute("UPDATE bids SET doc = ?, brief = COALESCE(brief, ?) WHERE id = ?", (s["doc"], s["brief"], bid_id))
                    self.db.commit()
                bid["doc"], bid["brief"] = json.loads(s["doc"]), bid["brief"] or (json.loads(s["brief"]) if s["brief"] else None)
        return bid

    def list(self, stage: str | None = None) -> list[dict]:
        where, args = ("WHERE stage = ?", [stage]) if stage else ("", [])
        with self.lock:
            rows = self.db.execute(
                f"SELECT {_BID_LIST_COLUMNS}, (SELECT COUNT(*) FROM bid_items i WHERE i.bid_id = bids.id) AS items_total, "
                f"(SELECT COUNT(*) FROM bid_items i WHERE i.bid_id = bids.id AND i.done = 1) AS items_done, "
                f"(SELECT COUNT(*) FROM bid_changes c WHERE c.bid_id = bids.id AND c.seen = 0) AS unseen FROM bids {where}", args).fetchall()
        out = [dict(r) for r in rows]
        # Open bids by how soon they close (no date last); finished ones after, most recently decided first.
        live = sorted((b for b in out if b["stage"] not in CLOSED), key=lambda b: (b["closing"] is None, b["closing"] or ""))
        done = sorted((b for b in out if b["stage"] in CLOSED), key=lambda b: b["stage_changed"] or "", reverse=True)
        return live + done

    def update(self, bid_id: int, patch: dict[str, Any], now: datetime) -> dict:
        bid = self.get(bid_id)
        if not bid:
            raise BidError("No such bid")
        if not patch:
            return bid
        sets = {**patch, "updated": _iso(now)}
        if "stage" in patch and patch["stage"] != bid["stage"]:
            sets["stage_changed"] = _iso(now)
        cols = ", ".join(f"{k} = ?" for k in sets)
        with self.lock:
            self.db.execute(f"UPDATE bids SET {cols} WHERE id = ?", [*sets.values(), bid_id])
            self.db.commit()
        return self.get(bid_id) or bid

    def set_json(self, bid_id: int, field: str, value: Any, now: datetime) -> None:
        if field not in ("doc", "brief", "eligibility", "drafts", "corrigenda", "portal_facts", "doc_checked"):
            raise ValueError(field)
        with self.lock:
            self.db.execute(f"UPDATE bids SET {field} = ?, updated = ? WHERE id = ?", (json.dumps(value) if value is not None else None, _iso(now), bid_id))
            if field == "doc":  # new AI-read facts have not been checked by anyone yet
                self.db.execute("UPDATE bids SET doc_checked = NULL WHERE id = ?", (bid_id,))
            self.db.commit()

    # ---- the tender notice itself, kept against the tender so risk and BOQ steps never need it uploaded twice ----------

    def _source_path(self, key: str, kind: str) -> Path | None:
        import hashlib

        return self.vault_dir / "sources" / f"{hashlib.sha1(key.encode()).hexdigest()}.{kind}" if self.vault_dir else None

    def save_source(self, key: str, data: bytes | str) -> None:
        """Keeps the notice (a PDF, or pasted text) for this tender, replacing any earlier one."""
        if not self.vault_dir or not data:
            return
        self.delete_source(key)
        kind = "pdf" if isinstance(data, bytes) else "txt"
        path = self._source_path(key, kind)
        path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))  # type: ignore[union-attr]

    def load_source(self, key: str) -> tuple[str, str]:
        """(text, pdf_base64): whichever was kept, else two empty strings."""
        import base64

        pdf, txt = self._source_path(key, "pdf"), self._source_path(key, "txt")
        if pdf and pdf.is_file():
            return "", base64.b64encode(pdf.read_bytes()).decode("ascii")
        if txt and txt.is_file():
            return txt.read_text(encoding="utf-8", errors="ignore"), ""
        return "", ""

    def has_source(self, key: str) -> bool:
        return any(p and p.is_file() for p in (self._source_path(key, "pdf"), self._source_path(key, "txt")))

    def delete_source(self, key: str) -> None:
        for k in ("pdf", "txt"):
            p = self._source_path(key, k)
            if p:
                p.unlink(missing_ok=True)

    def sync_listing(self, key: str, fields: dict[str, Any]) -> None:
        """A tender added by hand was corrected: carry the correction into its bid's own copy."""
        cols = {k: v for k, v in fields.items() if k in ("title", "description", "organisation", "location", "value", "emd", "closing", "opening", "tender_id")}
        if not cols:
            return
        with self.lock:
            self.db.execute(f"UPDATE bids SET {', '.join(f'{c} = ?' for c in cols)} WHERE key = ?", [*cols.values(), key])
            self.db.commit()

    def delete(self, bid_id: int) -> bool:
        with self.lock:
            self.db.execute("DELETE FROM bid_items WHERE bid_id = ?", (bid_id,))
            self.db.execute("DELETE FROM bid_changes WHERE bid_id = ?", (bid_id,))
            cur = self.db.execute("DELETE FROM bids WHERE id = ?", (bid_id,))
            self.db.commit()
            return cur.rowcount > 0

    # ---- checklist -------------------------------------------------------------------------------------------------

    def items(self, bid_id: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM bid_items WHERE bid_id = ? ORDER BY position, id", (bid_id,)).fetchall()
        return [{**dict(r), "done": bool(r["done"])} for r in rows]

    def add_item(self, bid_id: int, text: str, kind: str, now: datetime, source: str = "manual") -> dict:
        text = (text or "").strip()[:300]
        if not text:
            raise BidError("Type the item first")
        if kind not in ("document", "condition", "task"):
            kind = "task"
        with self.lock:
            n = self.db.execute("SELECT COUNT(*), COALESCE(MAX(position), 0) FROM bid_items WHERE bid_id = ?", (bid_id,)).fetchone()
            if n[0] >= MAX_ITEMS:
                raise BidError(f"A bid can hold up to {MAX_ITEMS} checklist items")
            cur = self.db.execute("INSERT INTO bid_items (bid_id, kind, text, position, source, created) VALUES (?,?,?,?,?,?)", (bid_id, kind, text, n[1] + 1, source, _iso(now)))
            self.db.commit()
            row = self.db.execute("SELECT * FROM bid_items WHERE id = ?", (cur.lastrowid,)).fetchone()
        return {**dict(row), "done": False}

    def update_item(self, bid_id: int, item_id: int, raw: dict[str, Any]) -> dict:
        sets: dict[str, Any] = {}
        if "done" in raw:
            sets["done"] = 1 if raw["done"] else 0
        if "text" in raw:
            t = str(raw["text"]).strip()[:300]
            if not t:
                raise BidError("An item needs some text")
            sets["text"] = t
        for f in ("owner", "note"):
            if f in raw:
                sets[f] = str(raw[f] or "").strip()[:200]
        if "vault_id" in raw:
            v = raw["vault_id"]
            if v in (None, ""):
                sets["vault_id"] = None
            else:
                if not isinstance(v, int) or isinstance(v, bool) or not self.vault_get(v):
                    raise BidError("That document is not in the vault")
                sets["vault_id"] = v
        if not sets:
            raise BidError("Nothing to change")
        with self.lock:
            cur = self.db.execute(f"UPDATE bid_items SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ? AND bid_id = ?", [*sets.values(), item_id, bid_id])
            self.db.commit()
            if not cur.rowcount:
                raise BidError("No such item")
            row = self.db.execute("SELECT * FROM bid_items WHERE id = ?", (item_id,)).fetchone()
        return {**dict(row), "done": bool(row["done"])}

    def delete_item(self, bid_id: int, item_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM bid_items WHERE id = ? AND bid_id = ?", (item_id, bid_id))
            self.db.commit()
            return cur.rowcount > 0

    def generate_items(self, bid_id: int, now: datetime) -> int:
        """Adds the standard steps and, if the tender document has been read, its required documents and conditions. Never duplicates."""
        bid = self.get(bid_id)
        if not bid:
            raise BidError("No such bid")
        have = {_norm(i["text"]) for i in self.items(bid_id)}
        doc = bid.get("doc") or {}
        wanted = [(t, "task", "standard") for t in STANDARD_TASKS]
        wanted += [(d, "document", "tender") for d in doc.get("documents", [])]
        wanted += [(r, "condition", "tender") for r in doc.get("requirements", [])]
        wanted += [(t, k, "portal") for t, k in tasks_from_facts(bid.get("portal_facts") or [])]
        added = 0
        for text, kind, source in wanted:
            if _norm(text) in have or not _norm(text):
                continue
            try:
                self.add_item(bid_id, text, kind, now, source)
            except BidError:
                break
            have.add(_norm(text))
            added += 1
        return added

    def with_matches(self, bid: dict, today: datetime) -> list[dict]:
        """The checklist, each required document matched to the vault: ready, expires before the bid closes, expired, or missing."""
        ref = _day(bid.get("closing")) or today.date().isoformat()
        docs = self.vault_list()
        by_id = {d["id"]: d for d in docs}
        out = []
        for it in self.items(bid["id"]):
            match = None
            if it["kind"] == "document":
                chosen = by_id.get(it["vault_id"]) if it["vault_id"] else None
                kind = chosen["kind"] if chosen else infer_kind(it["text"])
                candidates = [chosen] if chosen else [d for d in docs if d["kind"] == kind and kind != "other"]
                if candidates:
                    best = None
                    for d in sorted(candidates, key=lambda d: (d["expiry"] or "9999", d["created"]), reverse=True):
                        status = self.doc_status(d, today, ref)
                        if status == "ok":
                            best = (d, status)
                            break
                        best = best or (d, status)
                    d, status = best  # type: ignore[misc]
                    match = {"status": status, "vault_id": d["id"], "name": d["name"], "expiry": d["expiry"], "kind": kind, "chosen": bool(chosen)}
                else:
                    match = {"status": "missing" if kind != "other" else "unknown", "vault_id": None, "name": "", "expiry": None, "kind": kind, "chosen": False}
            out.append({**it, "match": match})
        return out

    @staticmethod
    def doc_status(d: dict, today: datetime, ref_day: str | None = None) -> str:
        exp = d.get("expiry")
        if not exp:
            return "ok"
        if exp < today.date().isoformat():
            return "expired"
        if ref_day and exp < ref_day:
            return "expires_before_close"
        return "ok"

    # ---- changes (corrigenda, moved dates) ---------------------------------------------------------------------------

    def add_change(self, bid_id: int, what: str, old: str | None, new: str | None, now: datetime) -> None:
        with self.lock:
            self.db.execute("INSERT INTO bid_changes (bid_id, at, what, old, new) VALUES (?,?,?,?,?)", (bid_id, _iso(now), what, old, new))
            self.db.commit()

    def changes(self, bid_id: int, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, at, what, old, new, seen FROM bid_changes WHERE bid_id = ? ORDER BY id DESC LIMIT ?", (bid_id, limit)).fetchall()
        return [{**dict(r), "seen": bool(r["seen"])} for r in rows]

    def mark_seen(self, bid_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE bid_changes SET seen = 1 WHERE bid_id = ?", (bid_id,))
            self.db.commit()

    def unseen_total(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM bid_changes c JOIN bids b ON b.id = c.bid_id WHERE c.seen = 0 AND b.stage IN ('interested','preparing','submitted','opened')").fetchone()[0]

    def watch_keys(self) -> dict[str, int]:
        marks = ",".join("?" for _ in WATCHED)
        with self.lock:
            return {r["key"]: r["id"] for r in self.db.execute(f"SELECT key, id FROM bids WHERE stage IN ({marks})", WATCHED)}

    def watch(self, t: Any, client: Any, now: datetime, bid_id: int | None = None) -> int:
        """Looks again at one tender you are bidding for, using this run's fresh list row and one detail-page fetch.

        Records a change when the closing or opening date moved, a corrigendum appeared, or the pre-bid meeting date became known.
        Returns how many changes were recorded. Never raises: a portal hiccup must not fail a run.
        """
        bid_id = bid_id or self.id_for_key(t.key)
        bid = self.get(bid_id) if bid_id else None
        if not bid or bid["stage"] not in WATCHED:
            return 0
        found = 0
        for what, new_dt in (("closing", t.closing), ("opening", t.opening)):
            new, old = _iso(new_dt), bid.get(what)
            if new and new != old:
                self.add_change(bid["id"], what, old, new, now)
                with self.lock:
                    self.db.execute(f"UPDATE bids SET {what} = ?, updated = ? WHERE id = ?", (new, _iso(now), bid["id"]))
                    self.db.commit()
                self._sync_tender_dates(t.key, new_dt if what == "closing" else None, new_dt if what == "opening" else None, what)
                found += 1
        try:
            fetch = getattr(client, "detail_page", None)
            pairs, corr = fetch(t) if fetch else (client.detail(t), None)
        except Exception as e:  # noqa: BLE001 - one tender's detail page failing must not stop the run
            log.info("watch: detail failed for %s: %s", t.key, e)
            pairs, corr = {}, None
        if corr is not None:
            known = bid["corrigenda"]
            if known is None:
                if corr:
                    self.add_change(bid["id"], "corrigendum", None, f"{len(corr)} already published: " + "; ".join(corr)[:400], now)
                    found += 1
            else:
                for c in corr:
                    if c not in known:
                        self.add_change(bid["id"], "corrigendum", None, c[:300], now)
                        found += 1
            self.set_json(bid["id"], "corrigenda", corr, now)
        pre = next((v for k, v in pairs.items() if k.lower().startswith("pre bid meeting date")), "")
        d = parse_date(pre)
        if d and not bid.get("prebid_date"):
            with self.lock:
                self.db.execute("UPDATE bids SET prebid_date = ?, updated = ? WHERE id = ?", (_iso(d), _iso(now), bid["id"]))
                self.db.commit()
            self.add_change(bid["id"], "prebid", None, _iso(d), now)
            found += 1
        return found

    def _sync_tender_dates(self, key: str, closing: datetime | None, opening: datetime | None, what: str) -> None:
        """Keeps the tender list (and so the digest's 'closing soon') in step with a date that moved."""
        with self.lock:
            r = self.db.execute("SELECT payload FROM seen WHERE key = ?", (key,)).fetchone()
            if not r or not r["payload"]:
                return
            t = _from_json(r["payload"])
            if what == "closing":
                t.closing = closing
            else:
                t.opening = opening
            self.db.execute("UPDATE seen SET payload = ?, closing = ? WHERE key = ?", (_to_json(t), _iso(t.closing), key))
            self.db.commit()

    # ---- alerts ----------------------------------------------------------------------------------------------------

    def alerts(self, now: datetime, days: int = 7) -> list[dict]:
        """Dates coming up (or just missed) across bids in progress, plus papers in the vault about to expire."""
        out: list[dict] = []
        today = now.date()

        def add(bid: dict | None, kind: str, label: str, when: datetime | None, extra: str = "") -> None:
            if not when:
                return
            left = (when.date() - today).days
            if left > days or left < -2:
                return
            out.append({
                "bid_id": bid["id"] if bid else None, "kind": kind, "label": label, "date": _iso(when), "days_left": left,
                "urgency": "overdue" if when < now else "today" if left == 0 else "soon" if left <= 2 else "later",
                "title": ((bid["description"] or bid["title"]) if bid else "")[:140], "tender_id": bid["tender_id"] if bid else "", "extra": extra,
            })

        for b in self.list():
            if b["stage"] in PREPARING:
                add(b, "closing", "Bid closes", _when(b["closing"], False))
                add(b, "emd", "EMD due", _when(b["emd_due"]))
                add(b, "prebid", "Pre-bid meeting", _when(b["prebid_date"], False))
                add(b, "query", "Last date for queries", _when(b["query_deadline"]))
                c0 = _when(b["closing"], False)
                if b["portal"] == "manual" and c0 and 0 <= (c0.date() - today).days <= 5:
                    seen_on = _when(b.get("portal_checked"))
                    if not seen_on or (today - seen_on.date()).days >= 2:
                        add(b, "recheck", "Check the portal for corrigenda", c0, "Added by hand, so the app cannot watch it")
                left = b["items_total"] - b["items_done"]
                c = _when(b["closing"], False)
                if left and c and 0 <= (c.date() - today).days <= 3:
                    add(b, "checklist", f"{left} checklist item{'s' if left != 1 else ''} still open", c)
            elif b["stage"] == "submitted":
                add(b, "opening", "Bids open", _when(b["opening"], False))
        for d in self.vault_list():
            e = _when(d["expiry"])
            if e and (e.date() - today).days <= max(days, 30) and (e.date() - today).days >= -2:
                out.append({"bid_id": None, "kind": "vault", "label": f"{d['name']} expires", "date": _iso(e), "days_left": (e.date() - today).days,
                            "urgency": "overdue" if e < now else "soon" if (e.date() - today).days <= 7 else "later", "title": "", "tender_id": "", "extra": VAULT_KINDS.get(d["kind"], ("", ""))[0]})
        out.sort(key=lambda a: a["date"] or "")
        return out

    def digest_block(self, now: datetime) -> dict[str, Any]:
        """What the daily email adds for your bids: the next few days and changes not yet mailed."""
        with self.lock:
            rows = self.db.execute(
                "SELECT c.id, c.what, c.old, c.new, b.tender_id, b.title, b.description FROM bid_changes c JOIN bids b ON b.id = c.bid_id "
                "WHERE c.notified = 0 AND b.stage IN ('interested','preparing','submitted','opened') ORDER BY c.id").fetchall()
        changes = [{"id": r["id"], "what": r["what"], "old": r["old"], "new": r["new"], "tender_id": r["tender_id"], "title": (r["description"] or r["title"] or "")[:140]} for r in rows]
        return {"alerts": [a for a in self.alerts(now, 5) if a["urgency"] != "later" or a["days_left"] <= 3], "changes": changes}

    def mark_notified(self, change_ids: list[int]) -> None:
        with self.lock:
            self.db.executemany("UPDATE bid_changes SET notified = 1 WHERE id = ?", [(i,) for i in change_ids])
            self.db.commit()

    # ---- results ---------------------------------------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Win rate and how your bids compare with the winner, overall and by work type, department and size."""
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT stage, work_type, organisation, value, my_value, l1_value FROM bids")]
        decided = [r for r in rows if r["stage"] in ("won", "lost")]
        won = [r for r in decided if r["stage"] == "won"]
        lost = [r for r in decided if r["stage"] == "lost"]
        gaps = [(r["my_value"] - r["l1_value"]) / r["l1_value"] * 100 for r in lost if r["my_value"] and r["l1_value"]]

        def group(label: str, key) -> list[dict]:
            agg: dict[str, dict] = {}
            for r in decided:
                g = agg.setdefault(key(r), {"label": key(r), "won": 0, "lost": 0, "won_value": 0.0})
                g["won" if r["stage"] == "won" else "lost"] += 1
                if r["stage"] == "won":
                    g["won_value"] += r["my_value"] or r["value"] or 0
            return sorted(agg.values(), key=lambda g: -(g["won"] + g["lost"]))[:10]

        return {
            "total": len(rows), "in_progress": sum(1 for r in rows if r["stage"] in ACTIVE), "submitted": sum(1 for r in rows if r["stage"] in ("submitted", "opened", "won", "lost")),
            "dropped": sum(1 for r in rows if r["stage"] == "dropped"), "won": len(won), "lost": len(lost),
            "win_rate": round(100 * len(won) / len(decided)) if decided else None,
            "won_value": sum(r["my_value"] or r["value"] or 0 for r in won),
            "avg_gap_to_l1": round(sum(gaps) / len(gaps), 1) if gaps else None, "gap_samples": len(gaps),
            "by_work_type": group("work type", lambda r: r["work_type"] or "Not tagged"),
            "by_department": group("department", lambda r: _dept(r["organisation"])),
            "by_size": group("size", lambda r: _band(r["value"])),
        }

    def l1_outcomes(self, work_type: str | None, value: float | None) -> tuple[list[float], str]:
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT stage, work_type, value, my_value, l1_value FROM bids WHERE stage IN ('won','lost')")]
        return l1_outcomes_from(rows, work_type, value)

    def history_notes(self, work_type: str | None, organisation: str | None, value: float | None) -> list[str]:
        """Plain lines about how you have done on similar tenders: used on a tender's page and in its bid brief."""
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT stage, work_type, organisation, value FROM bids WHERE stage IN ('won','lost')")]
        if not rows:
            return []
        out = []
        for label, pick in ((f"{work_type} work" if work_type else "", lambda r: bool(work_type) and r["work_type"] == work_type),
                            (f"{_dept(organisation)} tenders" if organisation else "", lambda r: bool(organisation) and _dept(r["organisation"]) == _dept(organisation)),
                            (f"tenders of size {_band(value)}" if value else "", lambda r: value is not None and _band(r["value"]) == _band(value))):
            hit = [r for r in rows if pick(r)]
            if label and hit:
                w = sum(1 for r in hit if r["stage"] == "won")
                out.append(f"Your record on {label}: {w} won, {len(hit) - w} lost")
        return out

    # ---- vault -----------------------------------------------------------------------------------------------------

    def vault_list(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT id, name, kind, issued, expiry, notes, filename, size, created FROM vault ORDER BY kind, name")]

    def vault_get(self, doc_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT id, name, kind, issued, expiry, notes, filename, stored, size, created FROM vault WHERE id = ?", (doc_id,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _vault_fields(raw: dict[str, Any], partial: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "name" in raw or not partial:
            name = str(raw.get("name") or "").strip()[:120]
            if not name:
                raise BidError("Give the document a name")
            out["name"] = name
        if "kind" in raw or not partial:
            kind = raw.get("kind") or "other"
            if kind not in VAULT_KINDS:
                raise BidError("Unknown kind of document")
            out["kind"] = kind
        for f in ("issued", "expiry"):
            if f in raw:
                v = str(raw[f] or "").strip()
                if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                    raise BidError("Dates look like 2026-11-20")
                out[f] = v or None
        if "notes" in raw:
            out["notes"] = str(raw["notes"] or "").strip()[:500]
        return out

    def vault_add(self, raw: dict[str, Any], data: bytes | None, filename: str, now: datetime) -> dict:
        fields = self._vault_fields(raw, False)
        if len(self.vault_list()) >= MAX_VAULT_DOCS:
            raise BidError(f"The vault holds up to {MAX_VAULT_DOCS} documents")
        stored, size, shown = None, 0, ""
        if data:
            if self.vault_dir is None:
                raise BidError("File storage is not set up on this server")
            if len(data) > MAX_FILE_BYTES:
                raise BidError("That file is over 10 MB")
            ext = Path(filename or "").suffix.lower()
            if ext not in FILE_TYPES:
                raise BidError("Allowed file types: " + ", ".join(sorted(FILE_TYPES)))
            if not looks_like(ext, data):
                raise BidError(f"That file does not look like a real {ext} file. Check that you chose the right one")
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            stored = uuid.uuid4().hex + ext
            (self.vault_dir / stored).write_bytes(data)
            size, shown = len(data), re.sub(r"[^\w.\- ()]", "_", Path(filename).name)[:120]
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO vault (name, kind, issued, expiry, notes, filename, stored, size, created) VALUES (?,?,?,?,?,?,?,?,?)",
                (fields["name"], fields["kind"], fields.get("issued"), fields.get("expiry"), fields.get("notes", ""), shown, stored, size, _iso(now)))
            self.db.commit()
        return self.vault_get(cur.lastrowid) or {}

    def vault_update(self, doc_id: int, raw: dict[str, Any]) -> dict:
        fields = self._vault_fields(raw, True)
        if not fields:
            raise BidError("Nothing to change")
        with self.lock:
            cur = self.db.execute(f"UPDATE vault SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), doc_id])
            self.db.commit()
        if not cur.rowcount:
            raise BidError("No such document")
        return self.vault_get(doc_id) or {}

    def vault_delete(self, doc_id: int) -> bool:
        d = self.vault_get(doc_id)
        if not d:
            return False
        with self.lock:
            self.db.execute("DELETE FROM vault WHERE id = ?", (doc_id,))
            self.db.execute("UPDATE bid_items SET vault_id = NULL WHERE vault_id = ?", (doc_id,))
            self.db.commit()
        if d["stored"] and self.vault_dir:
            try:
                (self.vault_dir / d["stored"]).unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove vault file %s", d["stored"])
        return True

    def vault_file(self, doc_id: int) -> tuple[Path, str] | None:
        d = self.vault_get(doc_id)
        if not d or not d["stored"] or not self.vault_dir:
            return None
        path = (self.vault_dir / d["stored"]).resolve()
        if self.vault_dir.resolve() not in path.parents or not path.is_file():
            return None
        return path, d["filename"] or d["stored"]


def default_vault_dir(state_path: str) -> Path:
    """Next to the database, on the same persistent volume. A throwaway folder for the in-memory test database."""
    import os

    if os.environ.get("VAULT_DIR"):
        return Path(os.environ["VAULT_DIR"])
    if state_path == ":memory:":
        return Path(tempfile.mkdtemp(prefix="tendai-vault-"))
    return Path(state_path).expanduser().parent / "vault"
