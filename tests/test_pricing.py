"""The learned price model: what it does with little and with much data, and whether its predictions hold up when tested against history."""
import random
from datetime import datetime, timedelta

import pytest

from tender_digest import pricing

NOW = datetime(2026, 11, 20)


def row(pct, wt="Roads", dept="PWD", days_ago=30, source="market", value=5_000_000):
    return {"pct": pct, "work_type": wt, "dept": dept.lower(), "band": pricing.band(value), "when": NOW - timedelta(days=days_ago), "source": source}


def est(rows, wt="Roads", dept="PWD", value=5_000_000):
    return pricing.estimate([dict(r) for r in rows], wt, dept, value, NOW)


# ---- one result -----------------------------------------------------------------------------------------------------

def test_a_result_is_l1_against_the_estimate():
    r = pricing.row_from(1_000_000, 920_000, work_type="Roads", organisation="PWD||Nashik Circle", awarded="2026-10-01", source="own")
    assert r["pct"] == pytest.approx(-8.0) and r["dept"] == "pwd" and r["band"] == "10 lakh to 1 crore" and r["when"] == datetime(2026, 10, 1)


@pytest.mark.parametrize("estimate,price", [(None, 5), (5, None), (0, 5), (5, 0), (-5, 5), (1_000_000, 100_000), (1_000_000, 9_000_000)])
def test_numbers_that_cannot_be_a_result_are_refused(estimate, price):
    assert pricing.row_from(estimate, price) is None


def test_a_bad_date_leaves_the_result_undated_not_lost():
    assert pricing.row_from(100, 95, awarded="soon")["when"] is None


# ---- the estimate ---------------------------------------------------------------------------------------------------

def test_no_results_means_no_estimate():
    out = pricing.estimate([], "Roads", "PWD", 1e6, NOW)
    assert out["outcomes"] == [] and out["samples"] == 0 and out["basis"] == ""


def test_the_answer_is_a_sorted_spread_with_ordered_quantiles():
    rng = random.Random(1)
    out = est([row(rng.gauss(-6, 4)) for _ in range(40)])
    assert len(out["outcomes"]) == pricing.GRID and out["outcomes"] == sorted(out["outcomes"])
    q = out["quantiles"]
    assert q["p10"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p90"] and -9 < q["p50"] < -3


def test_a_tiny_group_barely_moves_the_answer_but_a_big_one_decides_it():
    wide = [row(-5, wt="Buildings", dept="Other") for _ in range(60)]
    two = est(wide + [row(+12, dept="PWD") for _ in range(2)])
    forty = est(wide + [row(+12, dept="PWD") for _ in range(40)])
    assert two["quantiles"]["p50"] < -3                 # two results from this department: still mostly the wide picture
    assert forty["quantiles"]["p50"] > 5                # forty: the department's own habit wins


def test_the_most_specific_group_with_enough_results_is_named_as_the_basis():
    rows = [row(-4, wt="Roads", dept="PWD") for _ in range(6)] + [row(-9, wt="Roads", dept="Water") for _ in range(10)] + [row(-1, wt="Bridges", dept="PWD") for _ in range(7)]
    out = est(rows)
    assert out["samples"] == 6 and "Roads in this department" in out["basis"] and out["basis"].startswith("6 results")
    other = est(rows, wt="Drains", dept="Nowhere")
    assert other["samples"] == len(rows) and "department" not in other["basis"] and "Drains" not in other["basis"]     # nothing about this tender is specific: the wide picture


def test_the_basis_says_how_many_of_the_results_are_yours():
    rows = [row(-4, source="own") for _ in range(2)] + [row(-5) for _ in range(5)]
    out = est(rows)
    assert (out["own"], out["market"]) == (2, 5) and "2 yours, 5 imported" in out["basis"]


def test_newer_results_count_more_than_old_ones():
    old, new = [row(-15, days_ago=1800) for _ in range(20)], [row(-2, days_ago=20) for _ in range(20)]
    mixed = est(old + new)["quantiles"]["p50"]
    assert mixed > -8, "the recent -2% results should pull the middle well above the old -15%"
    assert est([row(p, days_ago=1800) for p in (-15,) * 20 + (-2,) * 20])["quantiles"]["p50"] == pytest.approx(-8.5, abs=7)   # all equally old: no preference


def test_an_undated_result_counts_a_little_less_than_a_recent_one():
    r = row(-5)
    r["when"] = None
    assert pricing.weight(r, NOW) == pricing.UNDATED_WEIGHT < pricing.weight(row(-5, days_ago=1), NOW)


def test_a_group_the_tender_cannot_name_is_left_out():
    out = pricing.estimate([row(-5, wt="") for _ in range(10)], None, None, None, NOW)
    assert [lv["level"] for lv in out["levels"]] == ["all results"]


# ---- does it work? --------------------------------------------------------------------------------------------------

def synthetic(n, structured=True, seed=7):
    """Results in date order where departments really do price differently (or, for the control, do not)."""
    rng = random.Random(seed)
    habits = {"PWD": -9.0, "Water": -2.0, "Rural": +3.0}
    out = []
    for i in range(n):
        dept = rng.choice(list(habits))
        mean = habits[dept] if structured else -3.0
        out.append(row(rng.gauss(mean, 3.0), dept=dept, days_ago=n - i))
    return out


def test_with_real_structure_the_model_beats_the_plain_pooled_median():
    res = pricing.backtest(synthetic(240), NOW)
    assert res["tested"] > 200
    assert res["better_than_plain_pct"] > 25                              # a genuine gain, in pinball loss over five quantiles
    assert res["median_error_pts"] < res["plain_median_error_pts"] * 0.7
    assert 35 <= res["inside_middle_half_pct"] <= 65 and 65 <= res["inside_middle_80_pct"] <= 92     # and its ranges are honest: about 50% and 80%


def test_with_no_structure_it_does_not_pretend_to_know_more(monkeypatch):
    res = pricing.backtest(synthetic(240, structured=False), NOW)
    assert res["better_than_plain_pct"] <= 8                              # nothing to learn, so no big claimed gain
    assert 35 <= res["inside_middle_half_pct"] <= 65


def test_the_backtest_only_looks_backwards(monkeypatch):
    seen = []
    real = pricing.estimate

    def spy(history, work_type, organisation, value, now):
        seen.append((max(h["when"] for h in history), now))          # the newest result it may use, and the date of the result it is predicting
        return real(history, work_type, organisation, value, now)

    monkeypatch.setattr(pricing, "estimate", spy)
    pricing.backtest(synthetic(60), NOW)
    assert len(seen) == 60 - 15 and all(newest <= predicted for newest, predicted in seen)


def test_the_backtest_needs_enough_dated_history():
    assert pricing.backtest(synthetic(10), NOW)["tested"] == 0
    undated = [dict(r, when=None) for r in synthetic(60)]
    assert pricing.backtest(undated, NOW) == {"tested": 0, "needed": 16, "have": 0}
