"""A failed daily run is retried later the same day, but only when the failure may pass."""
from __future__ import annotations

from datetime import datetime, timedelta

from tender_digest.runner import MAX_ATTEMPTS, Scheduler
from tests.test_web import ctx  # noqa: F401  (the fixture)

DAY = datetime(2026, 11, 14)


def failed_run(ctx, at, error, status="failed"):
    rid = ctx.store.create_run("scheduled", [], True, at)
    ctx.store.update_run(rid, status=status, finished=(at + timedelta(minutes=20)).isoformat(timespec="seconds"), error=error)


def test_a_run_killed_by_a_restart_is_tried_again_after_a_pause(ctx):
    sched = Scheduler(ctx.manager)
    failed_run(ctx, DAY.replace(hour=8), "Interrupted by a restart of the service")        # finished 08:20
    assert not sched.due(DAY.replace(hour=8, minute=25))                                    # too soon
    assert sched.due(DAY.replace(hour=8, minute=30))


def test_retries_stop_after_the_daily_limit(ctx):
    sched = Scheduler(ctx.manager)
    for i in range(MAX_ATTEMPTS):
        failed_run(ctx, DAY.replace(hour=8 + i), "Every portal failed")
    assert not sched.due(DAY.replace(hour=23))
    assert sched.due(DAY.replace(hour=8) + timedelta(days=1))                               # a fresh day starts again


def test_only_failures_that_may_pass_are_retried(ctx):
    sched = Scheduler(ctx.manager)
    failed_run(ctx, DAY.replace(hour=8), "Email is not set up: BREVO_API_KEY and MAIL_FROM are missing on the server")
    assert not sched.due(DAY.replace(hour=12))                                              # setup problem: retrying changes nothing
    ctx.store.db.execute("DELETE FROM runs")
    failed_run(ctx, DAY.replace(hour=8), "OperationalError: database is locked")
    assert sched.due(DAY.replace(hour=12))                                                  # an unexpected error: try again
    ctx.store.db.execute("DELETE FROM runs")
    failed_run(ctx, DAY.replace(hour=8), None, status="ok")
    assert not sched.due(DAY.replace(hour=12))                                              # it worked: done for the day
