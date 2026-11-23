"""The test-data script promises that the data-driven features have something to show straight away. This keeps that promise true."""
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tender_digest import web
from tender_digest.config import Portal, Settings
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD

SCRIPT = Path(__file__).resolve().parent.parent / "testdata" / "seed_intelligence.py"


def load():
    spec = importlib.util.spec_from_file_location("seed_intelligence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_intelligence"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("seed") / "demo.db")
    assert load().main(["--db", path]) == 0
    return path


@pytest.fixture
def client(seeded):
    store = SeenStore(seeded)
    env = Settings(portals=[Portal(k, k.title(), k.title(), f"https://{k}.gov.in/nicgep") for k in ("maharashtra", "delhi", "up", "central")], recipients=["a@x.in"], brevo_api_key="k",
                   mail_from="f@x.in", mail_from_name="T", state_path=":memory:", request_delay=0)       # the four portals the real installation starts with
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    c.store = store
    yield c
    store.close()


def test_the_script_refuses_to_run_twice_unless_forced(seeded, capsys):
    assert load().main(["--db", seeded]) == 1 and "already has the history" in capsys.readouterr().out


def test_win_chance_and_the_win_model_have_enough_history(client):
    out = client.get("/api/win-model").json()
    assert out["decided"] == 80 and out["state"] == "ready" and out["test"]["reliable"] is True
    open_id = client.store.db.execute("SELECT id FROM bids WHERE key = 'demo:O0'").fetchone()[0]
    mine = client.get(f"/api/bids/{open_id}/win-chance").json()
    assert mine["applies"] is True and mine["score_pct"] is not None and mine["parts"]


def test_who_usually_wins_lists_the_rivals_for_a_bid(client):
    open_id = client.store.db.execute("SELECT id FROM bids WHERE key = 'demo:O0'").fetchone()[0]
    out = client.get(f"/api/bids/{open_id}/rivals").json()
    assert out["total"] >= 5 and out["rows"][0]["wins"] >= 2 and any(r["met"] for r in out["rows"]) and out["market"] > 0 and out["own"] > 0


def test_price_history_is_imported_and_backtests(client):
    summary = client.get("/api/market-results").json()
    assert summary["imported"] == 120 and summary["usable"] >= 190
    bt = client.get("/api/market-results/backtest").json()
    assert bt["tested"] >= 100 and bt["better_than_plain_pct"] > 0


def test_the_planner_has_open_bids_to_choose_from(client):
    out = client.get("/api/plan", params={"budget": 500000, "margin": 8}).json()
    assert out["candidates"] == 5 and 1 <= out["submit"] < 5 and out["emd_unknown"] in (0, 1) and out["cash_locked"] == 200000
    assert any(r["p_source"] == "your win model" for r in out["rows"])


def test_the_forecast_has_a_year_of_history_and_a_watch_list(client):
    out = client.get("/api/forecast").json()
    assert out["weeks_observed"] > 50 and out["total"]["expected"] > 0 and out["season_used"] is True
    assert {r["title"] for r in out["coming_round"]} >= {"Annual maintenance of rural roads, Nashik taluka"}


def test_portal_trouble_shows_on_the_health_report_and_the_coverage_report(client):
    health = {h["key"]: h["health"] for h in client.get("/api/portal-health").json()["items"]}
    assert health["maharashtra"] == "broken" and health["delhi"] == "dried_up" and health["up"] == "ok"
    cov = {r["key"]: r["verdict"] for r in client.get("/api/portals/coverage").json()["rows"]}
    assert cov["up"] == "proven" and cov["central"] == "failed"
