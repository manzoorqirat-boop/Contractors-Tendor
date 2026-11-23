"""The speed-ups must never change an answer: what is remembered is handed out only while nothing has been written, and a changed score is always written."""
import threading
import time
from datetime import datetime, timedelta

from tender_digest.gepnic import Tender
from tender_digest.store import SeenStore
from tender_digest.web import Background

NOW = datetime(2026, 11, 16, 10, 0)


def tender(n, days=7, category="Works"):
    return Tender(portal="maha", tender_id=f"T{n}", title=f"Road {n}", reference="", description=f"Road {n}", organisation="PWD||Division", category=category, location="Nashik",
                  value=2e6, emd=None, published=NOW, closing=NOW + timedelta(days=days), opening=None)


def store(n=5):
    s = SeenStore(":memory:")
    for i in range(n):
        s.keep(tender(i), NOW)
    return s


def test_dashboard_figures_are_worked_out_once_then_remembered_until_anything_is_written(monkeypatch):
    s = store()
    runs = []
    real = s._stats
    monkeypatch.setattr(s, "_stats", lambda *a, **k: (runs.append(1), real(*a, **k))[1])
    first = s.stats(NOW)
    assert s.stats(NOW) == first and len(runs) == 1                       # the second look costs nothing
    s.keep(tender(99), NOW)                                               # any write: the figures are worked out again and are correct
    assert s.stats(NOW)["kpi"]["open"] == first["kpi"]["open"] + 1 and len(runs) == 2
    s.stats(NOW, 3, {"portal": "maha"})
    assert len(runs) == 3                                                 # other filters are other answers


def test_a_remembered_answer_is_a_copy_so_a_caller_who_changes_it_changes_nothing_else():
    s = store()
    a = s.stats(NOW)
    a["kpi"]["open"] = -1
    a["by_portal"].clear()
    b = s.stats(NOW)
    assert b["kpi"]["open"] == 5 and b["by_portal"]


def test_the_filter_lists_and_categories_follow_the_data():
    s = store(3)
    assert s.categories() == ["Works"] and [f["value"] for f in s.facets(NOW)["category"]] == ["Works"]
    s.keep(tender(50, category="Drainage"), NOW)
    assert s.categories() == ["Drainage", "Works"]
    assert {f["value"] for f in s.facets(NOW)["category"]} == {"Works", "Drainage"}
    s.db.execute("DELETE FROM seen WHERE category = 'Drainage'")        # deleted by hand, as a clean-up would
    s.db.commit()
    assert s.categories() == ["Works"]


def test_a_star_is_seen_in_the_figures_at_once():
    s = store()
    key = s.search(NOW, {}, "closing", 1, 0)[0][0]["key"]
    assert s.stats(NOW)["kpi"]["starred"] == 0
    s.db.execute("INSERT INTO starred (key, created) VALUES (?, ?)", (key, NOW.isoformat()))
    s.db.commit()
    assert s.stats(NOW)["kpi"]["starred"] == 1


def test_only_the_scores_that_changed_are_written_and_they_are_still_right():
    s = store(6)
    assert s.recompute_priorities(NOW) == 6
    before = [tuple(r) for r in s.db.execute("SELECT key, priority, priority_note FROM seen ORDER BY key")]
    writes = s.db.total_changes
    assert s.recompute_priorities(NOW) == 6                               # nothing changed since: all six are scored again, none rewritten
    assert s.db.total_changes == writes
    s.recompute_priorities(NOW + timedelta(days=6))                       # time passed: urgency moves, so scores change and are written
    after = [tuple(r) for r in s.db.execute("SELECT key, priority, priority_note FROM seen ORDER BY key")]
    assert after != before and s.db.total_changes > writes


def test_background_work_runs_off_the_callers_thread_and_a_burst_of_asks_is_folded():
    ran, gate, started = [], threading.Event(), threading.Event()

    def job():
        ran.append(threading.current_thread().name)
        started.set()
        gate.wait(2)

    bg = Background(job)
    t0 = time.time()
    bg.ask()
    assert started.wait(2) and time.time() - t0 < 1 and ran[0] != threading.current_thread().name
    for _ in range(20):                                                   # twenty clicks while it is busy
        bg.ask()
    gate.set()
    for _ in range(100):
        if len(ran) >= 2:
            break
        time.sleep(0.02)
    time.sleep(0.2)
    assert len(ran) == 2                                                  # the first run, and one more for all twenty
