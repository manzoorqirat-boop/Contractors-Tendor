"""Who bid, the award, and who the competitors are."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from .. import ai, planner, rivals, winmodel
from ..api_common import (
    AwardBody,
    BiddersBody,
)
from ..bids import BidError
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    ai_call = ctx.ai_call
    auth = ctx.auth
    bid_as_tender = ctx.bid_as_tender
    bid_error = ctx.bid_error
    bid_or_404 = ctx.bid_or_404
    bids = ctx.bids
    market = ctx.market
    money = ctx.money
    now = ctx.now
    run_job = ctx.run_job


    @app.post("/api/bids/{bid_id}/award", dependencies=auth)
    def bid_award(bid_id: int, body: AwardBody):
        return run_job("award", str(bid_id), lambda: do_bid_award(bid_id, body))

    def do_bid_award(bid_id: int, body: AwardBody) -> dict:
        """Reads an award notice or comparative statement into a list of bidders for the person to check. Saves nothing."""
        bid = bid_or_404(bid_id)
        return {"award": ai_call(ai.read_award, bid_as_tender(bid), body.text, body.pdf_base64)}

    @app.get("/api/bids/{bid_id}/bidders", dependencies=auth)
    def get_bidders(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return {"bidders": bids.bidders(bid_id)}

    @app.put("/api/bids/{bid_id}/bidders", dependencies=auth)
    def put_bidders(bid_id: int, body: BiddersBody) -> dict:
        bid_or_404(bid_id)
        try:
            patch = bids.set_bidders(bid_id, body.bidders, now())
        except BidError as e:
            raise bid_error(e) from None
        return {"bidders": bids.bidders(bid_id), "updated": patch}

    @app.get("/api/competitors", dependencies=auth)
    def competitors() -> dict:
        return {"competitors": bids.competitors()}

    @app.get("/api/win-model", dependencies=auth)
    def win_model() -> dict:
        """How many bids are decided, whether the win model has earned a score on your history, and how it did when tested on your past bids."""
        return winmodel.assess(bids.model_rows())

    @app.get("/api/bids/{bid_id}/win-chance", dependencies=auth)
    def bid_win_chance(bid_id: int) -> dict:
        """The chance of winning this bid, learned from your decided bids, shown only when there is enough history and the model beat the plain win rate in testing."""
        bid_or_404(bid_id)
        rows = bids.model_rows()
        me = next((r for r in rows if r["id"] == bid_id), None)
        if me is None:
            raise HTTPException(404, "No such bid")
        if me["stage"] in ("won", "lost", "dropped"):
            return {**winmodel.assess(rows), "applies": False}
        # this bid is not decided, so it is not in the training rows; its own features are what is asked about
        return {**winmodel.assess(rows, me), "applies": True}

    @app.get("/api/bids/{bid_id}/rivals", dependencies=auth)
    def bid_rivals(bid_id: int) -> dict:
        """Who usually wins tenders like this one, from the winners on record (your bidder lists and imported results that name the winner), and how you fared against each."""
        bid = bid_or_404(bid_id)
        return rivals.who_wins(market.winner_rows(), bids.company_key, bid.get("work_type"), (bid.get("organisation") or "").split("||")[0], bids.head_to_head())

    @app.get("/api/plan", dependencies=auth)
    def bid_plan(budget: float | None = Query(None, ge=0, le=1e12), margin: float = Query(8.0, ge=0, le=60)) -> dict:
        """Which open bids to submit with a limited amount of free cash for earnest money: the set with the most expected profit, with the reason for every bid left out."""
        predictor = winmodel.Predictor(bids.model_rows())
        today = now()
        cands = []
        for b in bids.list():
            if b["stage"] not in ("interested", "preparing"):
                continue
            if b.get("closing") and str(b["closing"])[:19] < today.isoformat(timespec="seconds"):
                continue
            p, source = predictor.chance({**b, "fit": None})
            cands.append({"id": b["id"], "title": (b.get("description") or b.get("title") or "")[:140], "tender_id": b.get("tender_id"), "value": b.get("value"), "emd": b.get("emd"),
                          "closing": b.get("closing"), "stage": b["stage"], "p": round(p, 3), "p_source": source})
        out = planner.plan(cands, budget, margin)
        out["cash_locked"] = money.summary(today)["totals"].get("cash_locked", 0)
        return out
