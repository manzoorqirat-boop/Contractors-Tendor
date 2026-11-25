"""Tender data: the list, details, filters, statistics, analytics, saved views, export."""
from __future__ import annotations

import csv
import io

from fastapi import Depends, FastAPI, HTTPException, Query, Response

from .. import ai, appsettings, smartsearch
from ..api_common import (
    MAX_EXPORT_ROWS,
    BulkStar,
    real_date,
)
from ..digest import CSV_COLUMNS, safe_cell, tenders_workbook
from ..store import DIMENSIONS, SORTS
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    config = ctx.config
    env = ctx.env
    manager = ctx.manager
    now = ctx.now
    portal_names = ctx.portal_names
    portal_search = ctx.portal_search
    portal_states = ctx.portal_states
    portals_now = ctx.portals_now
    refresh_priorities = ctx.refresh_priorities
    shape = ctx.shape
    size_limits = ctx.size_limits
    storage_note = ctx.storage_note
    store = ctx.store


    def filter_params(
        portal: list[str] | None = Query(None), category: list[str] | None = Query(None), department: list[str] | None = Query(None),
        location: list[str] | None = Query(None), work_type: list[str] | None = Query(None), min_fit: int | None = Query(None, ge=1, le=5), q: str | None = Query(None, max_length=100), status: str = "open",
        min_value: float | None = Query(None, ge=0), max_value: float | None = Query(None, ge=0), value_lt: float | None = Query(None, ge=0),
        no_value: bool = False, no_closing: bool = False, no_published: bool = False, starred: bool = False,
        closing_from: str | None = None, closing_to: str | None = None, seen_from: str | None = None, seen_to: str | None = None,
        published_from: str | None = None, published_to: str | None = None,
        closing_within: int | None = Query(None, ge=1, le=365), since_days: int | None = Query(None, ge=1, le=365),
        hidden: str = "exclude", fits_me: bool = False,
    ) -> dict:
        if status not in ("open", "closing_soon", "closed", "all"):
            raise HTTPException(422, "Unknown status")
        if hidden not in ("exclude", "only"):
            raise HTTPException(422, "Unknown value for hidden")
        dates = dict(closing_from=closing_from, closing_to=closing_to, seen_from=seen_from, seen_to=seen_to, published_from=published_from, published_to=published_to)
        for name, value in dates.items():
            if value and not real_date(value):
                raise HTTPException(422, f"{name} must be a real date, like 2026-11-20")
        lists = {"portal": portal, "category": category, "department": department, "location": location, "work_type": work_type}
        for name, values in lists.items():
            if values and (len(values) > 50 or any(len(v) > 200 for v in values)):
                raise HTTPException(422, f"Too many or too long values for {name}")
        return {
            **{k: v for k, v in lists.items() if v}, "q": q, "status": status, "min_value": min_value, "max_value": max_value, "value_lt": value_lt,
            "no_value": no_value, "no_closing": no_closing, "no_published": no_published, "starred": starred, "closing_within": closing_within, "since_days": since_days, "min_fit": min_fit,
            "soon_days": env.closing_soon_days, **{k: v for k, v in dates.items() if v}, "hidden": hidden, **(size_limits() if fits_me else {}),
        }

    fparams = Depends(filter_params)

    @app.get("/api/config", dependencies=auth)
    def get_config() -> dict:
        cur = appsettings.current(env, store.get_settings())
        return {
            "portals": [{"key": p.key, "name": p.name, "state": p.state, "search_url": portal_search[p.key]} for p in portals_now()],
            "mail_configured": manager.mail_configured, "recipient_count": len(cur["recipients"]),
            "now": now().isoformat(timespec="seconds"), "timezone": config.tz_name,
            "dimensions": [{"key": k, "label": d["label"], "time": isinstance(d["order"], str) and d["order"] in ("first", "last")} for k, d in DIMENSIONS.items()],
            "soon_days": env.closing_soon_days, "ai_enabled": ai.configured(), "storage_warning": storage_note, "require_signoff": cur["require_signoff"],
        }

    @app.get("/api/stats", dependencies=auth)
    def stats(f: dict = fparams) -> dict:
        data = store.stats(now(), env.closing_soon_days, f)
        for row in data["by_portal"]:
            row["name"] = portal_names.get(row["portal"], row["portal"])
            row["short"] = portal_states.get(row["portal"], row["portal"])
        for row in data["departments"]:
            row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        for row in data["biggest"]:
            row["portal_name"] = portal_names.get(row["portal"], row["portal"])
        last = store.list_runs(1)
        data["last_run"] = last[0] if last else None
        data["soon_days"] = env.closing_soon_days
        return data

    @app.get("/api/analytics", dependencies=auth)
    def analytics(group_by: str, split_by: str | None = None, top: int = Query(12, ge=3, le=40), f: dict = fparams) -> dict:
        if group_by not in DIMENSIONS or (split_by and split_by not in DIMENSIONS):
            raise HTTPException(422, "Unknown dimension")
        if split_by == group_by:
            split_by = None
        result = store.pivot(now(), f, group_by, split_by, top)
        result["labels"] = {k: portal_states.get(k, k) for k in portal_names} if "portal" in (group_by, split_by) else {}
        return result

    @app.get("/api/facets", dependencies=auth)
    def facets() -> dict:
        data = store.facets(now())
        for row in data["portal"]:
            row["label"] = portal_names.get(row["value"], row["value"])
        return data

    @app.get("/api/tenders", dependencies=auth)
    def tenders(f: dict = fparams, sort: str = "smart", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
        if sort not in SORTS and sort != "relevance":
            raise HTTPException(422, "Unknown sort")
        rows, total = store.search(now(), f, sort, page_size, (page - 1) * page_size)
        out = {"items": [shape(r) for r in rows], "total": total, "page": page, "page_size": page_size, "categories": store.categories()}
        if total == 0:
            out["kept"] = store.kept_count()          # 0: nothing has been collected yet; more than 0: there are tenders, just none that match (or all have closed)
        if f.get("q"):
            parsed = smartsearch.parse(str(f["q"]))
            out["understood"] = parsed.describe() if (parsed.has_text or parsed.min_value is not None or parsed.max_value is not None) else ""
            if total == 0 and parsed.has_text:
                out["suggest"] = smartsearch.suggest(str(f["q"]), store.vocabulary())
        return out

    @app.get("/api/tenders/export", dependencies=auth)
    def export(f: dict = fparams, sort: str = "smart", format: str = Query("csv", pattern="^(csv|xlsx)$"), keys: list[str] | None = Query(None, max_length=300)) -> Response:
        """The tenders the filters select (or only the `keys` a person ticked) as a CSV or an Excel workbook."""
        if sort not in SORTS:
            raise HTTPException(422, "Unknown sort")
        if keys:
            if len(keys) > 300 or any(len(k) > 300 for k in keys):
                raise HTTPException(422, "Too many tenders selected")
            f = {**f, "keys": keys, "status": "all"}
        rows, _ = store.search(now(), f, sort, MAX_EXPORT_ROWS, 0)
        table = [[
            "Open" if not r["closing"] or r["closing"] >= now().isoformat() else "Closed", portal_names.get(r["portal"], r["portal"]), r["tender_id"],
            r["description"] or r["title"], (r["organisation"] or "").replace("||", " > "), r["category"], r["location"],
            "" if r["value"] is None else int(r["value"]), "" if r["emd"] is None else int(r["emd"]), r["published"] or "", r["closing"] or "",
        ] for r in rows]
        stamp = now().strftime("%Y-%m-%d")
        if format == "xlsx":
            return Response(tenders_workbook(table), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="tenders-{stamp}.xlsx"'})
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for r in table:
            writer.writerow([safe_cell(c) for c in r])
        return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="tenders-{stamp}.csv"'})

    @app.post("/api/tenders/star-bulk", dependencies=auth)
    def star_bulk(body: BulkStar) -> dict:
        """Stars or unstars every ticked tender at once."""
        done = sum(1 for k in dict.fromkeys(body.keys) if store.set_starred(k, body.starred, now()))
        refresh_priorities()
        return {"changed": done, "starred": body.starred}
