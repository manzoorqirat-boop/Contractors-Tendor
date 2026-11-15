"""Price advisor: the numbers, the history it learns from, and the endpoint."""
from __future__ import annotations

import pytest

from tender_digest import bids
from tender_digest.boq import advisor
from tests.test_bids import app_ctx  # noqa: F401
from tests.test_boq_api import b64, load_catalogue, start_bid


def test_price_for_margin_and_win_chance():
    assert advisor.price_for_margin(90, 10) == pytest.approx(100)
    outs = [-8.0, -5.0, -3.0, 0.0, 2.0]
    assert advisor.win_chance(-4, outs) == 60.0          # three of five winners priced at or above -4%
    assert advisor.win_chance(-4, outs[:2]) is None      # fewer than 3 results: no number
    assert advisor.confidence(2) == "none" and advisor.confidence(4) == "rough" and advisor.confidence(9) == "fair" and advisor.confidence(30) == "good"


def test_advice_with_history_recommends_a_safe_quote():
    out = advisor.advise(90_000_000, 96_000_000, 100_000_000, [-9, -8, -7, -6, -5, -4, -3, -2, 0, 3], 3, 8, "roads tenders")
    assert out["samples"] == 10 and out["confidence"] == "fair"
    assert out["floor_pct"] == pytest.approx(-1.03, abs=0.05)                   # 96m / 0.97 = 98.97m, against 100m
    assert out["recommended_pct"] >= out["floor_pct"] - 1e-9                    # never recommends a quote that loses money at the worst case
    wins = [p["win"] for p in out["points"]]
    assert wins == sorted(wins, reverse=True)                                     # the dearer the quote, the lower the chance
    assert all(p["margin_best"] >= p["margin_worst"] for p in out["points"])


def test_advice_without_history_falls_back_to_the_target_margin():
    out = advisor.advise(90_000_000, 90_000_000, 100_000_000, [-5, -3], 3, 8)
    assert out["confidence"] == "none" and all(p["win"] is None and p["expected"] is None for p in out["points"])
    assert out["recommended_pct"] == pytest.approx(out["target_pct"], abs=0.5) and any("not enough recorded results" in n for n in out["notes"])


def test_unlikely_win_and_bad_inputs():
    out = advisor.advise(95_000_000, 99_000_000, 100_000_000, [-20, -18, -15, -14, -12], 3, 8)
    assert any("winning is unlikely" in n for n in out["notes"])
    for args in ((0, 1, 100, [], 3, 8), (2, 1, 100, [], 3, 8), (1, 2, 0, [], 3, 8), (1, 2, 100, [], 8, 3), (1, 2, 100, [], 3, 60)):
        with pytest.raises(ValueError):
            advisor.advise(*args)


def test_history_prefers_similar_tenders_and_counts_a_win_at_your_price():
    def row(stage, wt, value, mine, l1):
        return {"stage": stage, "work_type": wt, "value": value, "my_value": mine, "l1_value": l1}
    roads = [row("lost", "Roads", 100, 95, 90 + i) for i in range(5)]
    rows = roads + [row("won", "Buildings", 100, 80, None), row("lost", "Buildings", 100, 99, None), row("lost", "Roads", None, 50, 40)]
    pcts, basis = bids.l1_outcomes_from(rows, "Roads", 100)
    assert basis == "Roads tenders" and pcts == pytest.approx([-10, -9, -8, -7, -6])
    pcts, basis = bids.l1_outcomes_from(rows, "Bridges", 5_000_000)          # nothing similar: every usable decided bid
    assert basis == "all your decided bids" and len(pcts) == 6 and -20 in [round(p) for p in pcts]
    assert bids.l1_outcomes_from([], "Roads", 100) == ([], "")


def test_advice_endpoint(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    assert c.get(f"/api/bids/{bid}/advice").status_code == 422                 # no BOQ yet
    assert c.get("/api/bids/9999/advice").status_code == 404
    load_catalogue(app_ctx)
    c.post(f"/api/bids/{bid}/boq", json={"filename": "boq.csv", "file_base64": b64("boq_sample.csv")})
    c.get(f"/api/bids/{bid}/boq", params={"include_review": True})
    out = c.get(f"/api/bids/{bid}/advice", params={"min_margin": 2, "target_margin": 10})
    assert out.status_code == 200
    body = out.json()
    assert body["mode"] == "boq_only" and body["points"] and any("Risk allowances" in n for n in body["notes"])
    assert body["min_margin"] == 2 and body["target_margin"] == 10 and body["confidence"] == "none"
    assert c.get(f"/api/bids/{bid}/advice", params={"min_margin": 9, "target_margin": 5}).status_code == 422
