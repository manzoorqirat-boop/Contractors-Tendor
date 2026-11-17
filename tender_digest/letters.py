"""Letter templates with a version history, filled in from a bid.

A bid needs the same few letters again and again: a covering letter, a request for extension, a clarification query, an undertaking.
A template holds the wording once, with placeholders such as {{tender_id}} or {{closing}} that are filled from the bid and the company
facts. Every save keeps the earlier wording, so a change can be compared and put back.
"""
from __future__ import annotations

import builtins
import re
import sqlite3
from datetime import datetime
from typing import Any

MAX_TEMPLATES = 100
MAX_VERSIONS = 60       # per template; the oldest are dropped beyond this
MAX_BODY = 20_000
PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")

# What every bid can fill in. Company facts add {{company_<label>}} (the label in lower case, other characters as underscores).
BID_FIELDS: dict[str, str] = {
    "tender_id": "Tender ID", "title": "Tender title", "organisation": "Department / organisation", "portal": "Portal", "location": "Location",
    "closing": "Closing date", "opening": "Bid opening date", "prebid": "Pre-bid meeting date", "emd": "EMD amount", "value": "Estimated value", "today": "Today's date",
}


class LetterError(ValueError):
    pass


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]


def used_placeholders(body: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER.findall(body)))


def render(body: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """The body with every known placeholder filled. Returns (text, placeholders that had no value). Unknown ones are left in place so they are easy to spot."""
    missing: list[str] = []

    def put(m: re.Match[str]) -> str:
        v = values.get(m.group(1), "")
        if not v:
            if m.group(1) not in missing:
                missing.append(m.group(1))
            return m.group(0)
        return v

    return PLACEHOLDER.sub(put, body), missing


class Letters:
    def __init__(self, store: Any):
        self.db, self.lock = store.db, store.lock
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS letter_templates (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS letter_versions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, note TEXT,
                  author TEXT, created TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS letter_versions_t ON letter_versions (template_id, version);
                """
            )
            self.db.commit()

    @staticmethod
    def _clean_name(name: Any) -> str:
        n = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if not n:
            raise LetterError("Give the template a name")
        return n

    @staticmethod
    def _clean_body(body: Any) -> str:
        b = str(body or "").replace("\r\n", "\n").strip("\n")
        if not b.strip():
            raise LetterError("The template is empty")
        if len(b) > MAX_BODY:
            raise LetterError(f"A template can be up to {MAX_BODY:,} characters")
        return b

    def _latest(self, template_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM letter_versions WHERE template_id = ? ORDER BY version DESC LIMIT 1", (template_id,)).fetchone()

    def _shape(self, t: sqlite3.Row) -> dict:
        v = self._latest(t["id"])
        return {"id": t["id"], "name": t["name"], "created": t["created"], "updated": t["updated"], "version": v["version"] if v else 0, "body": v["body"] if v else "",
                "note": v["note"] if v else "", "author": v["author"] if v else "", "placeholders": used_placeholders(v["body"]) if v else []}

    def list(self) -> list[dict]:
        with self.lock:
            return [self._shape(t) for t in self.db.execute("SELECT * FROM letter_templates ORDER BY name COLLATE NOCASE")]

    def get(self, template_id: int) -> dict | None:
        with self.lock:
            t = self.db.execute("SELECT * FROM letter_templates WHERE id = ?", (template_id,)).fetchone()
            return self._shape(t) if t else None

    def create(self, name: Any, body: Any, author: str, now: datetime, note: str = "") -> dict:
        name, body = self._clean_name(name), self._clean_body(body)
        with self.lock:
            if self.db.execute("SELECT COUNT(*) FROM letter_templates").fetchone()[0] >= MAX_TEMPLATES:
                raise LetterError(f"Up to {MAX_TEMPLATES} templates can be kept")
            if self.db.execute("SELECT 1 FROM letter_templates WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
                raise LetterError("A template with that name already exists")
            stamp = now.isoformat(timespec="seconds")
            cur = self.db.execute("INSERT INTO letter_templates (name, created, updated) VALUES (?,?,?)", (name, stamp, stamp))
            self.db.execute("INSERT INTO letter_versions (template_id, version, body, note, author, created) VALUES (?,?,?,?,?,?)",
                            (cur.lastrowid, 1, body, str(note or "")[:200], author, stamp))
            self.db.commit()
        return self.get(cur.lastrowid) or {}

    def save(self, template_id: int, body: Any, author: str, now: datetime, name: Any = None, note: str = "") -> dict:
        """Saves new wording as the next version (nothing is added when the wording did not change). A new name renames the template."""
        body = self._clean_body(body)
        with self.lock:
            t = self.db.execute("SELECT * FROM letter_templates WHERE id = ?", (template_id,)).fetchone()
            if not t:
                raise LetterError("No such template")
            stamp = now.isoformat(timespec="seconds")
            if name is not None and self._clean_name(name) != t["name"]:
                new = self._clean_name(name)
                if self.db.execute("SELECT 1 FROM letter_templates WHERE name = ? COLLATE NOCASE AND id != ?", (new, template_id)).fetchone():
                    raise LetterError("A template with that name already exists")
                self.db.execute("UPDATE letter_templates SET name = ?, updated = ? WHERE id = ?", (new, stamp, template_id))
            last = self._latest(template_id)
            if not last or last["body"] != body:
                self.db.execute("INSERT INTO letter_versions (template_id, version, body, note, author, created) VALUES (?,?,?,?,?,?)",
                                (template_id, (last["version"] if last else 0) + 1, body, str(note or "")[:200], author, stamp))
                self.db.execute("UPDATE letter_templates SET updated = ? WHERE id = ?", (stamp, template_id))
                self.db.execute("DELETE FROM letter_versions WHERE template_id = ? AND version <= (SELECT MAX(version) FROM letter_versions WHERE template_id = ?) - ?",
                                (template_id, template_id, MAX_VERSIONS))
            self.db.commit()
        return self.get(template_id) or {}

    def versions(self, template_id: int) -> builtins.list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT version, body, note, author, created FROM letter_versions WHERE template_id = ? ORDER BY version DESC", (template_id,))]

    def restore(self, template_id: int, version: int, author: str, now: datetime) -> dict:
        """Puts an earlier wording back as a NEW version, so nothing in the history is lost."""
        with self.lock:
            r = self.db.execute("SELECT body FROM letter_versions WHERE template_id = ? AND version = ?", (template_id, version)).fetchone()
        if not r:
            raise LetterError("That version no longer exists")
        return self.save(template_id, r["body"], author, now, note=f"Restored version {version}")

    def delete(self, template_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM letter_templates WHERE id = ?", (template_id,))
            self.db.execute("DELETE FROM letter_versions WHERE template_id = ?", (template_id,))
            self.db.commit()
        return bool(cur.rowcount)
