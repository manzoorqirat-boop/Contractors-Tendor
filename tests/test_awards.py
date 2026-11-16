"""Award notices and competitors: who bid, who won, and how rivals quote."""
import json

import pytest

from tender_digest import ai
from tender_digest.bids import BidBook
from tests.test_bids import NOW, app_ctx, kept  # noqa: F401


def open_bid(app_ctx, n=1, value=2e7):
    t = kept(app_ctx.store, n)
    return app_ctx.client.post("/api/bids", json={"key": t.key}).json()["id"], t


def bidders(*rows):
    return [{"name": n, "price": p, "rank": r, "status": s, "mine": m} for n, p, r, s, m in rows]


# ---- reading a notice ------------------------------------------------------------------------------------------------------

def test_an_award_notice_is_read_into_a_list_for_checking_and_nothing_is_saved(app_ctx, monkeypatch):
    bid, _ = open_bid(app_ctx)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    reply = {"winner": "M/s Rao Infra Pvt. Ltd.", "winning_price": 18500000, "awarded": "2026-12-20", "estimate": 20000000, "notes": "Work awarded after negotiation.",
             "bidders": [{"name": "M/s Rao Infra Pvt. Ltd.", "price": 18500000, "rank": 1, "status": "qualified"}, {"name": "Shah Constructions", "price": "19100000", "rank": 2, "status": "qualified"},
                         {"name": "Late & Co", "price": -5, "rank": 0, "status": "rejected"}, {"name": "", "price": 1}, "junk", {"name": "Patil Builders", "price": None, "rank": None, "status": "x" * 90}]}
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(reply))
    got = app_ctx.client.post(f"/api/bids/{bid}/award", json={"text": "AWARD NOTICE ..."}).json()["award"]
    assert got["winner"] == "M/s Rao Infra Pvt. Ltd." and got["winning_price"] == 18500000 and got["awarded"] == "2026-12-20"
    names = [b["name"] for b in got["bidders"]]
    assert names == ["M/s Rao Infra Pvt. Ltd.", "Shah Constructions", "Late & Co", "Patil Builders"]      # blanks and junk dropped
    by = {b["name"]: b for b in got["bidders"]}
    assert by["Shah Constructions"]["price"] is None                                                       # a price given as text is not trusted
    assert by["Late & Co"]["price"] is None and by["Late & Co"]["rank"] is None                            # negative price and rank 0 refused
    assert len(by["Patil Builders"]["status"]) == 40
    assert app_ctx.client.get(f"/api/bids/{bid}/bidders").json()["bidders"] == []                          # nothing was saved


def test_the_winner_is_taken_from_rank_one_when_not_stated(monkeypatch):
    got = ai.clean_award({"bidders": [{"name": "B", "rank": 2}, {"name": "A", "rank": 1}], "awarded": "not a date"})
    assert got["winner"] == "A" and got["awarded"] == ""
    with pytest.raises(ai.AIError):
        ai.clean_award("a list, not an object")


def test_award_reading_needs_the_ai_and_a_notice(app_ctx, monkeypatch):
    bid, _ = open_bid(app_ctx)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert app_ctx.client.post(f"/api/bids/{bid}/award", json={"text": "x"}).status_code == 503
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert app_ctx.client.post(f"/api/bids/{bid}/award", json={}).status_code == 502                       # nothing to read


# ---- saving who bid ---------------------------------------------------------------------------------------------------------------

def test_saving_bidders_fills_in_l1_and_your_price_once(app_ctx):
    bid, _ = open_bid(app_ctx)
    c = app_ctx.client
    rows = bidders(("Rao Infra", 18500000, 1, "qualified", False), ("Our company", 19200000, 2, "qualified", True), ("Cheap Co", 15000000, None, "Rejected: late", False))
    r = c.put(f"/api/bids/{bid}/bidders", json={"bidders": rows}).json()
    assert [b["name"] for b in r["bidders"]] == ["Rao Infra", "Our company", "Cheap Co"]                     # ranked first, unranked last
    assert r["updated"] == {"l1_value": 18500000.0, "l1_bidder": "Rao Infra", "my_value": 19200000.0}      # the rejected cheaper quote is not L1
    detail = c.get(f"/api/bids/{bid}").json()
    assert detail["l1_value"] == 18500000 and detail["my_value"] == 19200000
    c.patch(f"/api/bids/{bid}", json={"l1_value": 18000000})
    again = c.put(f"/api/bids/{bid}/bidders", json={"bidders": rows}).json()
    assert again["updated"] == {} and c.get(f"/api/bids/{bid}").json()["l1_value"] == 18000000              # a figure you typed is never overwritten


@pytest.mark.parametrize("bad,why", [
    ([{"name": "A", "price": "lots"}], "not an amount"), ([{"name": "A", "price": -1}], "not an amount"), ([{"name": "A", "rank": 0}], "whole number"),
    ([{"name": "A", "rank": 1.5}], "whole number"), ([{"name": "A", "mine": True}, {"name": "B", "mine": True}], "Only one row"),
])
def test_bad_bidder_lists_are_refused_with_a_reason(app_ctx, bad, why):
    bid, _ = open_bid(app_ctx)
    r = app_ctx.client.put(f"/api/bids/{bid}/bidders", json={"bidders": bad})
    assert r.status_code == 422 and why in r.json()["detail"]


def test_at_most_thirty_bidders_and_blank_rows_are_ignored(app_ctx):
    bid, _ = open_bid(app_ctx)
    c = app_ctx.client
    assert c.put(f"/api/bids/{bid}/bidders", json={"bidders": [{"name": f"C{i}"} for i in range(31)]}).status_code == 422
    r = c.put(f"/api/bids/{bid}/bidders", json={"bidders": [{"name": "  "}, {"name": "Real Co", "price": ""}]}).json()
    assert [b["name"] for b in r["bidders"]] == ["Real Co"] and r["bidders"][0]["price"] is None


# ---- competitors ------------------------------------------------------------------------------------------------------------------

def test_company_names_match_across_spellings():
    k = BidBook.company_key
    assert k("M/s Rao Infra Pvt. Ltd.") == k("RAO INFRA PRIVATE LIMITED") == k("rao infra") == "rao infra"
    assert k("Shah & Sons Co.") == k("SHAH AND SONS") and k("") == "" and k("Pvt Ltd") == ""


def test_competitors_summarise_how_rivals_quote_across_bids(app_ctx):
    c = app_ctx.client
    b1, _ = open_bid(app_ctx, 1)
    b2, _ = open_bid(app_ctx, 2)
    b3, _ = open_bid(app_ctx, 3)
    # the fixture tenders are worth 2 crore: Rao quotes 10% under on #1, and 5% under on #2 and loses; Shah quotes on #1 only
    c.put(f"/api/bids/{b1}/bidders", json={"bidders": bidders(("M/s Rao Infra Pvt. Ltd.", 18000000, 1, "qualified", False), ("Shah Constructions", 19000000, 2, "qualified", False), ("Us", 19500000, 3, "qualified", True))})
    c.put(f"/api/bids/{b2}/bidders", json={"bidders": bidders(("RAO INFRA PRIVATE LIMITED", 19000000, 2, "qualified", False), ("Someone Else", 18000000, 1, "qualified", False))})
    c.put(f"/api/bids/{b3}/bidders", json={"bidders": []})
    got = c.get("/api/competitors").json()["competitors"]
    rao = next(x for x in got if x["key"] == "rao infra")
    assert rao["bids"] == 2 and rao["wins"] == 1 and rao["quotes"] == 2 and rao["avg_vs_estimate"] == -7.5       # (-10 and -5) / 2
    assert rao["departments"] and rao["last_seen"]
    assert got[0]["key"] == "rao infra"                                                                        # the most frequent rival first
    assert not any("us" == x["key"] for x in got)                                                              # your own company is not a competitor
    assert {x["key"] for x in got} == {"rao infra", "shah constructions", "someone else"}


def test_view_only_accounts_read_competitors_but_cannot_record_bidders(app_ctx):
    from fastapi.testclient import TestClient
    from tender_digest import web
    from tests.test_web import PASSWORD, settings
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), settings(), app_ctx.store)
    owner = TestClient(app)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    owner.post("/api/users", json={"username": "vera", "role": "viewer", "password": "temporary-pass-1"})
    v = TestClient(app)
    v.post("/api/login", json={"username": "vera", "password": "temporary-pass-1"})
    v.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
    bid = owner.post("/api/bids", json={"key": app_ctx.key}).json()["id"]
    assert v.get("/api/competitors").status_code == 200 and v.get(f"/api/bids/{bid}/bidders").status_code == 200
    assert v.put(f"/api/bids/{bid}/bidders", json={"bidders": []}).status_code == 403
    assert v.post(f"/api/bids/{bid}/award", json={"text": "x"}).status_code == 403


def test_deleting_a_bid_removes_its_bidders(app_ctx):
    bid, _ = open_bid(app_ctx)
    app_ctx.client.put(f"/api/bids/{bid}/bidders", json={"bidders": bidders(("A Co", 1e7, 1, "", False))})
    app_ctx.client.delete(f"/api/bids/{bid}")
    assert app_ctx.store.db.execute("SELECT COUNT(*) FROM bid_bidders").fetchone()[0] == 0
