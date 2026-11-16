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
    # Claude's labels and saved answers (empty until the AI features are used)
    "work_type": "TEXT", "fit": "INTEGER", "fit_reason": "TEXT", "ai_civil": "INTEGER", "ai_profile": "TEXT",
    "summary": "TEXT", "brief": "TEXT", "doc": "TEXT",
}

SORTS = {
    "closing": "closing ASC", "closing_desc": "closing DESC", "published": "published DESC",
    "value": "value DESC", "value_asc": "value ASC", "first_seen": "first_seen DESC", "fit": "fit DESC",
}


NONE = "__none__"  # filter value meaning "tenders where this is not stated"
DEPT_SQL = "(CASE WHEN instr(organisation,'||')>0 THEN substr(organisation,1,instr(organisation,'||')-1) ELSE organisation END)"
CATEGORY_SQL = "(CASE WHEN category IS NULL OR category = '' THEN 'Not stated' ELSE category END)"
LOCATION_SQL = "(CASE WHEN location IS NULL OR location = '' THEN 'Not stated' ELSE location END)"
DEPT_LABEL_SQL = "(CASE WHEN organisation IS NULL OR organisation = '' THEN 'Not stated' ELSE " + DEPT_SQL + " END)"
VALUE_BANDS = ["Under 1 lakh", "1 to 10 lakh", "10 lakh to 1 crore", "1 to 10 crore", "Over 10 crore", "Not stated"]
CLOSES_IN = ["Closed", "Within 24 hours", "1 to 3 days", "4 to 7 days", "8 to 14 days", "15 to 30 days", "Over 30 days", "No date"]

# key -> label and how its values are ordered: "size" (largest first), "first"/"last" (time order, keep the nearest/latest), or a fixed list.
DIMENSIONS: dict[str, dict[str, Any]] = {
    "portal": {"label": "Portal", "order": "size"},
    "category": {"label": "Category", "order": "size"},
    "department": {"label": "Department", "order": "size"},
    "location": {"label": "Location", "order": "size"},
    "work_type": {"label": "Work type", "order": "size"},
    "value_band": {"label": "Estimated value", "order": VALUE_BANDS},
    "closes_in": {"label": "Closes in", "order": CLOSES_IN},
    "closing_week": {"label": "Closing week", "order": "first"},
    "closing_day": {"label": "Closing day", "order": "first"},
    "first_seen_day": {"label": "Day found", "order": "last"},
    "published_day": {"label": "Day published", "order": "last"},
}


def _dim_sql(dim: str, now: datetime) -> tuple[str, list[Any]]:
    if dim == "portal":
        return "COALESCE(portal, 'Not stated')", []
    if dim == "category":
        return CATEGORY_SQL, []
    if dim == "department":
        return DEPT_LABEL_SQL, []
    if dim == "location":
        return LOCATION_SQL, []
    if dim == "work_type":
        return "(CASE WHEN work_type IS NULL OR work_type = '' THEN 'Not tagged' ELSE work_type END)", []
    if dim == "value_band":
        return ("CASE WHEN value IS NULL THEN 'Not stated' WHEN value < 1e5 THEN 'Under 1 lakh' WHEN value < 1e6 THEN '1 to 10 lakh' "
                "WHEN value < 1e7 THEN '10 lakh to 1 crore' WHEN value < 1e8 THEN '1 to 10 crore' ELSE 'Over 10 crore' END"), []
    if dim == "closes_in":
        d = "(julianday(closing) - julianday(?))"
        return (f"CASE WHEN closing IS NULL THEN 'No date' WHEN closing < ? THEN 'Closed' WHEN {d} < 1 THEN 'Within 24 hours' WHEN {d} < 4 THEN '1 to 3 days' "
                f"WHEN {d} < 8 THEN '4 to 7 days' WHEN {d} < 15 THEN '8 to 14 days' WHEN {d} < 31 THEN '15 to 30 days' ELSE 'Over 30 days' END"), [now.isoformat()] * 6
    if dim == "closing_week":
        return "COALESCE(date(closing, 'weekday 0', '-6 days'), 'No date')", []  # the Monday of that week
    if dim == "closing_day":
        return "COALESCE(substr(closing,1,10), 'No date')", []
    if dim == "first_seen_day":
        return "substr(first_seen,1,10)", []
    if dim == "published_day":
        return "COALESCE(substr(published,1,10), 'No date')", []
    raise ValueError(dim)


def drill(dim: str, key: str, now: datetime) -> dict[str, Any]:
    """The filters that select the tenders behind one value of a dimension."""
    if key in ("", "Other"):
        return {}
    none = key in ("Not stated", "No date")
    if dim == "portal":
        return {"portal": [key]}
    if dim in ("category", "department", "location"):
        return {dim: [NONE if none else key]}
    if dim == "work_type":
        return {dim: [NONE if key == "Not tagged" else key]}
    if dim == "value_band":
        lo_hi = {"Under 1 lakh": (None, 1e5), "1 to 10 lakh": (1e5, 1e6), "10 lakh to 1 crore": (1e6, 1e7), "1 to 10 crore": (1e7, 1e8), "Over 10 crore": (1e8, None)}
        if none:
            return {"no_value": True}
        lo, hi = lo_hi[key]
        return {**({"min_value": lo} if lo is not None else {}), **({"value_lt": hi} if hi is not None else {})}
    if dim == "closes_in":
        edge = lambda days: (now + timedelta(days=days)).isoformat()  # noqa: E731
        n = now.isoformat()
        return {
            "Closed": {"status": "closed"}, "No date": {"no_closing": True},
            "Within 24 hours": {"closing_from": n, "closing_to": edge(1)}, "1 to 3 days": {"closing_from": edge(1), "closing_to": edge(4)},
            "4 to 7 days": {"closing_from": edge(4), "closing_to": edge(8)}, "8 to 14 days": {"closing_from": edge(8), "closing_to": edge(15)},
            "15 to 30 days": {"closing_from": edge(15), "closing_to": edge(31)}, "Over 30 days": {"closing_from": edge(31)},
        }.get(key, {})
    if dim in ("closing_day", "closing_week", "first_seen_day", "published_day"):
        if none:
            return {"no_closing": True} if dim.startswith("closing") else {"no_published": True}
        col = {"closing_day": "closing", "closing_week": "closing", "first_seen_day": "seen", "published_day": "published"}[dim]
        start = datetime.fromisoformat(key)
        end = start + timedelta(days=7 if dim == "closing_week" else 1)
        return {f"{col}_from": start.isoformat(), f"{col}_to": end.isoformat()}
    return {}


def merge_drills(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Both filters at once: the tenders that match the row AND the column. Ranges narrow, they never overwrite."""
    out = dict(a)
    for k, v in b.items():
        if k not in out:
            out[k] = v
        elif k.endswith("_from") or k == "min_value":
            out[k] = max(out[k], v)
        elif k.endswith("_to") or k == "value_lt":
            out[k] = min(out[k], v)
        elif k == "status":
            out[k] = v if out[k] == "all" else out[k]
        elif isinstance(v, list):
            out[k] = [x for x in out[k] if x in v]
    return out


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
                CREATE TABLE IF NOT EXISTS starred (key TEXT PRIMARY KEY, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_fails (at REAL NOT NULL, client TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, created REAL NOT NULL, last_seen REAL NOT NULL, user_id INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS portal_status (
                  key TEXT PRIMARY KEY, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', checked TEXT NOT NULL,
                  departments INTEGER NOT NULL DEFAULT 0, tenders INTEGER NOT NULL DEFAULT 0);
                """
            )
            if "user_id" not in {r["name"] for r in self.db.execute("PRAGMA table_info(sessions)")}:
                self.db.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")  # sessions made before accounts existed belong to the owner (0)
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

    def tender_object(self, key: str) -> Tender | None:
        with self.lock:
            r = self.db.execute("SELECT payload FROM seen WHERE key = ?", (key,)).fetchone()
        return _from_json(r[0]) if r and r[0] else None

    def find_by_tender_id(self, tender_id: str) -> list[dict]:
        """Tenders already held under this Tender ID, from any portal (used to stop a hand-added tender duplicating a collected one)."""
        with self.lock:
            rows = self.db.execute("SELECT key, portal, organisation FROM seen WHERE civil = 1 AND lower(tender_id) = lower(?)", (tender_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_tender(self, key: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM starred WHERE key = ?", (key,))
            self.db.execute("DELETE FROM seen WHERE key = ?", (key,))
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
    def _like(text: str) -> str:
        """A search term is data: % and _ are matched literally."""
        return "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    @staticmethod
    def _bound(text: str, upper: bool) -> tuple[str, str]:
        """A date ('2026-11-20': the whole day, end included) or a date-time (start included, end excluded). Returns (operator, value)."""
        if len(text) == 10:
            if upper:
                return "<", (datetime.fromisoformat(text) + timedelta(days=1)).isoformat()
            return ">=", text
        return ("<" if upper else ">="), text

    @classmethod
    def _where(cls, now: datetime, f: dict[str, Any]) -> tuple[str, list[Any]]:
        where, args = ["civil = 1"], []  # type: list[str], list[Any]

        def any_of(sql: str, values: Any, none_sql: str) -> None:
            vals = [values] if isinstance(values, str) else [v for v in (values or []) if v is not None]
            if not vals:
                return
            parts, real = [], [v for v in vals if v != NONE]
            if real:
                parts.append(f"{sql} IN ({','.join('?' * len(real))})")
                args.extend(real)
            if NONE in vals:
                parts.append(none_sql)
            where.append("(" + " OR ".join(parts) + ")")

        any_of("portal", f.get("portal"), "portal IS NULL")
        any_of("category", f.get("category"), "(category IS NULL OR category = '')")
        any_of(DEPT_SQL, f.get("department"), f"({DEPT_SQL} IS NULL OR {DEPT_SQL} = '')")
        any_of("location", f.get("location"), "(location IS NULL OR location = '')")
        any_of("work_type", f.get("work_type"), "(work_type IS NULL OR work_type = '')")
        if f.get("min_fit") is not None:
            where.append("fit >= ?")
            args.append(int(f["min_fit"]))

        if f.get("q"):
            like = cls._like(str(f["q"]))
            where.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in ("title", "description", "organisation", "tender_id", "location", "category")) + ")")
            args += [like] * 6
        if f.get("min_value") is not None:
            where.append("value >= ?")
            args.append(float(f["min_value"]))
        if f.get("max_value") is not None:
            where.append("value <= ?")
            args.append(float(f["max_value"]))
        if f.get("value_lt") is not None:
            where.append("value < ?")
            args.append(float(f["value_lt"]))
        if f.get("no_value"):
            where.append("value IS NULL")
        if f.get("no_closing"):
            where.append("closing IS NULL")
        if f.get("no_published"):
            where.append("published IS NULL")
        if f.get("starred"):
            where.append("key IN (SELECT key FROM starred)")
        for col, lo, hi in (("closing", "closing_from", "closing_to"), ("first_seen", "seen_from", "seen_to"), ("published", "published_from", "published_to")):
            if f.get(lo):
                op, val = cls._bound(str(f[lo]), False)
                where.append(f"{col} {op} ?")
                args.append(val)
            if f.get(hi):
                op, val = cls._bound(str(f[hi]), True)
                where.append(f"{col} {op} ?")
                args.append(val)

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
        if f.get("closing_within"):
            where.append("closing >= ? AND closing <= ?")
            args += [now.isoformat(), (now + timedelta(days=int(f["closing_within"]))).isoformat()]
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
                f"SELECT key, portal, tender_id, first_seen, emailed, closing, published, value, emd, category, location, organisation, title, description, work_type, fit, fit_reason, ai_civil, "
                f"(key IN (SELECT key FROM starred)) AS starred FROM seen WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["starred"] = bool(r["starred"])
        return out, total

    def get_tender(self, key: str) -> dict | None:
        with self.lock:
            r = self.db.execute(
                "SELECT key, portal, tender_id, first_seen, emailed, closing, published, value, emd, category, location, organisation, title, description, payload, work_type, fit, fit_reason, ai_civil, summary, brief, doc, "
                "(key IN (SELECT key FROM starred)) AS starred FROM seen WHERE key = ? AND civil = 1", (key,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["starred"] = bool(d["starred"])
        d["opening"] = json.loads(d.pop("payload") or "{}").get("opening")
        return d

    def categories(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT category FROM seen WHERE civil=1 AND category <> '' ORDER BY category")]

    def facets(self, now: datetime, limit: int = 60) -> dict[str, list[dict]]:
        """Values to offer in filters, with how many open tenders each has."""
        where, args = self._where(now, {"status": "open"})
        out: dict[str, list[dict]] = {}
        with self.lock:
            for name, expr in (("portal", "portal"), ("category", "category"), ("department", DEPT_SQL), ("location", "location"), ("work_type", "work_type")):
                rows = self.db.execute(
                    f"SELECT {expr} AS value, COUNT(*) AS count FROM seen WHERE {where} AND {expr} IS NOT NULL AND {expr} <> '' "
                    f"GROUP BY 1 ORDER BY count DESC, value LIMIT ?", [*args, limit]).fetchall()
                out[name] = [dict(r) for r in rows]
        return out

    # ---- Claude's labels and saved answers -------------------------------------------------------------------

    def ai_pending(self, now: datetime, profile_hash: str, limit: int) -> list[dict]:
        """Open tenders with no label yet, or labelled against an older company profile. Newest first."""
        with self.lock:
            rows = self.db.execute(
                "SELECT key, title, description, organisation, category, location, portal, value, emd, closing, published FROM seen "
                "WHERE civil = 1 AND (closing IS NULL OR closing >= ?) AND (ai_profile IS NULL OR ai_profile <> ?) ORDER BY first_seen DESC LIMIT ?",
                (now.isoformat(), profile_hash, limit)).fetchall()
        return [dict(r) for r in rows]

    def ai_save(self, labels: list[tuple[str, dict]], profile_hash: str, hide_noncivil: bool) -> None:
        with self.lock:
            for key, lab in labels:
                self.db.execute(
                    "UPDATE seen SET work_type=?, fit=?, fit_reason=?, ai_civil=?, ai_profile=? WHERE key=?",
                    (lab["work_type"], lab["fit"], lab["reason"], 1 if lab["civil"] else 0, profile_hash, key))
                if hide_noncivil and not lab["civil"]:
                    self.db.execute("UPDATE seen SET civil = 0 WHERE key = ?", (key,))
            self.db.commit()

    def ai_counts(self, now: datetime) -> dict[str, int]:
        with self.lock:
            r = self.db.execute(
                "SELECT SUM(ai_profile IS NOT NULL), SUM(ai_profile IS NULL) FROM seen WHERE civil = 1 AND (closing IS NULL OR closing >= ?)", (now.isoformat(),)).fetchone()
        return {"labelled": r[0] or 0, "unlabelled": r[1] or 0}

    def ai_usage_today(self, day: str) -> int:
        with self.lock:
            r = self.db.execute("SELECT calls FROM ai_usage WHERE day = ?", (day,)).fetchone()
        return r["calls"] if r else 0

    def ai_usage_add(self, day: str, n: int = 1) -> int:
        with self.lock:
            self.db.execute("INSERT INTO ai_usage (day, calls) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET calls = calls + excluded.calls", (day, n))
            self.db.execute("DELETE FROM ai_usage WHERE day < date(?, '-60 day')", (day,))
            self.db.commit()
        return self.ai_usage_today(day)

    def ai_info(self, keys: list[str]) -> dict[str, dict]:
        if not keys:
            return {}
        with self.lock:
            rows = self.db.execute(f"SELECT key, work_type, fit, fit_reason FROM seen WHERE key IN ({','.join('?' * len(keys))})", keys).fetchall()
        return {r["key"]: dict(r) for r in rows}

    def set_ai_text(self, key: str, field: str, value: str | None) -> None:
        if field not in ("summary", "brief", "doc"):
            raise ValueError(field)
        with self.lock:
            self.db.execute(f"UPDATE seen SET {field} = ? WHERE key = ?", (value, key))
            self.db.commit()

    def related(self, key: str, limit: int = 4) -> list[dict]:
        """Other tenders that look like the same work: same department, near-identical wording, different tender ID."""
        import difflib
        import re

        stop = {"tender", "no", "nit", "rfp", "the", "of", "for", "and", "at", "in", "e"}

        def norm(t: dict) -> str:
            words = re.sub(r"\d+|[^a-z ]+", " ", (t.get("description") or t.get("title") or "").lower()).split()
            return " ".join(w for w in words if w not in stop)

        with self.lock:
            me = self.db.execute("SELECT key, tender_id, title, description, organisation FROM seen WHERE key = ?", (key,)).fetchone()
            if not me:
                return []
            dept = (me["organisation"] or "").split("||")[0]
            rows = self.db.execute(
                "SELECT key, tender_id, title, description, closing, first_seen, value, organisation FROM seen "
                "WHERE civil = 1 AND key <> ? AND organisation LIKE ? ORDER BY first_seen DESC LIMIT 400", (key, dept + "%")).fetchall() if dept else []
        mine = norm(dict(me))
        if len(mine) < 20:
            return []
        out = []
        for r in rows:
            d = dict(r)
            if d["tender_id"] == me["tender_id"]:
                continue
            other = norm(d)
            if len(other) < 20:
                continue
            m = difflib.SequenceMatcher(None, mine, other)
            if m.real_quick_ratio() >= 0.85 and m.quick_ratio() >= 0.85 and m.ratio() >= 0.88:
                out.append({"key": d["key"], "tender_id": d["tender_id"], "title": d["description"] or d["title"], "closing": d["closing"],
                            "first_seen": d["first_seen"], "value": d["value"], "similarity": round(m.ratio(), 2)})
        out.sort(key=lambda x: -x["similarity"])
        return out[:limit]

    # ---- starred ----------------------------------------------------------------------------------------------

    def set_starred(self, key: str, starred: bool, now: datetime) -> bool:
        with self.lock:
            if not self.db.execute("SELECT 1 FROM seen WHERE key = ? AND civil = 1", (key,)).fetchone():
                return False
            if starred:
                self.db.execute("INSERT OR IGNORE INTO starred (key, created) VALUES (?, ?)", (key, _iso(now)))
            else:
                self.db.execute("DELETE FROM starred WHERE key = ?", (key,))
            self.db.commit()
            return True

    # ---- analytics --------------------------------------------------------------------------------------------

    def stats(self, now: datetime, soon_days: int = 3, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Everything the dashboard shows. `filters` narrows all of it; status is decided per figure, so it is ignored."""
        base = {k: v for k, v in (filters or {}).items() if k not in ("status", "since_days", "closing_within")}
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        def W(**override: Any) -> tuple[str, list[Any]]:
            return self._where(now, {**base, "soon_days": soon_days, **override})

        def one(sql: str, args: list) -> Any:
            return self.db.execute(sql, args).fetchone()[0]

        ow, oa = W(status="open")
        aw, aa = W(status="all")
        with self.lock:
            kpi = {
                "open": one(f"SELECT COUNT(*) FROM seen WHERE {ow}", oa),
                "new_today": one(f"SELECT COUNT(*) FROM seen WHERE {aw} AND first_seen >= ?", [*aa, _iso(day_start)]),
                "new_yesterday": one(f"SELECT COUNT(*) FROM seen WHERE {aw} AND first_seen >= ? AND first_seen < ?", [*aa, _iso(day_start - timedelta(days=1)), _iso(day_start)]),
                "closing_soon": one(f"SELECT COUNT(*) FROM seen WHERE {W(status='closing_soon')[0]}", W(status="closing_soon")[1]),
                "closing_week": one(f"SELECT COUNT(*) FROM seen WHERE {W(closing_within=7)[0]}", W(closing_within=7)[1]),
                "open_value": one(f"SELECT COALESCE(SUM(value),0) FROM seen WHERE {ow}", oa),
                "avg_value": one(f"SELECT COALESCE(AVG(value),0) FROM seen WHERE {ow}", oa),
                "starred": one(f"SELECT COUNT(*) FROM seen WHERE {ow} AND key IN (SELECT key FROM starred)", oa),
                "total_kept": one(f"SELECT COUNT(*) FROM seen WHERE {aw}", aa),
            }
            by_portal = [dict(r) for r in self.db.execute(
                f"SELECT portal, COUNT(*) AS count, COALESCE(SUM(value),0) AS value FROM seen WHERE {ow} GROUP BY portal ORDER BY count DESC", oa)]
            by_category = [dict(r) for r in self.db.execute(
                f"SELECT {CATEGORY_SQL} AS category, COUNT(*) AS count FROM seen WHERE {ow} GROUP BY 1 ORDER BY count DESC LIMIT 12", oa)]
            bands = self.db.execute(
                f"SELECT SUM(value IS NULL) AS unknown, SUM(value < 1e5) AS b1, SUM(value >= 1e5 AND value < 1e6) AS b2, "
                f"SUM(value >= 1e6 AND value < 1e7) AS b3, SUM(value >= 1e7 AND value < 1e8) AS b4, SUM(value >= 1e8) AS b5 FROM seen WHERE {ow}", oa).fetchone()
            by_value = [
                {"band": "Under 1 lakh", "count": bands["b1"] or 0}, {"band": "1 to 10 lakh", "count": bands["b2"] or 0},
                {"band": "10 lakh to 1 crore", "count": bands["b3"] or 0}, {"band": "1 to 10 crore", "count": bands["b4"] or 0},
                {"band": "Over 10 crore", "count": bands["b5"] or 0}, {"band": "Not stated", "count": bands["unknown"] or 0},
            ]
            departments = [dict(r) for r in self.db.execute(
                f"SELECT {DEPT_SQL} AS department, portal, COUNT(*) AS count, COALESCE(SUM(value),0) AS value FROM seen WHERE {ow} "
                f"GROUP BY 1, 2 ORDER BY count DESC LIMIT 10", oa)]
            sw, sa = W(status="all", seen_from=_iso(day_start - timedelta(days=29)))
            per_day = {r["day"]: r["count"] for r in self.db.execute(f"SELECT substr(first_seen,1,10) AS day, COUNT(*) AS count FROM seen WHERE {sw} GROUP BY 1", sa)}
            cw, ca = W(status="open", closing_to=_iso(day_start + timedelta(days=13))[:10])
            closing_days = {r["day"]: r["count"] for r in self.db.execute(f"SELECT substr(closing,1,10) AS day, COUNT(*) AS count FROM seen WHERE {cw} AND closing IS NOT NULL GROUP BY 1", ca)}
            biggest = [dict(r) for r in self.db.execute(
                f"SELECT key, portal, tender_id, closing, value, category, organisation, title, description FROM seen WHERE {ow} AND value IS NOT NULL ORDER BY value DESC LIMIT 5", oa)]
        days = [(day_start - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
        ahead = [(day_start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 14)]
        return {
            "kpi": kpi, "by_portal": by_portal, "by_category": by_category, "by_value": by_value, "departments": departments,
            "new_per_day": [{"day": d, "count": per_day.get(d, 0)} for d in days],
            "closing_per_day": [{"day": d, "count": closing_days.get(d, 0)} for d in ahead],
            "biggest": biggest,
        }

    def pivot(self, now: datetime, filters: dict[str, Any], group_by: str, split_by: str | None = None, top: int = 12, top_split: int = 8) -> dict[str, Any]:
        """Counts and value totals grouped by one dimension, optionally split by a second one.

        Returns rows (largest first, or in time order for dates) with a `drill` filter set that selects exactly the tenders
        behind each row or cell, so the page can open them.
        """
        if group_by not in DIMENSIONS or (split_by and split_by not in DIMENSIONS):
            raise ValueError("unknown dimension")
        gsql, gargs = _dim_sql(group_by, now)
        ssql, sargs = _dim_sql(split_by, now) if split_by else ("''", [])
        where, args = self._where(now, filters)
        with self.lock:
            rows = self.db.execute(
                f"SELECT {gsql} AS g, {ssql} AS s, COUNT(*) AS c, COALESCE(SUM(value),0) AS v, SUM(value IS NOT NULL) AS vn FROM seen WHERE {where} GROUP BY 1, 2",
                [*gargs, *sargs, *args]).fetchall()

        cells: dict[tuple[str, str], tuple[int, float, int]] = {(r["g"], r["s"]): (r["c"], r["v"], r["vn"]) for r in rows}
        gtot: dict[str, list[float]] = {}
        stot: dict[str, list[float]] = {}
        for (g, s), (c, v, vn) in cells.items():
            for tot, k in ((gtot, g), (stot, s)):
                t = tot.setdefault(k, [0, 0.0, 0])
                t[0] += c; t[1] += v; t[2] += vn

        def order(dim: str, keys: list[str], totals: dict[str, list[float]], limit: int) -> tuple[list[str], bool]:
            kind = DIMENSIONS[dim]["order"]
            if isinstance(kind, list):  # a fixed order
                return [k for k in kind if k in totals], False
            if kind in ("first", "last"):
                chron = sorted(keys)
                return (chron[:limit * 5] if kind == "first" else chron[-limit * 5:]), False
            ranked = sorted(keys, key=lambda k: (k == "Not stated", -totals[k][0], k))
            return ranked[:limit], len(ranked) > limit

        gkeys, g_more = order(group_by, list(gtot), gtot, top)
        skeys, s_more = order(split_by, list(stot), stot, top_split) if split_by else ([""], False)
        gset, sset = set(gkeys), set(skeys)

        def cell(c: int, v: float, vn: int) -> dict:
            return {"count": c, "value": v, "avg": (v / vn) if vn else None}

        out_rows = []
        for g in gkeys:
            c, v, vn = gtot[g]
            row = {"key": g, "drill": drill(group_by, g, now), **cell(c, v, vn), "parts": {}}
            if split_by:
                for sk in skeys:
                    if (g, sk) in cells:
                        pc, pv, pvn = cells[(g, sk)]
                        row["parts"][sk] = {**cell(pc, pv, pvn), "drill": merge_drills(drill(group_by, g, now), drill(split_by, sk, now))}
                other = [x for (gg, ss), x in cells.items() if gg == g and ss not in sset]
                if other:
                    row["parts"]["Other"] = cell(sum(x[0] for x in other), sum(x[1] for x in other), sum(x[2] for x in other))
            out_rows.append(row)
        if g_more:
            rest = [t for k, t in gtot.items() if k not in gset]
            out_rows.append({"key": "Other", "drill": None, **cell(sum(t[0] for t in rest), sum(t[1] for t in rest), sum(t[2] for t in rest)), "parts": {}})
        total_c = sum(t[0] for t in gtot.values())
        total_v = sum(t[1] for t in gtot.values())
        return {
            "group_by": group_by, "split_by": split_by, "rows": out_rows,
            "splits": (skeys + (["Other"] if s_more else [])) if split_by else [],
            "total": {"count": total_c, "value": total_v},
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

    def scheduled_runs_on(self, day_iso: str) -> list[dict]:
        """The daily runs that started on this local day (YYYY-MM-DD), oldest first, so the scheduler can retry a failed one."""
        with self.lock:
            rows = self.db.execute("SELECT id, status, started, finished, error FROM runs WHERE trigger = 'scheduled' AND substr(started, 1, 10) = ? ORDER BY id",
                                   (day_iso,)).fetchall()
        return [dict(r) for r in rows]

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

    # ---- login sessions: kept here so that logging out, or going idle, really ends them ------------------------

    def session_start(self, sid: str, now: float, keep_seconds: float, user_id: int = 0) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE created < ?", (now - keep_seconds,))
            self.db.execute("INSERT OR REPLACE INTO sessions (sid, created, last_seen, user_id) VALUES (?, ?, ?, ?)", (sid, now, now, user_id))
            self.db.commit()

    def session_touch(self, sid: str, now: float, idle_seconds: float) -> int | None:
        """The session's user id (0 = the owner login) when it exists and was used within `idle_seconds`, else None.
        Records the use (not more than about once a minute)."""
        with self.lock:
            row = self.db.execute("SELECT last_seen, user_id FROM sessions WHERE sid = ?", (sid,)).fetchone()
            if not row or now - row["last_seen"] > idle_seconds:
                if row:
                    self.db.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
                    self.db.commit()
                return None
            if now - row["last_seen"] > min(60, idle_seconds / 4):
                self.db.execute("UPDATE sessions SET last_seen = ? WHERE sid = ?", (now, sid))
                self.db.commit()
            return int(row["user_id"])

    def sessions_end_for(self, user_id: int, except_sid: str | None = None) -> None:
        """Signs a user out everywhere (a password reset, a deactivation), optionally keeping the session making the change."""
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE user_id = ? AND sid != ?", (user_id, except_sid or ""))
            self.db.commit()

    def session_end(self, sid: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
            self.db.commit()

    # ---- failed sign-ins, kept so a restart does not give a guesser a fresh start ---------------------------------

    def login_fails_recent(self, since: float) -> list[tuple[float, str]]:
        with self.lock:
            self.db.execute("DELETE FROM login_fails WHERE at < ?", (since,))
            self.db.commit()
            return [(r["at"], r["client"]) for r in self.db.execute("SELECT at, client FROM login_fails ORDER BY at")]

    def login_fail_add(self, client: str, at: float) -> None:
        with self.lock:
            self.db.execute("INSERT INTO login_fails (at, client) VALUES (?, ?)", (at, client[:64]))
            self.db.commit()

    def login_fails_clear(self, client: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM login_fails WHERE client = ?", (client,))
            self.db.commit()

    def ping(self) -> None:
        """Raises when the database cannot be read."""
        with self.lock:
            self.db.execute("SELECT 1 FROM settings LIMIT 1").fetchone()

    def get_settings(self) -> dict[str, Any]:
        with self.lock:
            return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT key, value FROM settings")}

    def put_settings(self, values: dict[str, Any]) -> None:
        with self.lock:
            for k, v in values.items():
                self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))
            self.db.commit()

    # ---- can the app read each portal ---------------------------------------------------------------------------

    def put_portal_status(self, key: str, kind: str, detail: str, checked: datetime, departments: int = 0, tenders: int = 0) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO portal_status (key, kind, detail, checked, departments, tenders) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, detail=excluded.detail, checked=excluded.checked, "
                "departments=excluded.departments, tenders=excluded.tenders",
                (key, kind, detail[:300], _iso(checked), departments, tenders),
            )
            self.db.commit()

    def portal_statuses(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {r["key"]: dict(r) for r in self.db.execute("SELECT key, kind, detail, checked, departments, tenders FROM portal_status")}

    def delete_portal_status(self, key: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM portal_status WHERE key = ?", (key,))
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()
