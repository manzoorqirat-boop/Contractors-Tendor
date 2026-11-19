"""The web service's routes, one module per area.

Each module has register(app, ctx) and adds its routes with the code that used to sit in create_app, unchanged. register_all calls them in
the order below. Order matters where two paths could match the same address (a fixed path such as /api/bids/stats must be added before /api/bids/{bid_id});
the page's catch-all comes last and stays in create_app.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import accounts, admin, auth, bids, bidtools, boq, branding, calendar, firstrun, ignore, integrations, manual, money, portals, privacy, results, sso, team, tenders
from .context import Ctx

ORDER = (
    auth,
    branding,
    sso,
    tenders,
    ignore,
    manual,
    bids,
    boq,
    bidtools,
    team,
    results,
    money,
    accounts,
    firstrun,
    privacy,
    integrations,
    admin,
    portals,
    calendar,
)


def register_all(app: FastAPI, ctx: Ctx) -> None:
    for module in ORDER:
        module.register(app, ctx)
