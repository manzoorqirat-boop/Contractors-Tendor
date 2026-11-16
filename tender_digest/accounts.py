"""People who use the app: accounts, roles, passwords and the activity log.

Roles, from least to most:
  viewer  can look at everything (tenders, bids, charts) and change nothing;
  bidder  can also work: star, start and edit bids, use the Vault, read documents with AI, add tenders, run a collection;
  admin   can also change settings, portals, people and backups, and read the activity log.

The login set in the server variables (APP_USERNAME / APP_PASSWORD) is the "owner": always an admin, always able to sign in, and
the way back in if every other account is lost. It is not stored in the database and cannot be edited here.

Passwords are stored as salted scrypt hashes (standard library, nothing to install).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

ROLES = ("viewer", "bidder", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
ROLE_TEXT = {
    "viewer": "Can look at everything, can change nothing",
    "bidder": "Can work on tenders and bids, use the Vault and the AI assistant",
    "admin": "Can also change settings, portals, people and backups",
}
OWNER_ID = 0
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
MIN_PASSWORD = 12
_COMMON = {"password1234", "123456789012", "qwertyuiop12", "administrator", "letmein12345", "welcome12345", "iloveyou1234", "passw0rd1234", "tendai123456"}
MAX_AUDIT_ROWS = 20_000


@dataclass
class Principal:
    id: int
    username: str
    name: str
    role: str
    must_change: bool = False

    @property
    def owner(self) -> bool:
        return self.id == OWNER_ID

    def can(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "name": self.name, "role": self.role, "owner": self.owner, "must_change": self.must_change}


# ---- what each kind of request needs ---------------------------------------------------------------------------------

_ADMIN_AREAS = re.compile(r"^/api/(users|audit|backup|settings)(/|$)")
_VAULT_FILE = re.compile(r"^/api/vault/\d+/file$")


def required_role(method: str, path: str) -> str:
    """The least role allowed to make this request. One table for the whole app, so it can be read, and tested, in one place."""
    if path == "/api/account/password":
        return "viewer"  # everyone may change their own password
    if _ADMIN_AREAS.match(path):
        return "admin"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "bidder" if _VAULT_FILE.match(path) else "viewer"  # company papers are not for view-only accounts
    if path.startswith("/api/portals"):
        return "admin"
    return "bidder"


# ---- passwords -------------------------------------------------------------------------------------------------------

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("not-a-real-account")  # checked when the username is unknown, so a wrong name takes as long as a wrong password


def password_problem(password: str, username: str = "") -> str:
    """Why a new password is not acceptable, in words for the person choosing it. Empty when it is fine."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters. A few random words joined together is easy to remember and hard to guess"
    if len(password) > 200:
        return "That password is too long (200 characters at most)"
    if len(set(password)) < 5:
        return "That password repeats the same few characters. Choose something less predictable"
    if password.lower() in _COMMON:
        return "That is one of the most common passwords. Choose something else"
    if username and username.lower() in password.lower():
        return "The password cannot contain the username"
    return ""


# ---- the stored accounts and the activity log ----------------------------------------------------------------------------

class AccountError(Exception):
    pass


class Accounts:
    def __init__(self, store: Any):
        self.db, self.lock = store.db, store.lock
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'viewer',
                  pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, last_login TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, username TEXT NOT NULL, role TEXT, action TEXT NOT NULL, target TEXT, detail TEXT, client TEXT);
                CREATE INDEX IF NOT EXISTS audit_at ON audit (id DESC);
                """)
            self.db.commit()
        self._audit_writes = 0
        self._guard = threading.Lock()

    # -- people --

    def _principal(self, r: Any) -> Principal:
        return Principal(int(r["id"]), r["username"], r["name"] or r["username"], r["role"], bool(r["must_change"]))

    def get(self, user_id: int) -> Principal | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
        return self._principal(r) if r else None

    def authenticate(self, username: str, password: str) -> Principal | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username.strip().lower(),)).fetchone()
        ok = verify_password(password, r["pw_hash"] if r else _DUMMY_HASH)
        return self._principal(r) if (r and ok) else None

    def record_login(self, user_id: int, now: datetime) -> None:
        with self.lock:
            self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now.isoformat(timespec="seconds"), user_id))
            self.db.commit()

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT id, username, name, role, active, must_change, created, last_login FROM users ORDER BY active DESC, username").fetchall()
        return [{**dict(r), "active": bool(r["active"]), "must_change": bool(r["must_change"])} for r in rows]

    def exists(self, username: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM users WHERE username = ?", (username.strip().lower(),)).fetchone() is not None

    def create(self, username: str, name: str, role: str, password: str, now: datetime, reserved: str = "") -> dict[str, Any]:
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise AccountError("The username must be 3 to 40 characters: letters, numbers, dot, dash or underscore, starting with a letter or number")
        if reserved.strip() and username == reserved.strip().lower():
            raise AccountError("That username is reserved for the server's own login. Choose another")
        if role not in ROLES:
            raise AccountError("Choose a role: viewer, bidder or admin")
        problem = password_problem(password, username)
        if problem:
            raise AccountError(problem)
        if self.exists(username):
            raise AccountError("That username is already taken")
        with self.lock:
            cur = self.db.execute("INSERT INTO users (username, name, role, pw_hash, active, must_change, created) VALUES (?,?,?,?,1,1,?)",
                                  (username, name.strip()[:80], role, hash_password(password), now.isoformat(timespec="seconds")))
            self.db.commit()
        return next(u for u in self.list() if u["id"] == cur.lastrowid)

    def update(self, user_id: int, *, name: str | None = None, role: str | None = None, active: bool | None = None, password: str | None = None) -> dict[str, Any]:
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not r:
            raise AccountError("No such person")
        sets: dict[str, Any] = {}
        if name is not None:
            sets["name"] = name.strip()[:80]
        if role is not None:
            if role not in ROLES:
                raise AccountError("Choose a role: viewer, bidder or admin")
            sets["role"] = role
        if active is not None:
            sets["active"] = 1 if active else 0
        if password is not None:
            problem = password_problem(password, r["username"])
            if problem:
                raise AccountError(problem)
            sets.update(pw_hash=hash_password(password), must_change=1)  # a password set by someone else must be replaced by its owner
        if not sets:
            raise AccountError("Nothing to change")
        with self.lock:
            self.db.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), user_id])
            self.db.commit()
        return next(u for u in self.list() if u["id"] == user_id)

    def change_own_password(self, user_id: int, current: str, new: str) -> None:
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
        if not r or not verify_password(current, r["pw_hash"]):
            raise AccountError("The current password is not right")
        if verify_password(new, r["pw_hash"]):
            raise AccountError("The new password must be different from the current one")
        problem = password_problem(new, r["username"])
        if problem:
            raise AccountError(problem)
        with self.lock:
            self.db.execute("UPDATE users SET pw_hash = ?, must_change = 0 WHERE id = ?", (hash_password(new), user_id))
            self.db.commit()

    # -- activity log --

    def log(self, when: datetime, username: str, role: str | None, action: str, target: str = "", detail: str = "", client: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT INTO audit (at, username, role, action, target, detail, client) VALUES (?,?,?,?,?,?,?)",
                            (when.isoformat(timespec="seconds"), username[:80], role, action[:120], target[:200], detail[:300], client[:64]))
            self._audit_writes += 1
            if self._audit_writes % 200 == 0:  # keep the table from growing without end
                self.db.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - ?", (MAX_AUDIT_ROWS,))
            self.db.commit()

    def activity(self, limit: int = 100, username: str = "", before: int | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT id, at, username, role, action, target, detail, client FROM audit", []
        where = []
        if username:
            where.append("username = ?")
            args.append(username.strip().lower())
        if before:
            where.append("id < ?")
            args.append(before)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, [*args, max(1, min(limit, 500))]).fetchall()]


# ---- plain-words names for what was done, for the activity log -------------------------------------------------------------

ACTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/runs"): "Started a collection run",
    ("POST", "/api/tenders/star"): "Starred or unstarred a tender",
    ("POST", "/api/tenders/summary"): "Asked AI to summarise a tender",
    ("POST", "/api/tenders/brief"): "Asked AI for a bid / no-bid brief",
    ("POST", "/api/tenders/document"): "Read a tender document with AI",
    ("POST", "/api/tenders/manual"): "Added a tender by hand",
    ("PATCH", "/api/tenders/manual"): "Edited a hand-added tender",
    ("DELETE", "/api/tenders/manual"): "Removed a hand-added tender",
    ("POST", "/api/bids"): "Started a bid",
    ("PATCH", "/api/bids/{bid_id}"): "Edited a bid",
    ("DELETE", "/api/bids/{bid_id}"): "Removed a bid",
    ("POST", "/api/bids/{bid_id}/items"): "Added a checklist item",
    ("PATCH", "/api/bids/{bid_id}/items/{item_id}"): "Changed a checklist item",
    ("DELETE", "/api/bids/{bid_id}/items/{item_id}"): "Removed a checklist item",
    ("POST", "/api/bids/{bid_id}/document"): "Read a bid's tender document with AI",
    ("POST", "/api/bids/{bid_id}/fetch"): "Fetched a bid's details from the portal",
    ("POST", "/api/bids/{bid_id}/eligibility"): "Ran the eligibility check",
    ("POST", "/api/bids/{bid_id}/draft"): "Drafted a letter with AI",
    ("POST", "/api/bids/{bid_id}/boq"): "Uploaded a BOQ",
    ("POST", "/api/bids/{bid_id}/boq/lines/{line_no}"): "Confirmed a BOQ line match",
    ("POST", "/api/bids/{bid_id}/risks"): "Read tender risks",
    ("POST", "/api/boq/catalogue"): "Imported the item catalogue",
    ("POST", "/api/vault"): "Added a paper to the Vault",
    ("PATCH", "/api/vault/{doc_id}"): "Edited a Vault paper",
    ("DELETE", "/api/vault/{doc_id}"): "Removed a Vault paper",
    ("PUT", "/api/company-facts"): "Changed the company facts",
    ("PUT", "/api/company-profile"): "Changed the company profile",
    ("PUT", "/api/views"): "Changed saved views",
    ("PUT", "/api/settings"): "Changed settings",
    ("POST", "/api/settings/test-email"): "Sent a test email",
    ("POST", "/api/portals"): "Added a portal",
    ("DELETE", "/api/portals/{key}"): "Removed a portal",
    ("POST", "/api/portals/{key}/check"): "Checked a portal",
    ("POST", "/api/ai/score"): "Labelled tenders with AI",
    ("POST", "/api/users"): "Created an account",
    ("PATCH", "/api/users/{user_id}"): "Changed an account",
    ("POST", "/api/account/password"): "Changed own password",
    ("GET", "/api/backup"): "Downloaded a backup",
    ("GET", "/api/vault/{doc_id}/file"): "Downloaded a Vault file",
    ("GET", "/api/tenders/export"): "Downloaded the tender spreadsheet",
}


def describe_action(method: str, route_path: str) -> str:
    return ACTIONS.get((method, route_path), f"{method} {route_path}")
