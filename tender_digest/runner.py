"""Runs a collection (and, if asked, the email) in the background, one at a time, and keeps its history."""
from __future__ import annotations

import html
import logging
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import ai, alerts, appsettings, collector, digest, health, mailer, reminders
from .bids import BidBook
from .config import Settings
from .money import MoneyBook
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

    REMINDER_RETRY = timedelta(minutes=10)

    def send_reminders(self, now: datetime, app_url: str = "") -> int:
        """Sends the deadline reminders that are due (once each) by email and, if set up, to the webhook. Returns how many were sent.
        With no way to send (no email set up, no webhook) nothing is recorded, so reminders go out as soon as one is."""
        saved = self.store.get_settings()
        cur = appsettings.current(self.env, saved)
        if not cur["reminders_on"]:
            return 0
        sent = self.store.reminders_sent()
        fire, handled = reminders.due(reminders.deadlines(self.bids.list(), now), now, sent)
        if not fire:
            if handled:
                self.store.reminders_mark(handled, now.isoformat(timespec="seconds"))
            return 0
        by_mail = self.mail_configured and bool(cur["recipients"])
        hook = cur["webhook_url"]
        if not by_mail and not hook:
            return 0
        failed_at = saved.get("reminder_failed")  # after a failure, wait before trying again; after a success there is no wait
        if failed_at and now - datetime.fromisoformat(failed_at) < self.REMINDER_RETRY:
            return 0
        subject, html_body, text = reminders.build_message(fire, app_url)
        ok = False
        if by_mail:
            try:
                self.sender(self.env.brevo_api_key, self.env.mail_from, self.env.mail_from_name, cur["recipients"], subject, html_body, text)
                ok = True
            except Exception as e:  # tried again in 10 minutes
                log.warning("reminder email failed: %s", e)
        if hook:
            try:
                reminders.post_webhook(hook, f"{subject}\n{text}", fire)
                ok = True
            except reminders.WebhookError as e:
                log.warning("reminder webhook failed: %s", e)
        if ok:
            self.store.reminders_mark(handled, now.isoformat(timespec="seconds"))
            if failed_at:
                self.store.put_settings({"reminder_failed": None})
            return len(fire)
        self.store.put_settings({"reminder_failed": now.isoformat(timespec="seconds")})
        return 0

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
                except Exception:  # the email goes out without the bid section
                    log.exception("bid section of the digest failed")
                    bid_block = {"alerts": [], "changes": []}
                try:
                    bid_block["money"] = MoneyBook(self.bids).digest_lines(now)
                except Exception:  # the email goes out without the money items
                    log.exception("money section of the digest failed")
                    bid_block["money"] = []
                try:
                    unhealthy = health.problems(health.assess_all(self.store, settings.portals, {p.key for p in settings.portals}, now))
                except Exception:  # the email goes out without the portal-health section
                    log.exception("portal health section of the digest failed")
                    unhealthy = []
                quiet = (not pending and not soon and not any(r.errors for r in reports) and not bid_block["alerts"] and not bid_block["changes"]
                         and not bid_block["money"] and not unhealthy)
                if quiet and not saved.get("send_empty", True):
                    say("Nothing new, and 'send on empty days' is off: no email sent")
                else:
                    if ai.configured() and pending and saved.get("ai_digest_intro", True):
                        try:
                            info = self.store.ai_info([t.key for t in pending])
                            items: list[dict[str, Any]] = sorted(({"tender_id": t.tender_id, "title": t.title, "description": t.description, "location": t.location, "value": t.value,
                                             "closing": t.closing.date().isoformat() if t.closing else None, "fit": (info.get(t.key) or {}).get("fit")} for t in pending),
                                           key=lambda x: (-(x["fit"] or 0), -(x["value"] or 0)))
                            intro = ai.digest_intro(items, profile)
                        except Exception as e:  # the email goes out without the intro
                            say(f"Claude intro skipped: {str(e)[:120]}")
                    try:
                        searches = alerts.matches(self.store, now, saved.get("views", []), [t.key for t in pending], settings.closing_soon_days,
                                                 size=appsettings.size_limits(saved))
                    except Exception:  # the email goes out without the saved-search section
                        log.exception("saved-search section of the digest failed")
                        searches = []
                    built = digest.build(now, pending, soon, reports, settings.subject_prefix, settings.max_new_per_portal, settings.max_soon_per_portal, intro, bid_block, searches, unhealthy)
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
            self.store.prune(now, appsettings.current(self.env, self.store.get_settings())["tender_days"])
            try:  # new tenders, new AI labels and the passing hours all change the recommended order
                appsettings.rescore(self.store, now)
            except Exception:  # the run itself must not fail over its ordering
                log.exception("recomputing the recommended order failed")
            self.store.update_run(
                run_id, status="failed" if failed else "ok", finished=local_now(self.tz_name).isoformat(timespec="seconds"),
                reports=summary, new_civil=sum(r.new_civil for r in reports), emailed_count=emailed,
                error="Every portal failed" if failed else None,
            )
            say("Finished")
        except Exception as e:  # a run must always end in a recorded state
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

    def __init__(self, manager: RunManager, interval: float = 30.0, housekeeping: Callable[[datetime], object] | None = None):
        super().__init__(daemon=True, name="scheduler")
        self.manager, self.interval, self.housekeeping = manager, interval, housekeeping  # housekeeping: e.g. the daily backup, run every tick
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

    def maybe_alert(self, now: datetime) -> bool:
        """Email the recipients once a day when today's daily run has failed for good: no more retries are coming and none succeeded."""
        try:
            store, manager = self.manager.store, self.manager
            saved = store.get_settings()
            cur = appsettings.current(manager.env, saved)
            day = now.date().isoformat()
            if not cur["schedule_enabled"] or not manager.mail_configured or not cur["recipients"] or saved.get("last_failure_alert") == day:
                return False
            tried = saved.get("failure_alert_try")
            if tried and now.replace(tzinfo=None) - datetime.fromisoformat(tried).replace(tzinfo=None) < timedelta(minutes=15):
                return False
            today = store.scheduled_runs_on(day)
            if not today or any(r["status"] in ("running", "ok") for r in today):
                return False
            last = today[-1]
            if len(today) < MAX_ATTEMPTS and RETRYABLE.match(last.get("error") or ""):
                return False  # another attempt is still coming
            store.put_settings({"failure_alert_try": now.isoformat(timespec="seconds")})
            subject, body_html, body_text = self._alert_text(today, store.get_run(last["id"]) or {})
            manager.sender(manager.env.brevo_api_key, manager.env.mail_from, manager.env.mail_from_name, cur["recipients"], subject, body_html, body_text)
            store.put_settings({"last_failure_alert": day})
            return True
        except Exception:  # an alert problem must never stop the scheduler
            log.exception("failure alert could not be sent")
            return False

    @staticmethod
    def _alert_text(today: list[dict], last_run: dict) -> tuple[str, str, str]:
        attempts = "\n".join(f"- started {r['started'][11:16]}: {r.get('error') or 'failed'}" for r in today)
        portals = "\n".join(f"- {r.get('name') or r.get('portal')}: {(r.get('errors') or ['no detail'])[0][:140]}" for r in (last_run.get("reports") or []) if r.get("failed"))
        text = (f"Today's daily tender run did not complete, so there is no digest today.\n\nAttempts ({len(today)}):\n{attempts}\n"
                + (f"\nPortals that failed in the last attempt:\n{portals}\n" if portals else "")
                + "\nOpen the Runs page for the log, and press Run now to try again.")
        body_html = "<p>" + html.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
        return "TendAI: today's daily run failed", body_html, text

    def tick(self) -> int | None:
        now = local_now(self.manager.tz_name)
        if self.housekeeping:
            try:
                self.housekeeping(now)
            except Exception:  # a backup problem is logged and must never stop the daily run
                log.exception("housekeeping (daily backup) failed")
        if not self.due(now):
            self.maybe_alert(now)
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
            except Exception:
                log.exception("scheduler tick failed")
