"""Forecasting how many tenders to expect, and which come round again: from the app's long memory, tested on its own history."""
import random
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import forecast, web
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD, env_settings

TODAY = date(2026, 11, 18)                      # a Wednesday
NOW = datetime(2026, 11, 18, 9, 0)


def tender(i, org="PWD||Nashik Circle", portal="maha", title="Repair of road", value=1e6, found=NOW):
    return Tender(portal=portal, tender_id=f"T{i}", title=title, reference="", description=title, organisation=org, category="Works", location="x", value=value, emd=1e4,
                  published=found, closing=found + timedelta(days=10), opening=found + timedelta(days=12))


def fill(store, days, per_day, dept="PWD", runs=True, start=None):
    """days of history ending yesterday: `per_day(day_index, date)` new tenders each day, and a successful run each day."""
    start = start or TODAY - timedelta(days=days)
    for i in range(days):
        d = start + timedelta(days=i)
        n = per_day(i, d)
        if n:
            store.db.execute("INSERT INTO flow (day, portal, dept, n) VALUES (?, 'maha', ?, ?) ON CONFLICT(day, portal, dept) DO UPDATE SET n = n + excluded.n", (d.isoformat(), dept, n))
        if runs:
            store.db.execute("INSERT INTO runs (trigger, status, started) VALUES ('schedule', 'ok', ?)", (d.isoformat() + "T08:00:00",))
    store.db.commit()


# ---- the long memory ---------------------------------------------------------------------------------------------------

def test_a_kept_tender_is_remembered_once_even_if_kept_again():
    store = SeenStore(":memory:")
    t = tender(1)
    store.keep(t, NOW)
    store.keep(t, NOW + timedelta(days=1))                                    # a re-read of the same tender is not a new one
    store.keep(tender(2, org="Water Resources||Pune"), NOW)
    assert {(r["day"], r["dept"]): r["n"] for r in store.flow_rows("2026-01-01")} == {("2026-11-18", "PWD"): 1, ("2026-11-18", "Water Resources"): 1}
    assert len(store.memory_rows("2026-01-01", "2026-12-31")) == 2


def test_a_tender_first_skipped_and_later_kept_counts_when_it_is_kept():
    store = SeenStore(":memory:")
    store.skip("maha:T9", NOW)
    store.keep(tender(9), NOW + timedelta(days=1))
    assert [r["n"] for r in store.flow_rows("2026-01-01")] == [1]


def test_sample_and_hand_added_tenders_never_count_as_market_activity():
    store = SeenStore(":memory:")
    store.keep(tender(1, portal="sample"), NOW)
    store.keep(tender(2, portal="manual"), NOW)
    store.keep(tender(3), NOW)
    assert [r["n"] for r in store.flow_rows("2026-01-01")] == [1] and len(store.memory_rows("2026-01-01", "2026-12-31")) == 1


def test_pruning_forgets_old_tenders_but_not_the_counts_and_keeps_the_memory_for_two_years():
    store = SeenStore(":memory:")
    store.keep(tender(1, found=datetime(2024, 1, 5)), datetime(2024, 1, 5))
    store.keep(tender(2, found=datetime(2025, 9, 1)), datetime(2025, 9, 1))
    store.prune(NOW, 45)
    assert store.count() == 0                                                  # the tenders themselves are gone
    assert {r["day"] for r in store.flow_rows("2020-01-01")} == {"2024-01-05", "2025-09-01"}   # but the counts are not
    assert [m["day"] for m in store.memory_rows("2020-01-01", "2030-01-01")] == ["2025-09-01"]   # memory older than about two years is dropped


def test_a_new_long_memory_is_filled_from_the_tenders_already_held(tmp_path):
    path = str(tmp_path / "s.db")
    old = SeenStore(path)
    old.keep(tender(1), NOW)
    old.keep(tender(2, org="Water||x"), NOW)
    old.db.execute("DROP TABLE flow")
    old.db.execute("DROP TABLE memory")
    old.db.commit()
    old.close()
    again = SeenStore(path)                                                    # an older database gains the tables and a first fill
    assert sum(r["n"] for r in again.flow_rows("2026-01-01")) == 2 and len(again.memory_rows("2026-01-01", "2026-12-31")) == 2
    again.close()


# ---- the weeks ---------------------------------------------------------------------------------------------------------

def test_only_weeks_the_app_was_collecting_count_and_a_partial_first_week_is_left_out():
    first = date(2026, 9, 2)                                                   # a Wednesday
    flow = [{"day": (first + timedelta(days=i)).isoformat(), "dept": "PWD", "n": 3} for i in range(70)]
    days = {(first + timedelta(days=i)).isoformat() for i in range(70)}
    series = forecast.weekly_counts(flow, days, first, date(2026, 11, 8))
    assert series[0][0] == date(2026, 9, 7) and all(c == 21 for _, c in series)        # full weeks only, from the first Monday
    off = {d for d in days if not ("2026-09-21" <= d <= "2026-09-27")}                  # the server was off for one whole week
    missing = forecast.weekly_counts(flow, off, first, date(2026, 11, 8))
    assert date(2026, 9, 21) not in [w for w, _ in missing] and len(missing) == len(series) - 1


def test_the_level_follows_recent_weeks_more_than_old_ones():
    assert forecast.level([10] * 8) == pytest.approx(10) and forecast.level([2] * 6 + [20, 20]) > forecast.level([20, 20] + [2] * 6)
    assert forecast.level([5]) == 5.0


def test_the_season_is_ignored_without_a_year_of_history_and_capped_and_halved_with_one():
    short = [(date(2026, 1, 5) + timedelta(days=7 * i), 10) for i in range(30)]
    assert forecast.season_factor(short, short[-1][0] + timedelta(days=7)) == 1.0
    # two years: a big spike in the same four weeks one year earlier
    series = [(date(2024, 11, 18) + timedelta(days=7 * i), 40 if 52 <= i < 56 else 10) for i in range(105)]
    target = series[-1][0] + timedelta(days=7)
    f = forecast.season_factor(series, target)
    assert 1.0 < f <= forecast.SEASON_CAP[1]                                  # raised, but only partway (one year is thin evidence)


# ---- the forecast ------------------------------------------------------------------------------------------------------

def test_a_steady_market_is_forecast_near_its_level_and_the_method_proves_itself():
    store = SeenStore(":memory:")
    rng = random.Random(2)
    fill(store, 20 * 7, lambda i, d: rng.choice([4, 5, 5, 6, 7]))             # about 5.4 a day, about 38 a week
    out = forecast.build(store, NOW)
    assert out["weeks_observed"] >= 18 and out["enough"] is True and out["backtest"]["tested"] >= 8
    assert out["total"]["expected"] == pytest.approx(4 * 38, rel=0.2)
    assert out["total"]["low"] < out["total"]["expected"] < out["total"]["high"] and out["total"]["range_from"]
    assert out["recent_four_weeks"] == pytest.approx(4 * 38, rel=0.25) and out["season_used"] is False
    assert out["departments"][0]["dept"] == "PWD" and out["departments"][0]["expected"] == out["total"]["expected"]


def test_a_new_installation_says_there_is_nothing_to_forecast_yet():
    store = SeenStore(":memory:")
    assert forecast.build(store, NOW)["enough"] is False and forecast.build(store, NOW)["total"] is None
    fill(store, 3 * 7, lambda i, d: 5)
    out = forecast.build(store, NOW)
    assert out["total"] is None and out["enough"] is False and out["weeks_observed"] < forecast.MIN_WEEKS


def test_a_server_that_was_off_does_not_look_like_a_quiet_market():
    store = SeenStore(":memory:")
    fill(store, 12 * 7, lambda i, d: 5, runs=False)                            # tenders were found but no run is recorded: the weeks cannot be trusted
    assert forecast.build(store, NOW)["weeks_observed"] == 0


def test_the_backtest_reports_when_the_method_does_not_beat_repeating_the_last_four_weeks():
    rng = random.Random(5)
    series = [(date(2025, 1, 6) + timedelta(days=7 * i), rng.choice([0, 60])) for i in range(60)]      # wild swings: nothing to learn
    bt = forecast.backtest(series)
    assert bt["tested"] >= forecast.MIN_BACKTEST and "mae" in bt and "naive_mae" in bt and isinstance(bt["reliable"], bool)
    assert forecast.backtest(series[:8])["reliable"] is False and forecast.backtest(series[:8])["tested"] < forecast.MIN_BACKTEST


# ---- coming round again ------------------------------------------------------------------------------------------------

def memory(day, dept, title, value=1e6, key=None):
    return {"key": key or f"k{day}{title}", "day": day.isoformat() if isinstance(day, date) else day, "portal": "maha", "dept": dept, "title": title, "value": value}


def test_words_ignore_numbers_years_and_filler():
    assert forecast.title_words("Annual Repair of Roads at Nashik 2025-26, Package 3") == {"annual", "repair", "roads", "nashik", "package"}
    assert "2025" not in forecast.title_words("Repair 2025-26") and "the" not in forecast.title_words("the repair of the road")
    assert forecast.similar({"a", "b", "c"}, {"a", "b", "d"}) == 0.5 and forecast.similar(set(), {"a"}) == 0.0


def test_a_tender_from_this_time_last_year_with_no_look_alike_this_season_is_listed():
    last_year = TODAY - timedelta(days=365) + timedelta(days=20)
    mem = [memory(last_year, "PWD", "Annual maintenance of rural roads, Nashik taluka 2025-26", value=9e6),
           memory(last_year, "PWD", "Annual maintenance of rural roads, Nashik taluka 2025-26", value=9e6, key="dup"),          # the same tender twice: listed once
           memory(last_year, "PWD", "Construction of bridge over river Godavari", value=4e7),
           memory(TODAY - timedelta(days=30), "PWD", "Construction of bridge over river Godavari 2026-27", value=5e7),         # already came round this season
           memory(TODAY - timedelta(days=400), "PWD", "Old tender outside the window", value=1e6)]
    out = forecast.coming_round(mem, TODAY)
    assert [r["title"] for r in out] == ["Annual maintenance of rural roads, Nashik taluka 2025-26"]
    assert out[0]["expected_from"] == (last_year + timedelta(days=365)).isoformat() and out[0]["value"] == 9e6


def test_a_look_alike_from_another_department_does_not_count_as_this_season():
    last_year = TODAY - timedelta(days=365) + timedelta(days=10)
    mem = [memory(last_year, "PWD", "Annual maintenance of rural roads Nashik"), memory(TODAY - timedelta(days=10), "Water Resources", "Annual maintenance of rural roads Nashik")]
    assert len(forecast.coming_round(mem, TODAY)) == 1


def test_the_watch_list_needs_a_year_of_history():
    store = SeenStore(":memory:")
    fill(store, 12 * 7, lambda i, d: 5)
    store.db.execute("INSERT INTO memory (key, day, portal, dept, title, value) VALUES ('k', ?, 'maha', 'PWD', 'Annual maintenance of rural roads Nashik', 1e6)", ((TODAY - timedelta(days=350)).isoformat(),))
    store.db.commit()
    assert forecast.build(store, NOW)["coming_round"] == []
    fill(store, 400, lambda i, d: 0, start=TODAY - timedelta(days=400))
    store.db.execute("INSERT INTO flow (day, portal, dept, n) VALUES (?, 'maha', 'PWD', 1)", ((TODAY - timedelta(days=399)).isoformat(),))
    store.db.commit()
    assert [r["title"] for r in forecast.build(store, NOW)["coming_round"]] == ["Annual maintenance of rural roads Nashik"]


# ---- the endpoint ------------------------------------------------------------------------------------------------------

def test_the_forecast_endpoint_answers_and_needs_a_sign_in():
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    assert c.get("/api/forecast").status_code == 401
    c.post("/api/login", json={"password": PASSWORD})
    out = c.get("/api/forecast").json()
    assert out["enough"] is False and out["total"] is None and out["weeks_observed"] == 0
