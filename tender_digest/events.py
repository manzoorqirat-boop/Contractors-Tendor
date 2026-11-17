"""Outgoing webhooks: the app tells another system when something happens (a bid starts, changes stage, a date moves, a result is recorded).

Each webhook has an address, a secret and the events it wants. Deliveries are queued, sent by the scheduler, retried with a growing pause
when the receiver is down, and signed so the receiver can check they came from this app:

    X-TendAI-Signature: sha256=HMAC_SHA256(secret, "<X-TendAI-Timestamp>.<body>")

The address must be a public https address (the same rule as the reminder webhook), and redirects are not followed.
"""
from __future__ import annotations

import builtins
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from .gepnic import is_public_host
from .reminders import check_webhook_url
from .sealing import Sealer

EVENTS = {
    "bid.created": "A bid is started on a tender",
    "bid.stage_changed": "A bid moves to another stage (interested, preparing, submitted, won, lost...)",
    "bid.changed": "A closing date, opening date or corrigendum changes on a bid",
    "bid.result": "Bidders and prices are recorded for a bid",
    "test": "A test message you send yourself",
}
MAX_HOOKS = 10
MAX_QUEUE_PER_HOOK = 500
RETRY_SECONDS = (60, 300, 1800, 7200)  # after the 1st..4th failure; the 5th failure gives up
MAX_ATTEMPTS = len(RETRY_SECONDS) + 1


class HookError(ValueError):
    pass


def sign(secret: str, timestamp: str, body: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


class Events:
    def __init__(self, store: Any, sealer: Sealer | None = None):
        self.sealer = sealer or Sealer()
        self.db, self.lock = store.db, store.lock
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS webhooks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL, secret TEXT NOT NULL, events TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                  created TEXT NOT NULL, last_at TEXT, last_status TEXT, last_error TEXT);
                CREATE TABLE IF NOT EXISTS webhook_queue (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, hook_id INTEGER NOT NULL, event TEXT NOT NULL, delivery TEXT NOT NULL, body TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS webhook_queue_due ON webhook_queue (next_try);
                """)
            self.db.commit()

    # -- the webhooks --

    @staticmethod
    def _events(raw: Any) -> list[str]:
        chosen = [e for e in dict.fromkeys(raw or []) if e in EVENTS and e != "test"]
        if not chosen:
            raise HookError("Choose at least one event to send")
        return chosen

    def _shape(self, r: Any) -> dict:
        return {"id": r["id"], "name": r["name"], "url": r["url"], "events": json.loads(r["events"]), "active": bool(r["active"]), "created": r["created"],
                "secret_hint": "…" + self.sealer.open_text(r["secret"])[-4:], "last_at": r["last_at"], "last_status": r["last_status"], "last_error": r["last_error"]}

    def list(self) -> list[dict]:
        with self.lock:
            hooks = [self._shape(r) for r in self.db.execute("SELECT * FROM webhooks ORDER BY id")]
            waiting = {r["hook_id"]: r["n"] for r in self.db.execute("SELECT hook_id, COUNT(*) n FROM webhook_queue GROUP BY hook_id")}
        return [{**h, "waiting": waiting.get(h["id"], 0)} for h in hooks]

    def create(self, name: str, url: str, events: builtins.list[str], now: datetime) -> tuple[dict, str]:
        """Returns (the webhook, its secret). The secret is shown once."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise HookError("Name the webhook after what receives it")
        try:
            url = check_webhook_url(url)
        except ValueError as e:
            raise HookError(str(e)) from None
        if not url:
            raise HookError("Give the address that should receive the messages")
        chosen = self._events(events)
        with self.lock:
            if self.db.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0] >= MAX_HOOKS:
                raise HookError(f"Up to {MAX_HOOKS} webhooks can be set up")
            secret = "whsec_" + secrets.token_urlsafe(24)
            cur = self.db.execute("INSERT INTO webhooks (name, url, secret, events, created) VALUES (?,?,?,?,?)", (name, url, self.sealer.seal_text(secret), json.dumps(chosen), now.isoformat(timespec="seconds")))
            self.db.commit()
        return self.get(cur.lastrowid) or {}, secret

    def get(self, hook_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM webhooks WHERE id = ?", (hook_id,)).fetchone()
        return self._shape(r) if r else None

    def update(self, hook_id: int, *, name: str | None = None, url: str | None = None, events: builtins.list[str] | None = None, active: bool | None = None) -> dict:
        sets: dict[str, Any] = {}
        if name is not None:
            n = " ".join(name.split())[:60]
            if not n:
                raise HookError("Name the webhook after what receives it")
            sets["name"] = n
        if url is not None:
            try:
                sets["url"] = check_webhook_url(url)
            except ValueError as e:
                raise HookError(str(e)) from None
            if not sets["url"]:
                raise HookError("Give the address that should receive the messages")
        if events is not None:
            sets["events"] = json.dumps(self._events(events))
        if active is not None:
            sets["active"] = 1 if active else 0
        if not sets:
            raise HookError("Nothing to change")
        with self.lock:
            cur = self.db.execute(f"UPDATE webhooks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), hook_id])
            if sets.get("active") == 0 or "url" in sets:
                self.db.execute("DELETE FROM webhook_queue WHERE hook_id = ?", (hook_id,))  # nothing queued for the old address or a switched-off hook is sent
            self.db.commit()
        if not cur.rowcount:
            raise HookError("No such webhook")
        return self.get(hook_id) or {}

    def new_secret(self, hook_id: int) -> str:
        secret = "whsec_" + secrets.token_urlsafe(24)
        with self.lock:
            cur = self.db.execute("UPDATE webhooks SET secret = ? WHERE id = ?", (self.sealer.seal_text(secret), hook_id))
            self.db.commit()
        if not cur.rowcount:
            raise HookError("No such webhook")
        return secret

    def delete(self, hook_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM webhooks WHERE id = ?", (hook_id,))
            self.db.execute("DELETE FROM webhook_queue WHERE hook_id = ?", (hook_id,))
            self.db.commit()
        return bool(cur.rowcount)

    # -- sending --

    def emit(self, event: str, data: dict[str, Any], now: datetime, only_hook: int | None = None) -> int:
        """Queues the event for every active webhook that wants it. Returns how many were queued. Never raises: telling others must not break the action."""
        try:
            stamp = time.time()
            n = 0
            with self.lock:
                for r in self.db.execute("SELECT id, events FROM webhooks WHERE active = 1").fetchall():
                    if only_hook is not None and r["id"] != only_hook:
                        continue
                    if event != "test" and event not in json.loads(r["events"]):
                        continue
                    delivery = uuid.uuid4().hex
                    body = json.dumps({"id": delivery, "event": event, "at": now.isoformat(timespec="seconds"), "data": data}, ensure_ascii=False, default=str)
                    self.db.execute("INSERT INTO webhook_queue (hook_id, event, delivery, body, next_try, created) VALUES (?,?,?,?,?,?)", (r["id"], event, delivery, body, stamp, stamp))
                    self.db.execute("DELETE FROM webhook_queue WHERE hook_id = ? AND id <= (SELECT MAX(id) FROM webhook_queue WHERE hook_id = ?) - ?", (r["id"], r["id"], MAX_QUEUE_PER_HOOK))
                    n += 1
                self.db.commit()
            return n
        except Exception:
            return 0

    def deliver(self, now: datetime, session: Any = None, limit: int = 20, timeout: int = 10) -> int:
        """Sends what is due. Returns how many were delivered. A failure is recorded on the webhook and retried later."""
        t = time.time()
        with self.lock:
            due = self.db.execute("SELECT q.*, h.url, h.secret, h.active FROM webhook_queue q LEFT JOIN webhooks h ON h.id = q.hook_id WHERE q.next_try <= ? ORDER BY q.id LIMIT ?", (t, limit)).fetchall()
        sent = 0
        for q in due:
            if q["url"] is None or not q["active"]:
                self._drop(q["id"])
                continue
            error = ""
            try:
                if not is_public_host(urlparse(q["url"]).hostname or ""):
                    raise OSError("that address points to a private network")
                ts = str(int(t))
                r = (session or requests).post(q["url"], data=q["body"].encode(), timeout=timeout, allow_redirects=False,
                                               headers={"Content-Type": "application/json", "User-Agent": "TendAI-Webhook/1", "X-TendAI-Event": q["event"], "X-TendAI-Delivery": q["delivery"],
                                                        "X-TendAI-Timestamp": ts, "X-TendAI-Signature": sign(self.sealer.open_text(q["secret"]), ts, q["body"])})
                if r.status_code >= 300:
                    error = f"The receiver answered {r.status_code}"
            except (requests.RequestException, OSError) as e:
                error = f"Could not reach the receiver: {str(e)[:120]}"
            if not error:
                self._drop(q["id"])
                self._note(q["hook_id"], now, "ok", None)
                sent += 1
                continue
            attempts = q["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                self._drop(q["id"])
                self._note(q["hook_id"], now, "failed", f"Gave up on {q['event']} after {attempts} tries. {error}")
            else:
                with self.lock:
                    self.db.execute("UPDATE webhook_queue SET attempts = ?, next_try = ? WHERE id = ?", (attempts, t + RETRY_SECONDS[attempts - 1], q["id"]))
                    self.db.commit()
                self._note(q["hook_id"], now, "retrying", error)
        return sent

    def seal_existing(self) -> int:
        """Encrypts webhook secrets stored before a key was set (and re-encrypts under a new key)."""
        if not self.sealer.enabled:
            return 0
        n = 0
        with self.lock:
            for r in self.db.execute("SELECT id, secret FROM webhooks").fetchall():
                new = self.sealer.rotate_text(r["secret"]) if self.sealer.has_old or not self.sealer.is_sealed_text(r["secret"]) else r["secret"]
                if new != r["secret"]:
                    self.db.execute("UPDATE webhooks SET secret = ? WHERE id = ?", (new, r["id"]))
                    n += 1
            self.db.commit()
        return n

    def _drop(self, queue_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM webhook_queue WHERE id = ?", (queue_id,))
            self.db.commit()

    def _note(self, hook_id: int, now: datetime, status: str, error: str | None) -> None:
        with self.lock:
            self.db.execute("UPDATE webhooks SET last_at = ?, last_status = ?, last_error = ? WHERE id = ?", (now.isoformat(timespec="seconds"), status, error, hook_id))
            self.db.commit()
