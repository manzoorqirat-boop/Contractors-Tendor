"""Remembers every tender read and whether it has been emailed, so each one appears in the digest once.

Also holds the civil tenders themselves, so the digest can list ones closing soon without asking the portals again.
Keep this file on a persistent volume (see the README), or every run starts from nothing.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .gepnic import Tender

_DATES = ("published", "closing", "opening")


def _to_json(t: Tender) -> str:
    data = dataclasses.asdict(t)
    for name in _DATES:
        data[name] = data[name].isoformat() if data[name] else None
    data.pop("detail_path", None)  # session-bound, useless tomorrow
    return json.dumps(data)


def _from_json(raw: str) -> Tender:
    data = json.loads(raw)
    for name in _DATES:
        data[name] = datetime.fromisoformat(data[name]) if data[name] else None
    return Tender(**data)


class SeenStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS seen ("
            " key TEXT PRIMARY KEY, first_seen TEXT NOT NULL, civil INTEGER NOT NULL DEFAULT 0,"
            " emailed INTEGER NOT NULL DEFAULT 0, closing TEXT, payload TEXT)"
        )
        self.db.commit()

    def is_seen(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone() is not None

    def skip(self, key: str, now: datetime) -> None:
        """Seen but not worth keeping (too old to matter, or not civil)."""
        self.db.execute("INSERT OR IGNORE INTO seen (key, first_seen) VALUES (?, ?)", (key, now.isoformat(timespec="seconds")))
        self.db.commit()

    def keep(self, tender: Tender, now: datetime) -> None:
        self.db.execute(
            "INSERT INTO seen (key, first_seen, civil, closing, payload) VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET civil = 1, closing = excluded.closing, payload = excluded.payload",
            (tender.key, now.isoformat(timespec="seconds"), tender.closing.isoformat() if tender.closing else None, _to_json(tender)),
        )
        self.db.commit()

    def pending(self, now: datetime) -> list[Tender]:
        """Civil tenders not yet emailed and still open. Anything that failed to send is picked up again next run."""
        rows = self.db.execute(
            "SELECT payload FROM seen WHERE civil = 1 AND emailed = 0 AND (closing IS NULL OR closing >= ?)",
            (now.isoformat(),),
        ).fetchall()
        return [_from_json(r[0]) for r in rows]

    def closing_soon(self, now: datetime, days: int) -> list[Tender]:
        rows = self.db.execute(
            "SELECT payload FROM seen WHERE civil = 1 AND closing >= ? AND closing <= ?",
            (now.isoformat(), (now + timedelta(days=days)).isoformat()),
        ).fetchall()
        return [_from_json(r[0]) for r in rows]

    def mark_emailed(self, keys: list[str]) -> None:
        self.db.executemany("UPDATE seen SET emailed = 1 WHERE key = ?", [(k,) for k in keys])
        self.db.commit()

    def prune(self, now: datetime, keep_days: int = 60) -> None:
        self.db.execute("DELETE FROM seen WHERE first_seen < ?", ((now - timedelta(days=keep_days)).isoformat(),))
        self.db.commit()

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def close(self) -> None:
        self.db.close()
