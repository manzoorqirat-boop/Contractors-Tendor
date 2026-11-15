"""When the daily run fails for good, the recipients are told once."""
from __future__ import annotations

from datetime import datetime, timedelta

from tender_digest.runner import MAX_ATTEMPTS, Scheduler
from tests.test_web import ctx  # noqa: F401  (the fixture)

DAY = datetime(2026, 11, 14)


def setup(ctx):
    ctx.store.put_settings({"recipients": ["boss@example.com"]})        # the fixture already has mail configured


def failed_run(ctx, at, error="Every portal failed", status="failed"):
    rid = ctx.store.create_run("scheduled", [], True, at)
    ctx.store.update_run(rid, status=status, finished=(at + timedelta(minutes=20)).isoformat(timespec="seconds"), error=error)


def test_alert_is_sent_once_after_the_last_attempt_fails(ctx):
    setup(ctx)
    sched = Scheduler(ctx.manager)
    for i in range(MAX_ATTEMPTS):
        failed_run(ctx, DAY.replace(hour=8 + i), "Every portal failed")
    assert sched.maybe_alert(DAY.replace(hour=15)) is True
    (args, _), = ctx.sender.calls
    assert args[3] == ["boss@example.com"] and "failed" in args[4] and "Every portal failed" in args[6]
    assert sched.maybe_alert(DAY.replace(hour=16)) is False and len(ctx.sender.calls) == 1        # once a day
    assert sched.maybe_alert(DAY.replace(hour=8) + timedelta(days=1, hours=7)) is False              # nothing failed on the next day yet


def test_no_alert_while_a_retry_is_still_coming_or_when_it_worked(ctx):
    setup(ctx)
    sched = Scheduler(ctx.manager)
    failed_run(ctx, DAY.replace(hour=8), "Interrupted by a restart of the service")
    assert sched.maybe_alert(DAY.replace(hour=9)) is False
    failed_run(ctx, DAY.replace(hour=9), None, status="ok")
    for i in range(MAX_ATTEMPTS):
        failed_run(ctx, DAY.replace(hour=10 + i))
    assert sched.maybe_alert(DAY.replace(hour=20)) is False                                         # one run succeeded today
    assert ctx.sender.calls == []


def test_a_failure_that_cannot_be_retried_is_reported_at_once_and_a_mail_error_is_survived(ctx):
    setup(ctx)
    sched = Scheduler(ctx.manager)
    failed_run(ctx, DAY.replace(hour=8), "RuntimeError: out of memory")
    assert sched.maybe_alert(DAY.replace(hour=12)) is False                                         # unexpected error: another attempt comes first
    ctx.store.db.execute("DELETE FROM runs")
    failed_run(ctx, DAY.replace(hour=8), "No portal is switched on")                                 # setup problem: no retry will come
    tries = []

    def boom(*a, **k):
        tries.append(1)
        raise OSError("mail down")
    ctx.manager.sender = boom
    assert sched.maybe_alert(DAY.replace(hour=12)) is False and len(tries) == 1                      # logged, not raised
    assert sched.maybe_alert(DAY.replace(hour=12, minute=5)) is False and len(tries) == 1            # not retried every 30 seconds
    ctx.manager.sender = ctx.sender
    assert sched.maybe_alert(DAY.replace(hour=12, minute=20)) is True and len(ctx.sender.calls) == 1  # tried again later, then sent
