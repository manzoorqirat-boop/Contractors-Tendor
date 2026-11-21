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
import builtins
import hashlib
import hmac
import re
import secrets
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .sealing import Sealer

ROLES = ("viewer", "bidder", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
ROLE_TEXT = {
    "viewer": "Can look at everything, can change nothing",
    "bidder": "Can work on tenders and bids, use the Vault and the AI assistant",
    "admin": "Can also change settings, portals, people and backups",
}
OWNER_ID = 0
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
EMAIL_RE = re.compile(r"^[^\s@<>\",;]+@[^\s@<>\",;]+\.[^\s@<>\",;]{2,}$")


def clean_email(value: str | None) -> str:
    """An email address, lower-cased, or empty. Raises AccountError for something that is not one."""
    v = (value or "").strip().lower()
    if v and (len(v) > 120 or not EMAIL_RE.match(v)):
        raise AccountError("That does not look like an email address")
    return v
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
    two_step: bool = False
    email: str = ""

    @property
    def owner(self) -> bool:
        return self.id == OWNER_ID

    def can(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "name": self.name, "role": self.role, "owner": self.owner, "must_change": self.must_change, "two_step": self.two_step, "email": self.email}


# ---- what each kind of request needs ---------------------------------------------------------------------------------

_ADMIN_AREAS = re.compile(r"^/api/((users|audit|backup|settings|calendar|tokens|webhooks|sample-data|cleanup)(/|$)|sso/info$|portals/[^/]+/sample$|whatsapp/status$)")
_VAULT_FILE = re.compile(r"^/api/(vault/\d+/file|bids/\d+/pack|money(/export)?|bids/\d+/money|plan)$")   # company papers and money figures are not for view-only accounts


def required_role(method: str, path: str) -> str:
    """The least role allowed to make this request. One table for the whole app, so it can be read, and tested, in one place."""
    if path.startswith("/api/account/"):
        return "viewer"  # everyone manages their own password, two-step sign-in and devices
    if _ADMIN_AREAS.match(path):
        return "admin"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "bidder" if _VAULT_FILE.match(path) else "viewer"  # company papers are not for view-only accounts
    if path.startswith("/api/portals"):
        return "admin"
    return "bidder"


def describe_agent(ua: str) -> str:
    """"Chrome on Windows" from a browser's user-agent text: enough to recognise a device, nothing that identifies a person."""
    u = ua or ""
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if k in u), "")
    system = next((n for k, n in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Mac OS X", "Mac"), ("Linux", "Linux")) if k in u), "")
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Unknown device"


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


# ---- two-step sign-in: time-based one-time codes (RFC 6238), as shown by any authenticator app --------------------------------

TOTP_STEP = 30


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(number).zfill(digits)


def totp_match(secret: str, code: str, now: float | None = None, after_step: int = 0) -> int | None:
    """The time step the code belongs to when it is right (allowing one step either side for clock drift), else None.
    A step at or before `after_step` is refused, so a code that was already used cannot be used again."""
    code = re.sub(r"\s+", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    current = int((now if now is not None else time.time()) // TOTP_STEP)
    for step in (current, current - 1, current + 1):
        if step > after_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def totp_uri(secret: str, account: str, issuer: str = "TendAI") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&period={TOTP_STEP}&digits=6"


# ---- the stored accounts and the activity log ----------------------------------------------------------------------------

class AccountError(Exception):
    pass


class Accounts:
    def __init__(self, store: Any, sealer: Sealer | None = None):
        self.sealer = sealer or Sealer()
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
                CREATE TABLE IF NOT EXISTS password_resets (
                  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
                """)
            have = {r["name"] for r in self.db.execute("PRAGMA table_info(users)")}
            for col, kind in (("totp_secret", "TEXT"), ("sso_sub", "TEXT"), ("totp_on", "INTEGER NOT NULL DEFAULT 0"), ("totp_last", "INTEGER NOT NULL DEFAULT 0"), ("email", "TEXT NOT NULL DEFAULT ''")):
                if col not in have:
                    self.db.execute(f"ALTER TABLE users ADD COLUMN {col} {kind}")
            self.db.commit()
        self._audit_writes = 0
        self._code_fails: dict[int, list[float]] = {}
        self._guard = threading.Lock()

    # -- people --

    def _principal(self, r: Any) -> Principal:
        return Principal(int(r["id"]), r["username"], r["name"] or r["username"], r["role"], bool(r["must_change"]), bool(r["totp_on"]), r["email"] or "")

    def get(self, user_id: int) -> Principal | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
        return self._principal(r) if r else None

    def authenticate(self, username: str, password: str) -> Principal | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username.strip().lower(),)).fetchone()
        ok = verify_password(password, r["pw_hash"] if r else _DUMMY_HASH)
        return self._principal(r) if (r and ok) else None

    # -- who can be told things --

    def directory(self) -> list[dict[str, str]]:
        """Names for pickers (who is a task assigned to): everyone active, with no contact details."""
        with self.lock:
            return [{"username": r["username"], "name": r["name"] or r["username"]} for r in self.db.execute("SELECT username, name FROM users WHERE active = 1 ORDER BY name, username")]

    def emails_for(self, usernames: list[str]) -> list[str]:
        names = [u.strip().lower() for u in usernames if u and u.strip()]
        if not names:
            return []
        with self.lock:
            rows = self.db.execute(f"SELECT email FROM users WHERE active = 1 AND email != '' AND username IN ({','.join('?' * len(names))})", names).fetchall()
        return sorted({r["email"] for r in rows})

    def admin_emails(self) -> list[str]:
        with self.lock:
            return sorted({r["email"] for r in self.db.execute("SELECT email FROM users WHERE active = 1 AND role = 'admin' AND email != ''")})

    def set_own_email(self, user_id: int, email: str) -> str:
        email = clean_email(email)
        with self.lock:
            self.db.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
            self.db.commit()
        return email

    # -- two-step sign-in --

    def totp_start(self, user_id: int) -> str:
        """Makes a new secret for the person to add to their authenticator app. It is not required at sign-in until they confirm a code."""
        secret = new_totp_secret()
        with self.lock:
            self.db.execute("UPDATE users SET totp_secret = ?, totp_on = 0, totp_last = 0 WHERE id = ?", (self.sealer.seal_text(secret), user_id))
            self.db.commit()
        return secret

    def totp_enable(self, user_id: int, code: str, now: float | None = None) -> None:
        with self.lock:
            r = self.db.execute("SELECT totp_secret FROM users WHERE id = ?", (user_id,)).fetchone()
        step = totp_match(self.sealer.open_text(r["totp_secret"]), code, now) if r and r["totp_secret"] else None
        if step is None:
            raise AccountError("That code is not right. Check the app, wait for a new code, and try again")
        with self.lock:
            self.db.execute("UPDATE users SET totp_on = 1, totp_last = ? WHERE id = ?", (step, user_id))
            self.db.commit()

    def totp_off(self, user_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE users SET totp_secret = NULL, totp_on = 0, totp_last = 0 WHERE id = ?", (user_id,))
            self.db.commit()

    def totp_check(self, user_id: int, code: str, now: float | None = None) -> bool:
        """Checks a sign-in code. Each code works once, and ten wrong ones in ten minutes pause that account's codes for a while."""
        t = now if now is not None else time.time()
        fails = [x for x in self._code_fails.get(user_id, []) if t - x < 600]
        self._code_fails[user_id] = fails
        if len(fails) >= 10:
            return False
        with self.lock:
            r = self.db.execute("SELECT totp_secret, totp_last FROM users WHERE id = ? AND totp_on = 1", (user_id,)).fetchone()
            step = totp_match(self.sealer.open_text(r["totp_secret"]), code, t, r["totp_last"]) if r else None
            if step is not None:
                self.db.execute("UPDATE users SET totp_last = ? WHERE id = ?", (step, user_id))
                self.db.commit()
        if step is None:
            fails.append(t)
            return False
        self._code_fails[user_id] = []
        return True

    def seal_existing(self) -> int:
        """Encrypts two-step secrets stored before a key was set (and re-encrypts under a new key). Returns how many were written."""
        if not self.sealer.enabled:
            return 0
        n = 0
        with self.lock:
            for r in self.db.execute("SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL AND totp_secret != ''").fetchall():
                new = self.sealer.rotate_text(r["totp_secret"]) if self.sealer.has_old or not self.sealer.is_sealed_text(r["totp_secret"]) else r["totp_secret"]
                if new != r["totp_secret"]:
                    self.db.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (new, r["id"]))
                    n += 1
            self.db.commit()
        return n

    def plain_secrets(self) -> int:
        with self.lock:
            return sum(1 for r in self.db.execute("SELECT totp_secret FROM users WHERE totp_secret IS NOT NULL AND totp_secret != ''") if not self.sealer.is_sealed_text(r["totp_secret"]))

    def record_login(self, user_id: int, now: datetime) -> None:
        with self.lock:
            self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now.isoformat(timespec="seconds"), user_id))
            self.db.commit()

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT id, username, name, role, active, must_change, created, last_login, totp_on, email, sso_sub FROM users ORDER BY active DESC, username").fetchall()
        return [{**{k: v for k, v in dict(r).items() if k not in ("totp_on", "sso_sub")}, "active": bool(r["active"]), "must_change": bool(r["must_change"]), "two_step": bool(r["totp_on"]),
                 "sso": bool(r["sso_sub"])} for r in rows]

    def exists(self, username: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM users WHERE username = ?", (username.strip().lower(),)).fetchone() is not None

    def create(self, username: str, name: str, role: str, password: str, now: datetime, reserved: str = "", email: str = "") -> dict[str, Any]:
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
        email = clean_email(email)
        if self.exists(username):
            raise AccountError("That username is already taken")
        with self.lock:
            cur = self.db.execute("INSERT INTO users (username, name, role, pw_hash, active, must_change, created, email) VALUES (?,?,?,?,1,1,?,?)",
                                  (username, name.strip()[:80], role, hash_password(password), now.isoformat(timespec="seconds"), email))
            self.db.commit()
        return next(u for u in self.list() if u["id"] == cur.lastrowid)

    def update(self, user_id: int, *, name: str | None = None, role: str | None = None, active: bool | None = None, password: str | None = None,
               reset_two_step: bool = False, email: str | None = None) -> dict[str, Any]:
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
        if email is not None:
            sets["email"] = clean_email(email)
        if reset_two_step:
            sets.update(totp_secret=None, totp_on=0, totp_last=0)  # a lost phone: they set it up again after signing in
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

    # -- single sign-on --

    def sso_login(self, who: dict[str, str], default_role: str, now: datetime) -> Principal:
        """The person an SSO sign-in belongs to. Found by their provider id, else matched to an account with the same email (and linked), else created
        with the default role and no local password. A switched-off account stays switched off."""
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE sso_sub = ?", (who["sub"],)).fetchone()
            if r is None:
                r = self.db.execute("SELECT * FROM users WHERE email = ? AND email != '' AND (sso_sub IS NULL OR sso_sub = '') ORDER BY active DESC LIMIT 1", (who["email"],)).fetchone()
                if r is not None:
                    self.db.execute("UPDATE users SET sso_sub = ? WHERE id = ?", (who["sub"], r["id"]))
                    self.db.commit()
            if r is not None:
                if not r["active"]:
                    raise AccountError("Your account is switched off. Ask an administrator")
                return self._principal(r)
        base = re.sub(r"[^a-z0-9._-]+", "", who["email"].split("@")[0].lower()).lstrip("._-")[:34] or "user"
        name = base if len(base) >= 3 else base + "-sso"
        n = 1
        while self.exists(name):
            n += 1
            name = f"{base[:34]}{n}"
        with self.lock:
            cur = self.db.execute("INSERT INTO users (username, name, role, pw_hash, active, must_change, created, email, sso_sub) VALUES (?,?,?,?,1,0,?,?,?)",
                                  (name, who["name"] or name, default_role if default_role in ("viewer", "bidder") else "viewer", "!", now.isoformat(timespec="seconds"), who["email"], who["sub"]))
            self.db.commit()
            return self._principal(self.db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())

    # -- forgotten passwords: a link by email, good for an hour and for one use --

    RESET_MINUTES = 60
    RESET_PER_HOUR = 3

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def reset_request(self, who: str, now: float) -> tuple[Principal, str] | None:
        """Makes a reset link for the active person whose username or email is `who`. None when there is no such person, they have no email,
        or they have already asked three times this hour (the caller says the same thing in every case, so nobody learns which)."""
        who = (who or "").strip().lower()[:120]
        if not who:
            return None
        with self.lock:
            self.db.execute("DELETE FROM password_resets WHERE expires < ?", (now,))
            r = self.db.execute("SELECT * FROM users WHERE active = 1 AND email != '' AND (username = ? OR email = ?) ORDER BY (username = ?) DESC LIMIT 1", (who, who, who)).fetchone()
            if not r:
                self.db.commit()
                return None
            recent = self.db.execute("SELECT COUNT(*) FROM password_resets WHERE user_id = ? AND created > ?", (r["id"], now - 3600)).fetchone()[0]
            if recent >= self.RESET_PER_HOUR:
                self.db.commit()
                return None
            token = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO password_resets (token_hash, user_id, created, expires) VALUES (?,?,?,?)", (self._token_hash(token), r["id"], now, now + self.RESET_MINUTES * 60))
            self.db.commit()
        return self._principal(r), token

    def reset_confirm(self, token: str, new_password: str, now: float) -> Principal:
        """Sets the new password if the link is still good. The link, and any other link for that person, stops working."""
        with self.lock:
            row = self.db.execute("SELECT user_id, expires FROM password_resets WHERE token_hash = ?", (self._token_hash(token or ""),)).fetchone()
            u = self.db.execute("SELECT * FROM users WHERE id = ? AND active = 1", (row["user_id"],)).fetchone() if row else None
        if not row or not u or row["expires"] < now:
            raise AccountError("This link has expired or was already used. Ask for a new one on the sign-in page")
        problem = password_problem(new_password, u["username"])
        if problem:
            raise AccountError(problem)
        with self.lock:
            self.db.execute("UPDATE users SET pw_hash = ?, must_change = 0 WHERE id = ?", (hash_password(new_password), u["id"]))
            self.db.execute("DELETE FROM password_resets WHERE user_id = ?", (u["id"],))
            self.db.commit()
        return self._principal(self.db.execute("SELECT * FROM users WHERE id = ?", (u["id"],)).fetchone())

    # -- activity log --

    def log(self, when: datetime, username: str, role: str | None, action: str, target: str = "", detail: str = "", client: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT INTO audit (at, username, role, action, target, detail, client) VALUES (?,?,?,?,?,?,?)",
                            (when.isoformat(timespec="seconds"), username[:80], role, action[:120], target[:200], detail[:300], client[:64]))
            self._audit_writes += 1
            if self._audit_writes % 200 == 0:  # keep the table from growing without end
                self.db.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - ?", (MAX_AUDIT_ROWS,))
            self.db.commit()

    def prune_audit(self, now: datetime, keep_days: int) -> int:
        """Removes activity-log entries older than `keep_days`. Returns how many went."""
        cutoff = (now - timedelta(days=keep_days)).isoformat(timespec="seconds")
        with self.lock:
            cur = self.db.execute("DELETE FROM audit WHERE at < ?", (cutoff,))
            self.db.commit()
        return cur.rowcount

    def activity(self, limit: int = 100, username: str = "", before: int | None = None) -> builtins.list[dict[str, Any]]:
        sql = "SELECT id, at, username, role, action, target, detail, client FROM audit"
        args: list[Any] = []
        where: list[str] = []
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
    ("POST", "/api/tenders/star-bulk"): "Starred or unstarred several tenders",
    ("POST", "/api/bids/bulk"): "Started bids on several tenders",
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
    ("POST", "/api/bids/{bid_id}/doc-checked"): "Marked the AI-read details as checked (or unchecked)",
    ("POST", "/api/bids/{bid_id}/award"): "Read an award notice",
    ("PUT", "/api/bids/{bid_id}/bidders"): "Recorded who bid on a tender",
    ("POST", "/api/bids/{bid_id}/comments"): "Commented on a bid",
    ("DELETE", "/api/bids/{bid_id}/comments/{comment_id}"): "Removed a comment",
    ("POST", "/api/bids/{bid_id}/signoff"): "Asked for or gave approval to bid",
    ("POST", "/api/account/email"): "Changed own email address",
    ("POST", "/api/bids/{bid_id}/fetch"): "Fetched a bid's details from the portal",
    ("POST", "/api/bids/{bid_id}/eligibility"): "Ran the eligibility check",
    ("POST", "/api/bids/{bid_id}/draft"): "Drafted a letter with AI",
    ("POST", "/api/bids/{bid_id}/boq"): "Uploaded a BOQ",
    ("POST", "/api/bids/{bid_id}/boq/lines/{line_no}"): "Confirmed a BOQ line match",
    ("POST", "/api/bids/{bid_id}/boq/lines/{line_no}/cost"): "Entered a cost for a BOQ line",
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
    ("POST", "/api/settings/test-webhook"): "Sent a test message to the webhook",
    ("POST", "/api/calendar/token"): "Made a new calendar address",
    ("DELETE", "/api/calendar"): "Turned the calendar feed off",
    ("POST", "/api/portals"): "Added a portal",
    ("DELETE", "/api/portals/{key}"): "Removed a portal",
    ("POST", "/api/portals/{key}/check"): "Checked a portal",
    ("POST", "/api/ai/score"): "Labelled tenders with AI",
    ("POST", "/api/users"): "Created an account",
    ("PATCH", "/api/users/{user_id}"): "Changed an account",
    ("POST", "/api/account/password"): "Changed own password",
    ("POST", "/api/account/2fa/start"): "Began setting up two-step sign-in",
    ("POST", "/api/account/2fa/enable"): "Turned on two-step sign-in",
    ("POST", "/api/account/2fa/disable"): "Turned off two-step sign-in",
    ("POST", "/api/account/sessions/end-others"): "Signed out other devices",
    ("GET", "/api/backup"): "Downloaded a backup",
    ("POST", "/api/tenders/hide"): "Hid or restored tenders (not interested)",
    ("POST", "/api/mutes"): "Hid or showed a whole department",
    ("PUT", "/api/company-size"): "Changed the size of work the company takes",
    ("POST", "/api/cleanup"): "Cleaned up closed tenders",
    ("GET", "/api/portals/{key}/sample"): "Downloaded a sample of what a portal returns",
    ("POST", "/api/sample-data"): "Loaded sample tenders",
    ("DELETE", "/api/sample-data"): "Removed the sample tenders",
    ("GET", "/api/account/export"): "Downloaded own data",
    ("GET", "/api/users/{user_id}/export"): "Downloaded a person's data",
    ("POST", "/api/users/{user_id}/erase"): "Erased a person's data",
    ("POST", "/api/tokens"): "Made an API token",
    ("DELETE", "/api/tokens/{token_id}"): "Revoked an API token",
    ("POST", "/api/webhooks"): "Set up an outgoing webhook",
    ("PATCH", "/api/webhooks/{hook_id}"): "Changed an outgoing webhook",
    ("POST", "/api/webhooks/{hook_id}/secret"): "Made a new webhook secret",
    ("POST", "/api/webhooks/{hook_id}/test"): "Tested an outgoing webhook",
    ("DELETE", "/api/webhooks/{hook_id}"): "Removed an outgoing webhook",
    ("DELETE", "/api/account/sessions/{ref}"): "Signed out one device",
    ("GET", "/api/bids/{bid_id}/pack"): "Downloaded a bid pack",
    ("POST", "/api/letter-templates"): "Added a letter template",
    ("PUT", "/api/letter-templates/{template_id}"): "Saved a letter template",
    ("POST", "/api/letter-templates/{template_id}/restore"): "Restored an earlier version of a letter template",
    ("DELETE", "/api/letter-templates/{template_id}"): "Removed a letter template",
    ("POST", "/api/bids/{bid_id}/letter"): "Filled in a letter template for a bid",
    ("POST", "/api/bids/{bid_id}/corrigenda"): "Recorded a corrigendum",
    ("POST", "/api/backup/offsite-now"): "Sent a backup to the off-site bucket",
    ("GET", "/api/vault/{doc_id}/file"): "Downloaded a Vault file",
    ("GET", "/api/tenders/export"): "Downloaded the tender spreadsheet",
    ("POST", "/api/bids/{bid_id}/money"): "Recorded a payment on a bid",
    ("PATCH", "/api/bids/{bid_id}/money/{money_id}"): "Changed a payment on a bid",
    ("DELETE", "/api/bids/{bid_id}/money/{money_id}"): "Deleted a payment on a bid",
    ("GET", "/api/money/export"): "Downloaded the bid money spreadsheet",
}


def describe_action(method: str, route_path: str) -> str:
    return ACTIONS.get((method, route_path), f"{method} {route_path}")
