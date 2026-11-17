"""Money: fees, earnest money (EMD), performance security and bank guarantees."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import FastAPI, HTTPException, Response

from ..bids import BidError
from ..digest import safe_cell
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bid_error = ctx.bid_error
    bid_or_404 = ctx.bid_or_404
    money = ctx.money
    now = ctx.now


    @app.get("/api/bids/{bid_id}/money", dependencies=auth)
    def get_bid_money(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return money.bid_view(bid_id, now())

    @app.post("/api/bids/{bid_id}/money", dependencies=auth)
    def add_bid_money(bid_id: int, body: dict[str, Any]) -> dict:
        bid_or_404(bid_id)
        try:
            money.add(bid_id, body, now())
        except BidError as e:
            raise bid_error(e) from None
        return money.bid_view(bid_id, now())

    @app.patch("/api/bids/{bid_id}/money/{money_id}", dependencies=auth)
    def patch_bid_money(bid_id: int, money_id: int, body: dict[str, Any]) -> dict:
        bid_or_404(bid_id)
        try:
            money.update(bid_id, money_id, body, now())
        except BidError as e:
            raise bid_error(e) from None
        return money.bid_view(bid_id, now())

    @app.delete("/api/bids/{bid_id}/money/{money_id}", dependencies=auth)
    def delete_bid_money(bid_id: int, money_id: int) -> dict:
        bid_or_404(bid_id)
        if not money.delete(bid_id, money_id):
            raise HTTPException(404, "No such payment")
        return money.bid_view(bid_id, now())

    @app.get("/api/money", dependencies=auth)
    def money_summary() -> dict:
        """Cash locked in bids, guarantee limit in use, refunds coming or overdue, and what needs doing."""
        return money.summary(now())

    @app.get("/api/money/export", dependencies=auth)
    def money_export() -> Response:
        out = io.StringIO()
        writer = csv.writer(out)
        for row in money.export_rows():
            writer.writerow([safe_cell(c) if isinstance(c, str) else c for c in row])
        return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="bid-money-{now().strftime("%Y-%m-%d")}.csv"'})
