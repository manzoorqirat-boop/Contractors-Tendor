"""Price history: award results that teach the price advisor how the lowest bidder prices (see pricing.py and market.py)."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import market as market_module
from .. import pricing
from ..api_common import FieldHTTPException
from .context import Ctx


class ImportBody(BaseModel):
    text: str


class ResultBody(BaseModel):
    department: str = ""
    work_type: str = ""
    location: str = ""
    estimate: float
    price: float
    bidders: int | None = None
    awarded: str | None = None
    tender_id: str = ""


def register(app: FastAPI, ctx: Ctx) -> None:
    auth = ctx.auth
    market = ctx.market
    now = ctx.now

    @app.get("/api/market-results", dependencies=auth)
    def market_list(limit: int = 50, offset: int = 0) -> dict:
        return {**market.summary(), "rows": market.recent(max(1, min(limit, 200)), max(0, offset))}

    @app.post("/api/market-results/import", dependencies=auth)
    def market_import(body: ImportBody) -> dict:
        """Adds award results from a CSV or a table pasted from a spreadsheet. Rows that cannot be used are listed with the reason; the rest are kept."""
        good, bad, info = market_module.parse_table(body.text)
        if not good and bad and bad[0]["line"] <= 1:
            raise HTTPException(422, bad[0]["reason"])
        counts = market.add_rows(good, "import", now())
        hint = ""
        if not good and bad and all("missing or not a number" in b["reason"] for b in bad):
            hint = ("Every row failed on its amounts. If the amounts contain commas (5,00,000), each one must be inside quotes (\"5,00,000\"), as a spreadsheet's "
                    "Save as CSV does it; or paste the rows copied straight from the spreadsheet, which are separated by tabs.")
        return {**counts, "rejected": bad[:50], "rejected_total": len(bad), "columns": info.get("columns", {}), "hint": hint}

    @app.post("/api/market-results", dependencies=auth)
    def market_add(body: ResultBody) -> dict:
        if pricing.row_from(body.estimate, body.price) is None:
            raise HTTPException(422, "Give the buyer's estimate and the awarded price in rupees. A price under a third of the estimate or more than twice it is refused as a typing error")
        awarded = market_module.parse_date(body.awarded) if body.awarded else None
        if body.awarded and not awarded:
            raise HTTPException(422, "The date could not be read. Use day-month-year, for example 14-11-2026")
        ref = market_module._ref(body.tender_id.strip()[:80], body.department, body.estimate, body.price, awarded)
        for text, limit, what, field in ((body.department, 300, "The department", "department"), (body.work_type, 80, "The work type", "work_type"), (body.location, 80, "The location", "location")):
            if len(text.strip()) > limit:
                raise FieldHTTPException(422, f"{what} can be at most {limit} characters, and this is {len(text.strip())}. Shorten it and try again", field)
        row = {"department": body.department.strip(), "work_type": body.work_type.strip(), "location": body.location.strip(), "estimate": body.estimate,
               "price": body.price, "bidders": body.bidders if body.bidders and 0 < body.bidders < 1000 else None, "awarded": awarded, "ref": ref}
        counts = market.add_rows([row], "manual", now())
        return counts

    @app.delete("/api/market-results/{result_id}", dependencies=auth)
    def market_delete(result_id: int) -> dict:
        if not market.delete(result_id):
            raise HTTPException(404, "No such result")
        return {"ok": True}

    @app.delete("/api/market-results", dependencies=auth)
    def market_clear(source: str | None = None) -> dict:
        """Removes every imported result (source=import), every typed-in one (source=manual) or both. Your own decided bids are never touched."""
        if source not in (None, "import", "manual"):
            raise HTTPException(422, "source must be import or manual")
        return {"removed": market.clear(source)}

    @app.get("/api/market-results/backtest", dependencies=auth)
    def market_backtest() -> dict[str, Any]:
        """Checks the price model against history: each dated result is predicted from the earlier ones only. See pricing.backtest."""
        return pricing.backtest(market.model_rows(), now())
