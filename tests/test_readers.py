"""Reading does not queue behind writing: the heavy reads (tender list, dashboard figures, filter lists) use connections of their own in WAL mode.

What must stay true: a reader sees every save as soon as it is committed (never a stale list after a star, a hide or a new tender), a reader can change nothing,
and a slow write or a long hold on the shared lock does not make a read wait."""
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest

from tender_digest.gepnic import Tender
from tender_digest.store import SeenStore

NOW = datetime(2026, 11, 16, 10, 0)


def tender(n, days=7, dept="PWD", category="Works"):
    return Tender(portal="maha", tender_id=f"T{n}", title=f"Road work {n}", reference="", description=f"Road work {n}", organisation=f"{dept}||Division", category=category,
                  location="Nashik", value=2e6, emd=None, published=NOW, closing=NOW + timedelta(days=days), opening=None)


@pytest.fixture
def store(tmp_path):
    s = SeenStore(str(tmp_path / "t.db"))
    for i in range(6):
        s.keep(tender(i), NOW)
    yield s
    s.close()


def listing(s):
    rows, total = s.search(NOW, {}, "closing", 1000, 0)
    return total, [(r["key"], r["starred"], r["emailed"]) for r in rows]


def same_as_the_shared_connection(s):
    """What the readers answer, against what the one shared connection answers."""
    fast = (listing(s), s.stats(NOW)["kpi"], s.facets(NOW), s.categories())
    s._readers_ok = False
    try:
        s._memo_vals.clear()
        old = (listing(s), s.stats(NOW)["kpi"], s.facets(NOW), s.categories())
    finally:
        s._readers_ok = True
        s._memo_vals.clear()
    return fast == old


def test_readers_are_used_for_a_wal_file_and_not_otherwise(tmp_path, monkeypatch):
    assert SeenStore(str(tmp_path / "a.db"))._readers_ok is True
    if os.environ.get("TENDAI_TEST_FILE_DB") != "1":                       # (that switch turns every in-memory store into a file, which is its point)
        assert SeenStore(":memory:")._readers_ok is False                  # a second connection to :memory: would be a different, empty database
    monkeypatch.setenv("DB_WAL", "0")
    assert SeenStore(str(tmp_path / "b.db"))._readers_ok is False          # without WAL a reader and a writer block each other: keep the old way
    monkeypatch.delenv("DB_WAL")
    monkeypatch.setenv("DB_READERS", "0")
    assert SeenStore(str(tmp_path / "c.db"))._readers_ok is False          # the off-switch


def test_a_reader_sees_every_save_at_once(store):
    assert listing(store)[0] == 6 and same_as_the_shared_connection(store)
    store.keep(tender(50), NOW)
    assert listing(store)[0] == 7
    key = listing(store)[1][0][0]
    store.set_starred(key, True, NOW)
    assert dict((k, st) for k, st, _ in listing(store)[1])[key] is True and store.stats(NOW)["kpi"]["starred"] == 1
    store.mark_emailed([key])
    assert dict((k, e) for k, _, e in listing(store)[1])[key] in (1, True)
    store.hide([key], NOW)
    assert listing(store)[0] == 6 and store.stats(NOW)["kpi"]["starred"] == 0
    store.unhide([key])
    assert listing(store)[0] == 7
    store.keep(tender(51, category="Drainage"), NOW)
    assert "Drainage" in store.categories() and "Drainage" in {f["value"] for f in store.facets(NOW)["category"]}
    store.mute_department("PWD", NOW)
    assert same_as_the_shared_connection(store)
    store.unmute_department("PWD")
    assert store.delete_portal("maha") and listing(store)[0] == 0
    assert same_as_the_shared_connection(store)


def test_no_save_leaves_a_change_waiting_that_a_reader_could_not_see(store):
    """A reader sees only what is committed. Every public save must commit before it returns."""
    key = listing(store)[1][0][0]
    calls = [
        lambda: store.keep(tender(60), NOW), lambda: store.set_starred(key, True, NOW), lambda: store.mark_emailed([key]), lambda: store.hide([key], NOW),
        lambda: store.unhide([key]), lambda: store.mute_department("PWD", NOW), lambda: store.unmute_department("PWD"),
        lambda: store.put_settings({"min_value": 5}), lambda: store.recompute_priorities(NOW), lambda: store.set_ai_text(key, "summary", "x"),
    ]
    for call in calls:
        call()
        assert store.db.in_transaction is False, call


def test_a_reader_cannot_change_anything(store):
    with store.reading() as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.execute("DELETE FROM seen")
    assert listing(store)[0] == 6


def test_each_thread_reads_on_its_own_connection(store):
    seen = []
    def grab():
        with store.reading() as db:
            seen.append(id(db))
    with store.reading() as mine:
        main_id = id(mine)
    ts = [threading.Thread(target=grab) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(seen + [main_id])) == 4 and main_id != id(store.db)


def test_everything_read_in_one_block_comes_from_one_saved_state(store):
    with store.reading() as db:
        before = db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        store.keep(tender(70), NOW)                                       # saved from "another request" while this one is reading
        assert db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == before
    with store.reading() as db:
        assert db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == before + 1


def test_remembered_answers_follow_commits_and_never_show_an_unsaved_change(store):
    first = store.stats(NOW)["kpi"]["open"]
    store.db.execute("UPDATE seen SET closing = ? WHERE rowid = 1", ((NOW - timedelta(days=3)).isoformat(),))    # a change that is not saved yet
    assert store.stats(NOW)["kpi"]["open"] == first                       # not visible to readers, and not cached as if it were
    store.db.commit()
    assert store.stats(NOW)["kpi"]["open"] == first - 1                   # saved: the next look is worked out again


def test_a_read_does_not_wait_for_the_shared_lock_but_it_used_to(store):
    held, release = threading.Event(), threading.Event()
    def hold():
        with store.lock:
            held.set()
            release.wait(3)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(2)
    try:
        started = time.time()
        assert listing(store)[0] == 6 and store.stats(NOW)["kpi"]["open"] >= 0 and store.facets(NOW) and store.categories() == ["Works"]
        assert time.time() - started < 0.5                                # the lock is held for up to 3 s, the reads did not wait for it
        store._readers_ok = False                                          # the control: the old way does wait
        store._memo_vals.clear()
        result = {}
        waiter = threading.Thread(target=lambda: result.setdefault("done", (listing(store), time.time())))
        waiter.start()
        time.sleep(0.4)
        assert "done" not in result                                        # still waiting for the lock
    finally:
        release.set()
        t.join()
        waiter.join(3)
        store._readers_ok = True


def test_readers_and_a_writer_at_the_same_time_never_fail_and_never_see_half_a_save(store):
    stop, errors, totals = threading.Event(), [], []

    def write():
        try:
            for i in range(100, 400):
                store.keep(tender(i), NOW)
                if stop.is_set():
                    break
        except Exception as e:
            errors.append(("writer", repr(e)))

    def read():
        try:
            while not stop.is_set():
                total, rows = listing(store)
                assert total == len(rows)                                  # the count and the rows come from one saved state
                totals.append(total)
                k = store.stats(NOW)["kpi"]
                assert k["open"] >= 0 and k["total_kept"] >= k["open"]
        except Exception as e:
            errors.append(("reader", repr(e)))

    w = threading.Thread(target=write)
    rs = [threading.Thread(target=read) for _ in range(4)]
    [r.start() for r in rs]
    w.start()
    w.join(30)
    stop.set()
    [r.join(10) for r in rs]
    assert errors == [] and totals
    assert max(totals) <= 6 + 300 and listing(store)[0] == 6 + 300


def test_closing_the_store_closes_the_reader_connections(tmp_path):
    s = SeenStore(str(tmp_path / "c.db"))
    s.keep(tender(1), NOW)
    listing(s)
    s.stats(NOW)
    readers = list(s._opened)
    assert readers
    s.close()
    for db in readers:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
