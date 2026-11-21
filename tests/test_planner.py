"""Choosing which open bids to submit when earnest-money cash is limited."""
import itertools
import random

import pytest

from tender_digest import planner, winmodel
from tests.test_winmodel import make


def cand(i, value=10_000_000, emd=200_000, p=0.3, closing="2026-12-30T17:00", source="your overall win rate"):
    return {"id": i, "title": f"Bid {i}", "tender_id": f"T{i}", "value": value, "emd": emd, "closing": closing, "stage": "preparing", "p": p, "p_source": source}


# ---- the optimiser -----------------------------------------------------------------------------------------------------

def best_by_brute_force(items, cap):
    best, pick = 0.0, ()
    for r in range(len(items) + 1):
        for combo in itertools.combinations(range(len(items)), r):
            if sum(items[i][0] for i in combo) <= cap and sum(items[i][1] for i in combo) > best + 1e-9:
                best, pick = sum(items[i][1] for i in combo), combo
    return best, list(pick)


def test_the_chosen_set_is_exactly_the_best_one_within_the_cash():
    rng = random.Random(11)
    for _ in range(60):
        items = [(rng.choice([50_000, 100_000, 150_000, 200_000, 350_000]), rng.uniform(1_000, 90_000)) for _ in range(rng.randint(1, 9))]
        cap = rng.choice([100_000, 300_000, 500_000, 800_000])
        got = planner.choose(items, cap)
        want, _ = best_by_brute_force(items, cap)
        assert sum(items[i][0] for i in got) <= cap
        assert sum(items[i][1] for i in got) == pytest.approx(want, rel=1e-9)


def test_cash_goes_to_the_better_use_not_the_first_come():
    # one big EMD for a modest gain, or two small ones that together earn more
    items = [(400_000, 50_000.0), (200_000, 40_000.0), (200_000, 40_000.0)]
    assert planner.choose(items, 400_000) == [1, 2]
    assert planner.choose(items, 600_000) in ([0, 1], [0, 2])
    assert planner.choose(items, 800_000) == [0, 1, 2]
    assert planner.choose(items, 100_000) == [] and planner.choose([], 100_000) == []


def test_a_very_large_budget_keeps_the_table_small_and_still_exact_enough():
    assert planner._unit(1e6) == 5000 and planner._unit(1e10) > 5000
    assert planner.choose([(2e9, 5.0), (3e9, 6.0)], 4e9) == [1]


# ---- the plan ----------------------------------------------------------------------------------------------------------

def test_expected_profit_is_chance_times_estimate_times_margin():
    assert planner.expected_profit(0.3, 10_000_000, 8) == 240_000 and planner.expected_profit(0.5, None, 8) == 0


def test_the_plan_submits_the_best_set_and_says_why_each_other_bid_is_left_out():
    cands = [cand(1, emd=300_000, p=0.5), cand(2, emd=300_000, p=0.1), cand(3, emd=100_000, p=0.4), cand(4, value=None), cand(5, emd=9_000_000, p=0.9)]
    out = planner.plan(cands, 450_000, 8)
    by = {r["id"]: r for r in out["rows"]}
    assert {i for i, r in by.items() if r["decision"] == "submit"} == {1, 3} and out["emd_used"] == 400_000 and out["left"] == 50_000 and out["submit"] == 2
    assert out["expected_profit"] == by[1]["profit"] + by[3]["profit"]
    assert "earns more expected profit on the bids chosen" in by[2]["reason"] and "no estimated value" in by[4]["reason"] and "more than all the cash" in by[5]["reason"]
    assert [r["id"] for r in out["rows"][:2]] == [1, 3]                                                         # what to submit comes first
    assert out["with_more_cash"]["id"] == 5 and out["with_more_cash"]["extra_cash"] == 9_000_000 - 50_000       # the most profitable bid left out, and what it would take to add it


def test_it_says_how_much_more_cash_would_add_the_next_best_bid():
    out = planner.plan([cand(1, emd=300_000, p=0.5), cand(2, emd=300_000, p=0.4)], 400_000, 10)
    assert out["submit"] == 1 and out["with_more_cash"]["id"] == 2 and out["with_more_cash"]["extra_cash"] == 200_000


def test_with_no_cash_figure_every_bid_with_a_value_is_listed_and_nothing_is_cut():
    out = planner.plan([cand(1), cand(2), cand(3, value=None)], None, 8)
    assert out["submit"] == 2 and out["available"] is None and out["left"] is None and out["with_more_cash"] is None


def test_zero_cash_submits_nothing_except_bids_that_need_no_emd():
    out = planner.plan([cand(1, emd=200_000), cand(2, emd=0)], 0, 8)
    assert {r["id"] for r in out["rows"] if r["decision"] == "submit"} == {2}


def test_an_unknown_emd_is_treated_as_nothing_and_flagged():
    out = planner.plan([cand(1, emd=None)], 100_000, 8)
    assert out["rows"][0]["decision"] == "submit" and out["rows"][0]["emd_known"] is False and out["emd_unknown"] == 1


# ---- where each chance of winning comes from ---------------------------------------------------------------------------

def test_the_predictor_uses_the_model_only_when_it_has_earned_it_then_the_overall_rate_then_a_labelled_assumption():
    assert winmodel.Predictor([]).chance({}) == (winmodel.DEFAULT_CHANCE, "an assumed 20% (not enough history)")
    few = winmodel.Predictor(make(12))
    p, src = few.chance({"organisation": "PWD||x"})
    assert src == "your overall win rate" and p == pytest.approx((few.wins + 1) / (few.n + 2))
    many = winmodel.Predictor(make(120))
    pwd, src2 = many.chance({"work_type": "Roads", "organisation": "PWD||x", "value": 5e6, "portal": "maha"})
    water, _ = many.chance({"work_type": "Roads", "organisation": "Water||x", "value": 5e6, "portal": "maha"})
    assert src2 == "your win model" and pwd > water + 0.2


# ---- the endpoint ------------------------------------------------------------------------------------------------------

def test_the_plan_endpoint_picks_open_bids_that_have_not_closed_and_needs_a_bidder_role():
    from fastapi.testclient import TestClient

    from tender_digest import web
    from tender_digest.runner import RunManager
    from tender_digest.store import SeenStore
    from tests.test_portals import PASSWORD, env_settings
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    assert c.get("/api/plan").status_code == 401
    c.post("/api/login", json={"password": PASSWORD})
    ins = ("INSERT INTO bids (key, tender_id, portal, title, work_type, organisation, value, emd, closing, stage, created, updated, stage_changed) "
           "VALUES (?, ?, 'maha', ?, 'Roads', 'PWD||x', ?, ?, ?, ?, '2026-01-01', '2026-01-01', '2026-01-01')")
    store.db.execute(ins, ("maha:A", "A", "Road A", 10_000_000, 200_000, "2099-01-01T10:00:00", "preparing"))
    store.db.execute(ins, ("maha:B", "B", "Road B", 20_000_000, 400_000, "2099-01-02T10:00:00", "interested"))
    store.db.execute(ins, ("maha:C", "C", "Road C (closed)", 20_000_000, 400_000, "2020-01-01T10:00:00", "preparing"))
    store.db.execute(ins, ("maha:D", "D", "Road D (done)", 20_000_000, 400_000, "2099-01-01T10:00:00", "lost"))
    store.db.commit()
    out = c.get("/api/plan", params={"budget": 450_000, "margin": 8}).json()
    assert out["candidates"] == 2 and out["submit"] == 1 and out["emd_used"] in (200_000, 400_000) and out["rows"][0]["p_source"] == "an assumed 20% (not enough history)"
    assert out["cash_locked"] == 0 and c.get("/api/plan").json()["available"] is None
    assert c.get("/api/plan", params={"budget": -5}).status_code == 422 and c.get("/api/plan", params={"margin": 99}).status_code == 422
