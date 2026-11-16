"""API tokens: a way for another system (a spreadsheet script, an ERP, Zapier) to read from, or work in, the app without a person's password.

A token is shown once, when it is made. Only a hash is kept, so a copy of the database does not hold a working key. A token acts with the
role it was given (viewer or bidder, never admin), can be set to expire, and can be switched off at any time. Everything it does is in the
activity log under its own name.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime
from typing import Any

MAX_TOKENS = 25
ROLES = ("viewer", "bidder")
PREFIX = "tnd_"


class TokenError(ValueError):
    pass


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiTokens:
    def __init__(self, store: Any):
        self.db, self.lock = store.db, store.lock
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_tokens (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, hint TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
                  created_by TEXT, created TEXT NOT NULL, expires TEXT, last_used REAL, revoked INTEGER NOT NULL DEFAULT 0);
                """)
            self.db.commit()

    def create(self, name: str, role: str, days: int | None, by: str, now: datetime) -> tuple[dict, str]:
        """Returns (the token's record, the token itself). The token cannot be read again."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise TokenError("Name the token after what will use it, such as 'Finance spreadsheet'")
        if role not in ROLES:
            raise TokenError("A token can be view-only (viewer) or able to work on bids (bidder)")
        if days is not None and not 1 <= days <= 3650:
            raise TokenError("Choose a lifetime of 1 to 3650 days, or no expiry")
        with self.lock:
            if self.db.execute("SELECT COUNT(*) FROM api_tokens WHERE revoked = 0").fetchone()[0] >= MAX_TOKENS:
                raise TokenError(f"Up to {MAX_TOKENS} tokens can be active. Revoke one you no longer use")
            raw = PREFIX + secrets.token_urlsafe(32)
            expires = None
            if days:
                from datetime import timedelta
                expires = (now + timedelta(days=days)).isoformat(timespec="seconds")
            cur = self.db.execute("INSERT INTO api_tokens (name, hint, hash, role, created_by, created, expires) VALUES (?,?,?,?,?,?,?)",
                                  (name, raw[:8] + "…" + raw[-4:], _hash(raw), role, by[:80], now.isoformat(timespec="seconds"), expires))
            self.db.commit()
        return self.get(cur.lastrowid) or {}, raw

    def get(self, token_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT id, name, hint, role, created_by, created, expires, last_used, revoked FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
        return self._shape(r) if r else None

    @staticmethod
    def _shape(r: Any) -> dict:
        return {**dict(r), "revoked": bool(r["revoked"])}

    def list(self) -> list[dict]:
        with self.lock:
            return [self._shape(r) for r in self.db.execute("SELECT id, name, hint, role, created_by, created, expires, last_used, revoked FROM api_tokens ORDER BY revoked, id DESC")]

    def lookup(self, raw: str, now: datetime) -> dict | None:
        """The token's record when `raw` is a live token (not revoked, not expired), else None. Notes when it was last used (at most once a minute)."""
        if not raw.startswith(PREFIX) or len(raw) > 100:
            return None
        with self.lock:
            r = self.db.execute("SELECT id, name, role, expires, last_used, revoked FROM api_tokens WHERE hash = ?", (_hash(raw),)).fetchone()
            if not r or r["revoked"] or (r["expires"] and r["expires"] < now.isoformat(timespec="seconds")):
                return None
            t = time.time()
            if not r["last_used"] or t - r["last_used"] > 60:
                self.db.execute("UPDATE api_tokens SET last_used = ? WHERE id = ?", (t, r["id"]))
                self.db.commit()
        return {"id": r["id"], "name": r["name"], "role": r["role"]}

    def revoke(self, token_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("UPDATE api_tokens SET revoked = 1 WHERE id = ? AND revoked = 0", (token_id,))
            self.db.commit()
        return bool(cur.rowcount)
