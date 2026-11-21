"""Who bid, the award, and who the competitors are."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .. import ai, winmodel
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
