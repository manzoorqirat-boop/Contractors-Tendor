"""The service's database (SQLite): every civil tender found, which have been emailed, run history and saved settings.

Keep this file on a persistent volume (see the README), or every restart begins from nothing.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .gepnic import Tender

_DATES = ("published", "closing", "opening")

# Columns added after the first release are created on open, so an older database keeps working.
_TENDER_COLUMNS = {
    "portal": "TEXT", "tender_id": "TEXT", "published": "TEXT", "value": "REAL", "emd": "REAL", "category": "TEXT",
    "location": "TEXT", "organisation": "TEXT", "title": "TEXT", "description": "TEXT",
}

SORTS = {
    "closing": "closing ASC", "closing_desc": "closing DESC", "published": "published DESC",
    "value": "value DESC", "value_asc": "value ASC", "first_seen": "first_seen DESC",
}


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


def _iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


class SeenStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the web threads and the run thread; every use goes through the lock.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen (
                  key TEXT PRIMARY KEY, first_seen TEXT NOT NULL, civil INTEGER NOT NULL DEFAULT 0,
                  emailed INTEGER NOT NULL DEFAULT 0, closing TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT NOT NULL, status TEXT NOT NULL,
                  started TEXT NOT NULL, finished TEXT, portals TEXT, send_email INTEGER NOT NULL DEFAULT 0,
                  reports TEXT, new_civil INTEGER NOT NULL DEFAULT 0, emailed_count INTEGER NOT NULL DEFAULT 0,
                  error TEXT, log TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            have = {r["name"] for r in self.db.execute("PRAGMA table_info(seen)")}
            added = False
            for name, kind in _TENDER_COLUMNS.items():
                if name not in have:
                    self.db.execute(f"ALTER TABLE seen ADD COLUMN {name} {kind}")
                    added = True
            if added:
                self._backfill()
            self.db.execute("CREATE INDEX IF NOT EXISTS seen_civil_closing ON seen (civil, closing)")
            self.db.commit()

    def _backfill(self) -> None:
        for row in self.db.execute("SELECT key, payload FROM seen WHERE payload IS NOT NULL").fetchall():
            t = _from_json(row["payload"])
            self.db.execute(
                "UPDATE seen SET portal=?, tender_id=?, published=?, value=?, emd=?, category=?, location=?, organisation=?, title=?, description=? WHERE key=?",
                (t.portal, t.tender_id, _iso(t.published), t.value, t.emd, t.category, t.location, t.organisation, t.title, t.description, row["key"]),
            )

    # ---- tenders --------------------------------------------------------------------------------------------

    def is_seen(self, key: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone() is not None

    def skip(self, key: str, now: datetime) -> None:
        """Seen but not worth keeping (too old to matter, or not civil)."""
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO seen (key, first_seen) VALUES (?, ?)", (key, _iso(now)))
            self.db.commit()

    def keep(self, t: Tender, now: datetime) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO seen (key, first_seen, civil, closing, payload, portal, tender_id, published, value, emd, category, location, organisation, title, description) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET civil=1, closing=excluded.closing, payload=excluded.payload, portal=excluded.portal, "
                "tender_id=excluded.tender_id, published=excluded.published, value=excluded.value, emd=excluded.emd, category=excluded.category, "
                "location=excluded.location, organisation=excluded.organisation, title=excluded.title, description=excluded.description",
                (t.key, _iso(now), _iso(t.closing), _to_json(t), t.portal, t.tender_id, _iso(t.published), t.value, t.emd,
                 t.category, t.location, t.organisation, t.title, t.description),
            )
            self.db.commit()

    def pending(self, now: datetime) -> list[Tender]:
        """Civil tenders not yet emailed and still open. Anything that failed to send is picked up again next run."""
        with self.lock:
            rows = self.db.execute(
                "SELECT payload FROM seen WHERE civil = 1 AND emailed = 0 AND (closing IS NULL OR closing >= ?)", (now.isoformat(),)
            ).fetchall()
        return [_from_json(r[0]) for r in rows]

    def closing_soon(self, now: datetime, days: int) -> list[Tender]:
        with self.lock:
            rows = self.db.execute(
                "SELECT payload FROM seen WHERE civil = 1 AND closing >= ? AND closing <= ?",
                (now.isoformat(), (now + timedelta(days=days)).isoformat()),
            ).fetchall()
        return [_from_json(r[0]) for r in rows]

    def mark_emailed(self, keys: list[str]) -> None:
        with self.lock:
            self.db.executemany("UPDATE seen SET emailed = 1 WHERE key = ?", [(k,) for k in keys])
            self.db.commit()

    def prune(self, now: datetime, keep_days: int = 120) -> None:
        with self.lock:
            self.db.execute("DELETE FROM seen WHERE first_seen < ?", (_iso(now - timedelta(days=keep_days)),))
            self.db.commit()

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    # ---- browsing -------------------------------------------------------------------------------------------

    @staticmethod
    def _where(now: datetime, f: dict[str, Any]) -> tuple[str, list[Any]]:
        where, args = ["civil = 1"], []  # type: list[str], list[Any]
        if f.get("portal"):
            ports = [p for p in str(f["portal"]).split(",") if p]
            where.append(f"portal IN ({','.join('?' * len(ports))})")
            args += ports
        if f.get("q"):
            like = f"%{str(f['q']).strip()}%"
            where.append("(title LIKE ? OR description LIKE ? OR organisation LIKE ? OR tender_id LIKE ? OR location LIKE ?)")
            args += [like] * 5
        if f.get("category"):
            where.append("category = ?")
            args.append(f["category"])
        if f.get("min_value") is not None:
            where.append("value >= ?")
            args.append(float(f["min_value"]))
        if f.get("max_value") is not None:
            where.append("value <= ?")
            args.append(float(f["max_value"]))
        status = f.get("status") or "open"
        if status == "open":
            where.append("(closing IS NULL OR closing >= ?)")
            args.append(now.isoformat())
        elif status == "closing_soon":
            where.append("closing >= ? AND closing <= ?")
            args += [now.isoformat(), (now + timedelta(days=int(f.get("soon_days") or 3))).isoformat()]
        elif status == "closed":
            where.append("closing < ?")
            args.append(now.isoformat())
        if f.get("since_days"):
            where.append("first_seen >= ?")
            args.append(_iso(now - timedelta(days=int(f["since_days"]))))
        return " AND ".join(where), args

    def search(self, now: datetime, filters: dict[str, Any], sort: str = "closing", limit: int = 25, offset: int = 0) -> tuple[list[dict], int]:
        where, args = self._where(now, filters)
        order = SORTS.get(sort, SORTS["closing"])
        # Unknown values and dates sort last whichever way round.
        order = f"({order.split()[0]} IS NULL), {order}"
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM seen WHERE {where}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT key, portal, tender_id, first_seen, emailed, closing, published, value, emd, category, location, organisation, title, description "
                f"FROM seen WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def categories(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT category FROM seen WHERE civil=1 AND category <> '' ORDER BY category")]

    def stats(self, now: datetime, soon_days: int = 3) -> dict[str, Any]:
        """Everything the dashboard shows, in one query pass."""
        open_where = "civil = 1 AND (closing IS NULL OR closing >= ?)"
        iso = now.isoformat()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        def one(sql: str, args: list) -> Any:
            return self.db.execute(sql, args).fetchone()[0]

        with self.lock:
            kpi = {
                "open": one(f"SELECT COUNT(*) FROM seen WHERE {open_where}", [iso]),
                "new_today": one("SELECT COUNT(*) FROM seen WHERE civil=1 AND first_seen >= ?", [_iso(day_start)]),
                "closing_soon": one("SELECT COUNT(*) FROM seen WHERE civil=1 AND closing >= ? AND closing <= ?", [iso, _iso(now + timedelta(days=soon_days))]),
                "open_value": one(f"SELECT COALESCE(SUM(value),0) FROM seen WHERE {open_where}", [iso]),
                "total_kept": one("SELECT COUNT(*) FROM seen WHERE civil=1", []),
            }
            by_portal = [dict(r) for r in self.db.execute(
                f"SELECT portal, COUNT(*) AS count, COALESCE(SUM(value),0) AS value FROM seen WHERE {open_where} GROUP BY portal ORDER BY count DESC", [iso])]
            by_category = [dict(r) for r in self.db.execute(
                f"SELECT CASE WHEN category='' OR category IS NULL THEN 'Not stated' ELSE category END AS category, COUNT(*) AS count "
                f"FROM seen WHERE {open_where} GROUP BY 1 ORDER BY count DESC LIMIT 12", [iso])]
            bands = self.db.execute(
                f"SELECT SUM(value IS NULL) AS unknown, SUM(value < 1e5) AS b1, SUM(value >= 1e5 AND value < 1e6) AS b2, "
                f"SUM(value >= 1e6 AND value < 1e7) AS b3, SUM(value >= 1e7 AND value < 1e8) AS b4, SUM(value >= 1e8) AS b5 "
                f"FROM seen WHERE {open_where}", [iso]).fetchone()
            by_value = [
                {"band": "Under 1 lakh", "count": bands["b1"] or 0}, {"band": "1 to 10 lakh", "count": bands["b2"] or 0},
                {"band": "10 lakh to 1 crore", "count": bands["b3"] or 0}, {"band": "1 to 10 crore", "count": bands["b4"] or 0},
                {"band": "Over 10 crore", "count": bands["b5"] or 0}, {"band": "Not stated", "count": bands["unknown"] or 0},
            ]
            departments = [dict(r) for r in self.db.execute(
                f"SELECT CASE WHEN instr(organisation,'||')>0 THEN substr(organisation,1,instr(organisation,'||')-1) ELSE organisation END AS department, "
                f"portal, COUNT(*) AS count, COALESCE(SUM(value),0) AS value FROM seen WHERE {open_where} "
                f"GROUP BY 1, 2 ORDER BY count DESC LIMIT 10", [iso])]
            per_day = {r["day"]: r["count"] for r in self.db.execute(
                "SELECT substr(first_seen,1,10) AS day, COUNT(*) AS count FROM seen WHERE civil=1 AND first_seen >= ? GROUP BY 1",
                [_iso(day_start - timedelta(days=29))])}
            closing_days = {r["day"]: r["count"] for r in self.db.execute(
                "SELECT substr(closing,1,10) AS day, COUNT(*) AS count FROM seen WHERE civil=1 AND closing >= ? AND closing < ? GROUP BY 1",
                [iso, _iso(day_start + timedelta(days=15))])}
            biggest = [dict(r) for r in self.db.execute(
                f"SELECT key, portal, tender_id, closing, value, category, organisation, title, description FROM seen WHERE {open_where} AND value IS NOT NULL "
                f"ORDER BY value DESC LIMIT 5", [iso])]
        days = [(day_start - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
        ahead = [(day_start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 14)]
        return {
            "kpi": kpi, "by_portal": by_portal, "by_category": by_category, "by_value": by_value, "departments": departments,
            "new_per_day": [{"day": d, "count": per_day.get(d, 0)} for d in days],
            "closing_per_day": [{"day": d, "count": closing_days.get(d, 0)} for d in ahead],
            "biggest": biggest,
        }

    # ---- runs -----------------------------------------------------------------------------------------------

    def create_run(self, trigger: str, portals: list[str], send_email: bool, now: datetime) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO runs (trigger, status, started, portals, send_email) VALUES (?, 'running', ?, ?, ?)",
                (trigger, _iso(now), json.dumps(portals), int(send_email)),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def update_run(self, run_id: int, **fields: Any) -> None:
        allowed = {"status", "finished", "reports", "new_civil", "emailed_count", "error", "log"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            sets.append(f"{k} = ?")
            args.append(json.dumps(v) if k in ("reports", "log") else v)
        with self.lock:
            self.db.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", [*args, run_id])
            self.db.commit()

    @staticmethod
    def _run(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["portals"] = json.loads(d["portals"] or "[]")
        d["reports"] = json.loads(d["reports"]) if d.get("reports") else []
        d["log"] = json.loads(d.get("log") or "[]")
        d["send_email"] = bool(d["send_email"])
        return d

    def get_run(self, run_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run(r) if r else None

    def list_runs(self, limit: int = 30) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = self._run(r)
            d.pop("log", None)  # the list stays small; open a run for its log
            out.append(d)
        return out

    def running_run(self) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()
        return self._run(r) if r else None

    def last_run_started(self, trigger: str) -> datetime | None:
        with self.lock:
            r = self.db.execute("SELECT started FROM runs WHERE trigger = ? ORDER BY id DESC LIMIT 1", (trigger,)).fetchone()
        return datetime.fromisoformat(r[0]) if r else None

    def fail_stale_runs(self, now: datetime) -> int:
        """A run still marked running when the service starts was killed by a restart. Say so instead of showing it forever."""
        with self.lock:
            cur = self.db.execute(
                "UPDATE runs SET status='failed', finished=?, error='Interrupted by a restart of the service' WHERE status='running'", (_iso(now),))
            self.db.commit()
            return cur.rowcount

    # ---- saved settings ---------------------------------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        with self.lock:
            return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT key, value FROM settings")}

    def put_settings(self, values: dict[str, Any]) -> None:
        with self.lock:
            for k, v in values.items():
                self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()
