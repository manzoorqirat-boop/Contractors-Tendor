"""Runs a collection (and, if asked, the email) in the background, one at a time, and keeps its history."""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from . import ai, appsettings, collector, digest, mailer
from .bids import BidBook
from .config import Settings
from .store import SeenStore

log = logging.getLogger("tender_digest.runner")
MAX_LOG_LINES = 300


class Busy(RuntimeError):
    pass


class NotConfigured(RuntimeError):
    pass


def local_now(tz_name: str = "Asia/Kolkata") -> datetime:
    return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)


class RunManager:
    def __init__(self, env: Settings, store: SeenStore, client_factory: collector.ClientFactory = collector.default_client,
                 sender: Callable[..., list[str]] = mailer.send, tz_name: str = "Asia/Kolkata", background: bool = True,
                 bids: BidBook | None = None):
        self.env, self.store, self.client_factory, self.sender, self.tz_name = env, store, client_factory, sender, tz_name
        self.bids = bids or BidBook(store)
        self.background = background  # tests run inline
        self._guard = threading.Lock()
        self._lines: list[str] = []

    @property
    def mail_configured(self) -> bool:
        return bool(self.env.brevo_api_key and self.env.mail_from)

    def start(self, trigger: str, portals: list[str] | None = None, send_email: bool = False) -> int:
        saved = self.store.get_settings()
        settings = appsettings.effective(self.env, saved, portals)
        if not settings.portals:
            raise NotConfigured("No portal is switched on")
        if send_email:
            if not self.mail_configured:
                raise NotConfigured("Email is not set up: BREVO_API_KEY and MAIL_FROM are missing on the server")
            if not settings.recipients:
                raise NotConfigured("There are no recipients: add at least one email address in Settings")
        if not self._guard.acquire(blocking=False):
            raise Busy("A run is already in progress")
        try:
            run_id = self.store.create_run(trigger, [p.key for p in settings.portals], send_email, local_now(self.tz_name))
        except Exception:
            self._guard.release()
            raise
        self._lines = []
        if self.background:
            threading.Thread(target=self._run, args=(run_id, settings, send_email, saved), daemon=True, name=f"run-{run_id}").start()
        else:
            self._run(run_id, settings, send_email, saved)
        return run_id

    def _say(self, run_id: int, message: str) -> None:
        self._lines.append(f"{local_now(self.tz_name).strftime('%H:%M:%S')}  {message}")
        self._lines = self._lines[-MAX_LOG_LINES:]
        self.store.update_run(run_id, log=self._lines)

    def _run(self, run_id: int, settings: Settings, send_email: bool, saved: dict) -> None:
        say = lambda m: self._say(run_id, m)  # noqa: E731
        try:
            now = local_now(self.tz_name)
            say(f"Started ({', '.join(p.name for p in settings.portals)})")
            tracked = self.bids.watch_keys()
            watched = {"changes": 0}

            def watch(t, client) -> None:
                bid_id = tracked.get(t.key)
                if bid_id:
                    n = self.bids.watch(t, client, now, bid_id)
                    if n:
                        watched["changes"] += n
                        say(f"Bid watch: {n} change{'s' if n != 1 else ''} on {t.tender_id}")

            reports = collector.collect(settings, self.store, now, self.client_factory, say, watch if tracked else None)
            if tracked:
                say(f"Bid watch: looked again at tenders you are bidding for ({watched['changes']} change{'s' if watched['changes'] != 1 else ''} found)")
            summary = [
                {"portal": r.portal.key, "name": r.portal.name, "departments": r.organisations_read, "listed": r.listed,
                 "new_civil": r.new_civil, "errors": r.errors[:5], "failed": r.failed, "access": r.access}
                for r in reports
            ]
            intro = ""
            profile = str(saved.get("company_profile", "") or "")
            if ai.configured():
                ai.enrich(self.store, now, profile, bool(saved.get("ai_hide_noncivil", False)), int(os.environ.get("AI_MAX_PER_RUN", "150") or 150), say)
            emailed = 0
            if send_email:
                pending = self.store.pending(now)
                soon = self.store.closing_soon(now, settings.closing_soon_days)
                try:
                    bid_block = self.bids.digest_block(now)
                except Exception:  # noqa: BLE001 - the email goes out without the bid section
                    log.exception("bid section of the digest failed")
                    bid_block = {"alerts": [], "changes": []}
                quiet = not pending and not soon and not any(r.errors for r in reports) and not bid_block["alerts"] and not bid_block["changes"]
                if quiet and not saved.get("send_empty", True):
                    say("Nothing new, and 'send on empty days' is off: no email sent")
                else:
                    if ai.configured() and pending and saved.get("ai_digest_intro", True):
                        try:
                            info = self.store.ai_info([t.key for t in pending])
                            items = sorted(({"tender_id": t.tender_id, "title": t.title, "description": t.description, "location": t.location, "value": t.value,
                                             "closing": t.closing.date().isoformat() if t.closing else None, "fit": (info.get(t.key) or {}).get("fit")} for t in pending),
                                           key=lambda x: (-(x["fit"] or 0), -(x["value"] or 0)))
                            intro = ai.digest_intro(items, profile)
                        except Exception as e:  # noqa: BLE001 - the email goes out without the intro
                            say(f"Claude intro skipped: {str(e)[:120]}")
                    built = digest.build(now, pending, soon, reports, settings.subject_prefix, settings.max_new_per_portal, settings.max_soon_per_portal, intro, bid_block)
                    say(f"Sending '{built.subject}' to {len(settings.recipients)} recipient(s)")
                    accepted = self.sender(
                        settings.brevo_api_key, settings.mail_from, settings.mail_from_name, settings.recipients, built.subject, built.html, built.text,
                        attachments=[{"name": built.csv_name, "content": built.csv_base64}] if built.csv_base64 else None,
                    )
                    self.store.mark_emailed([t.key for t in pending])
                    self.bids.mark_notified([c["id"] for c in bid_block["changes"]])
                    emailed = len(accepted)
                    say(f"Email accepted for {emailed} of {len(settings.recipients)} recipient(s)")
            failed = reports and all(r.failed for r in reports)
            self.store.prune(now)
            self.store.update_run(
                run_id, status="failed" if failed else "ok", finished=local_now(self.tz_name).isoformat(timespec="seconds"),
                reports=summary, new_civil=sum(r.new_civil for r in reports), emailed_count=emailed,
                error="Every portal failed" if failed else None,
            )
            say("Finished")
        except Exception as e:  # noqa: BLE001 - a run must always end in a recorded state
            log.exception("run %s failed", run_id)
            self.store.update_run(run_id, status="failed", finished=local_now(self.tz_name).isoformat(timespec="seconds"), error=f"{type(e).__name__}: {e}"[:500])
            say(f"Failed: {e}")
        finally:
            self._guard.release()

    def send_test(self) -> int:
        if not self.mail_configured:
            raise NotConfigured("Email is not set up: BREVO_API_KEY and MAIL_FROM are missing on the server")
        recipients = appsettings.current(self.env, self.store.get_settings())["recipients"]
        if not recipients:
            raise NotConfigured("There are no recipients: add at least one email address first")
        accepted = self.sender(self.env.brevo_api_key, self.env.mail_from, self.env.mail_from_name, recipients, "TendAI by QMSofts: test email",
                               "<p>This is a test. The daily digest is set up for this address.</p>", "This is a test. The daily digest is set up for this address.")
        return len(accepted)


MAX_ATTEMPTS = 3                         # daily runs tried per day, the first one included
RETRY_AFTER = timedelta(minutes=10)      # wait after a failed run before trying again
RETRYABLE = re.compile(r"^(Interrupted by a restart|Every portal failed|[A-Za-z_.]+(Error|Exception|Timeout)\b)")


class Scheduler(threading.Thread):
    """Starts the daily scheduled run. If the service was down at the set time it runs once when it comes back, the same day."""

    def __init__(self, manager: RunManager, interval: float = 30.0):
        super().__init__(daemon=True, name="scheduler")
        self.manager, self.interval = manager, interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def due(self, now: datetime) -> bool:
        saved = self.manager.store.get_settings()
        cur = appsettings.current(self.manager.env, saved)
        if not cur["schedule_enabled"]:
            return False
        hh, mm = (int(x) for x in cur["send_time"].split(":"))
        if now < now.replace(hour=hh, minute=mm, second=0, microsecond=0):
            return False
        today = self.manager.store.scheduled_runs_on(now.date().isoformat())
        if not today:
            return True
        # A daily run that failed for a reason that may pass (a restart of the service, every portal down) is tried again a little
        # later, up to MAX_ATTEMPTS a day. A run that is still going, finished, or failed because setup is missing is left alone.
        last = today[-1]
        if len(today) >= MAX_ATTEMPTS or last["status"] != "failed" or not RETRYABLE.match(last.get("error") or ""):
            return False
        done = datetime.fromisoformat(last["finished"] or last["started"]).replace(tzinfo=None)
        return now.replace(tzinfo=None) - done >= RETRY_AFTER

    def tick(self) -> int | None:
        now = local_now(self.manager.tz_name)
        if not self.due(now):
            return None
        try:
            return self.manager.start("scheduled", None, send_email=True)
        except Busy:
            return None  # a manual run is going; try again on the next tick
        except NotConfigured as e:
            # Recorded as a failed scheduled run so the page shows it and it is not retried every 30 seconds.
            run_id = self.manager.store.create_run("scheduled", [], True, now)
            self.manager.store.update_run(run_id, status="failed", finished=now.isoformat(timespec="seconds"), error=str(e))
            return run_id

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
