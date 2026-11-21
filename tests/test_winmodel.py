"""The win model: simple on purpose, honest about when it knows nothing, and shown only once it has beaten the plain win rate on your own history."""
import random
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import web, winmodel as w
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD, env_settings

MODERATE = {"PWD": 0.65, "Water": 0.2, "Rural": 0.35}      # a real but ordinary difference between departments
STRONG = {"PWD": 0.85, "Water": 0.08, "Rural": 0.4}


def make(n, structured=True, seed=1, habits=STRONG):
    """Decided bids in date order. With structure, the department really does change the odds; without, nothing does."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        dept = rng.choice(list(habits))
        p = habits[dept] if structured else 0.35
        out.append({"stage": "won" if rng.random() < p else "lost", "work_type": rng.choice(["Roads", "Buildings"]), "organisation": dept + "||Division",
                    "value": rng.choice([5e5, 5e6, 5e7]), "portal": "maha", "stage_changed": (datetime(2025, 1, 1) + timedelta(days=5 * i)).isoformat()})
    return out


def candidate(dept="PWD", **over):
    return {"work_type": "Roads", "organisation": dept + "||Division", "value": 5e6, "portal": "maha", **over}


# ---- the parts ---------------------------------------------------------------------------------------------------------

def test_what_is_known_about_a_bid_is_turned_into_features_and_the_rest_is_left_out():
    f = w.features({"work_type": "Roads", "organisation": "PWD||Nashik Circle", "value": 5e6, "portal": "maha", "fit": 4, "my_value": 4.5e6})
    assert f == {"work type": "Roads", "department": "pwd", "size": "10 lakh to 1 crore", "portal": "maha", "fit": "fit 4 to 5", "price": "quoted 3 to 10% below the estimate"}
    assert w.features({"work_type": None, "organisation": None, "value": None}) == {}
    assert w.quote_bucket(95, 100) == "quoted 3 to 10% below the estimate" and w.quote_bucket(80, 100) == "quoted over 10% below the estimate"
    assert w.quote_bucket(101, 100) == "quoted within 3% of the estimate" and w.quote_bucket(110, 100) == "quoted above the estimate" and w.quote_bucket(None, 100) == ""


def test_only_won_and_lost_bids_teach_it():
    rows = w.rows_from([{"stage": s, "work_type": "Roads"} for s in ("won", "lost", "dropped", "interested", "submitted", "preparing")])
    assert [r["won"] for r in rows] == [True, False]


def test_a_group_of_two_bids_barely_moves_the_answer_but_twenty_do():
    rows = [{"won": False, "feats": {"department": "other"}, "when": None} for _ in range(40)] + [{"won": True, "feats": {"department": "pwd"}, "when": None} for _ in range(2)]
    small = w.predict(w.fit(rows), {"department": "pwd"})
    rows += [{"won": True, "feats": {"department": "pwd"}, "when": None} for _ in range(18)]
    big = w.predict(w.fit(rows), {"department": "pwd"})
    assert small["p"] < 0.4 < 0.6 < big["p"] and big["p"] - small["p"] > 0.25     # 2 of 2 wins is only half believed; 20 of 20 is trusted


def test_a_value_it_has_never_seen_changes_nothing():
    model = w.fit(w.rows_from(make(60)))
    unseen = w.predict(model, {"department": "no such department", "work type": "Tunnels"})
    assert unseen["p"] == pytest.approx(model["base"]) and unseen["parts"] == []


def test_overlapping_features_are_damped_so_the_answer_is_not_overconfident():
    rows = [{"won": True, "feats": {"department": "pwd", "work type": "Roads", "size": "x", "portal": "p"}, "when": None} for _ in range(10)] \
        + [{"won": False, "feats": {"department": "z", "work type": "Z", "size": "y", "portal": "q"}, "when": None} for _ in range(10)]
    model = w.fit(rows)
    one, four = w.predict(model, {"department": "pwd"})["p"], w.predict(model, {"department": "pwd", "work type": "Roads", "size": "x", "portal": "p"})["p"]
    assert one < four < 0.97 and four - one < 0.25               # more evidence helps, but four copies of the same signal are not counted four times
    assert 0.03 < w.predict(model, {"department": "z", "work type": "Z", "size": "y", "portal": "q"})["p"] < 0.5


# ---- when a score is shown ---------------------------------------------------------------------------------------------

def test_with_too_few_decided_bids_there_is_no_score_only_the_plain_record():
    out = w.assess(make(25), candidate())
    assert out["state"] == "too_few" and out["score_pct"] is None and out["decided"] == 25 and out["need"] == w.MIN_DECIDED and out["overall_pct"] is not None
    assert w.assess([])["overall_pct"] is None


def test_with_real_structure_and_enough_history_a_score_is_earned_and_explained():
    out = w.assess(make(120), candidate("PWD"))
    assert out["state"] == "ready" and out["test"]["reliable"] and out["test"]["brier_model"] < out["test"]["brier_baseline"]
    assert out["parts"][0]["feature"] == "department" and out["parts"][0]["shift"] > 0
    assert all(abs(p["shift"]) >= 0.15 for p in out["parts"])                     # features that hardly moved the answer are not "explained"
    low = w.assess(make(120), candidate("Water"))["score_pct"]
    assert out["score_pct"] > low + 15                           # it tells the department you win from the one you rarely do


def test_with_ordinary_structure_it_is_found_more_often_than_not_and_nothing_is_found_where_there_is_none():
    found = sum(w.assess(make(100, True, s, MODERATE))["state"] == "ready" for s in range(40))
    wrongly = sum(w.assess(make(100, False, s))["state"] == "ready" for s in range(40))
    assert found >= 20 and wrongly <= 4                                          # measured at about 65% found and 1 to 5% false alarms with 100 bids


def test_when_nothing_in_your_history_predicts_a_win_it_says_so_and_shows_no_score():
    states = [w.assess(make(80, structured=False, seed=s), candidate())["state"] for s in range(30)]
    assert states.count("ready") <= 4                            # a false "ready" is possible (that is what testing at 5% means) but rare
    sample = w.assess(make(80, structured=False, seed=3), candidate())
    assert sample["state"] == "no_edge" and sample["score_pct"] is None and sample["parts"] == []


def test_the_model_is_tested_on_each_bid_using_only_the_bids_before_it(monkeypatch):
    seen = []
    real = w.fit

    def spy(rows):
        seen.append(max(r["when"] for r in rows))
        return real(rows)

    monkeypatch.setattr(w, "fit", spy)
    rows = w.rows_from(make(60))
    w.backtest(rows)
    dates = sorted(r["when"] for r in rows)
    assert len(seen) == 60 - w.MIN_TRAIN and all(s <= dates[w.MIN_TRAIN + i] for i, s in enumerate(seen))


def test_the_backtest_reports_calibration_bands():
    t = w.backtest(w.rows_from(make(120)))
    assert t["tested"] == 120 - w.MIN_TRAIN and sum(b["n"] for b in t["calibration"]) == t["tested"]
    assert all(0 <= b["predicted_pct"] <= 100 and 0 <= b["actual_pct"] <= 100 for b in t["calibration"])
    undated = [{**r, "when": None} for r in w.rows_from(make(80))]
    assert w.backtest(undated)["tested"] == 0 and w.backtest(undated)["reliable"] is False


# ---- the endpoints -----------------------------------------------------------------------------------------------------

@pytest.fixture
def app():
    store, env = SeenStore(":memory:"), env_settings()
    created = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(created)
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return type("App", (), {"client": c, "store": store})


def add_bids(app, rows, open_dept="PWD"):
    for i, r in enumerate(rows):
        app.store.db.execute(
            "INSERT INTO bids (key, tender_id, portal, title, work_type, organisation, value, stage, created, updated, stage_changed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"maha:T{i}", f"T{i}", r["portal"], "t", r["work_type"], r["organisation"], r["value"], r["stage"], r["stage_changed"], r["stage_changed"], r["stage_changed"]))
    app.store.db.execute(
        "INSERT INTO bids (key, tender_id, portal, title, work_type, organisation, value, stage, created, updated, stage_changed) VALUES ('maha:OPEN', 'OPEN', 'maha', 't', 'Roads', ?, 5000000, 'preparing', '2026-11-01', '2026-11-01', '2026-11-01')",
        (open_dept + "||Division",))
    app.store.db.commit()
    return app.store.db.execute("SELECT id FROM bids WHERE key = 'maha:OPEN'").fetchone()[0]


def test_the_win_model_summary_says_where_the_history_stands(app):
    add_bids(app, make(12))
    out = app.client.get("/api/win-model").json()
    assert out["state"] == "too_few" and out["decided"] == 12 and out["need"] == w.MIN_DECIDED and out["score_pct"] is None


def test_an_open_bid_gets_a_score_once_the_history_has_earned_it(app):
    open_id = add_bids(app, make(120))
    out = app.client.get(f"/api/bids/{open_id}/win-chance").json()
    assert out["applies"] is True and out["state"] == "ready" and 0 < out["score_pct"] < 100 and out["parts"] and out["decided"] == 120


def test_a_bid_that_is_already_decided_gets_no_score(app):
    add_bids(app, make(120))
    decided = app.store.db.execute("SELECT id FROM bids WHERE stage IN ('won','lost') LIMIT 1").fetchone()[0]
    out = app.client.get(f"/api/bids/{decided}/win-chance").json()
    assert out["applies"] is False and out["score_pct"] is None


def test_the_win_endpoints_need_a_sign_in_and_a_real_bid(app):
    assert app.client.get("/api/bids/9999/win-chance").status_code == 404
    app.client.post("/api/logout")
    assert app.client.get("/api/win-model").status_code == 401 and app.client.get("/api/bids/1/win-chance").status_code == 401
