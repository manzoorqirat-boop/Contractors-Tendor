"""Tenders you are not interested in, the size of work you take, and clean-up."""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Query, Request

from .. import ai, appsettings, learn
from ..accounts import Principal
from ..api_common import (
    CleanupBody,
    DocBody,
    HideBody,
    MuteBody,
    SizeBody,
    SummaryBody,
)
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    ai_call = ctx.ai_call
    auth = ctx.auth
    bids = ctx.bids
    env = ctx.env
    keep_source = ctx.keep_source
    now = ctx.now
    refresh_priorities = ctx.refresh_priorities
    run_job = ctx.run_job
    shape = ctx.shape
    size_limits = ctx.size_limits
    store = ctx.store
    tender_for_ai = ctx.tender_for_ai


    @app.post("/api/tenders/hide", dependencies=auth)
    def hide_tenders(body: HideBody, request: Request) -> dict:
        """Marks tenders as not interesting (or brings them back). With mute_department, everything from their departments is hidden too."""
        user: Principal = request.state.user
        if not body.hidden:
            changed = store.unhide(body.keys)
            refresh_priorities()
            return {"changed": changed, "hidden": False, "muted": []}
        muted = []
        if body.mute_department:
            for d in dict.fromkeys(store.department_of(k) for k in body.keys):
                if d and store.mute_department(d, now(), user.username):
                    muted.append(d)
        changed = store.hide(body.keys, now(), user.username)
        refresh_priorities()
        return {"changed": changed, "hidden": True, "muted": muted}

    @app.get("/api/mutes", dependencies=auth)
    def list_mutes() -> dict:
        return {"departments": store.muted_departments(), "hidden_tenders": store.search(now(), {"hidden": "only", "status": "all"}, "closing", 1, 0)[1]}

    @app.post("/api/mutes", dependencies=auth)
    def set_mute(body: MuteBody, request: Request) -> dict:
        ok = store.mute_department(body.department, now(), request.state.user.username) if body.muted else store.unmute_department(body.department)
        refresh_priorities()
        return {"changed": ok, "departments": store.muted_departments()}

    @app.get("/api/company-size", dependencies=auth)
    def get_company_size() -> dict:
        lim = size_limits()
        return {"min": lim.get("size_min"), "max": lim.get("size_max")}

    @app.put("/api/company-size", dependencies=auth)
    def put_company_size(body: SizeBody) -> dict:
        lo, hi = body.min or None, body.max or None
        if lo and hi and lo > hi:
            raise HTTPException(422, "The smallest size cannot be bigger than the largest")
        store.put_settings({"size_min": lo, "size_max": hi})
        refresh_priorities()
        return {"min": lo, "max": hi}

    @app.get("/api/learning", dependencies=auth)
    def learning_status() -> dict:
        """What the recommended order has learned from the company's own choices, so it can be checked and trusted (or switched off in Settings)."""
        model = store.learning_model()
        top = model.top()
        return {"enabled": appsettings.current(env, store.get_settings())["learning"], "ready": model.ready, "needed": learn.MIN_SIGNALS,
                "positive": model.signals["positive"], "negative": model.signals["negative"], **top}

    @app.get("/api/cleanup/preview", dependencies=auth)
    def cleanup_preview(days: int = Query(30, ge=7, le=3650)) -> dict:
        return store.cleanup_preview(now(), days)

    @app.post("/api/cleanup", dependencies=auth)
    def cleanup_now(body: CleanupBody) -> dict:
        """Removes closed tenders older than `days` that nobody starred and no bid uses, and the placeholders for tenders judged not civil."""
        return store.cleanup(now(), body.days)

    @app.get("/api/tenders/detail", dependencies=auth)
    def tender_detail(key: str = Query(..., max_length=300)) -> dict:
        row = store.get_tender(key)
        if not row:
            raise HTTPException(404, "No such tender")
        for field in ("brief", "doc"):
            row[field] = json.loads(row[field]) if row.get(field) else None
        row["related"] = store.related(key)
        row["bid_id"] = bids.id_for_key(key)
        row["manual"] = row["portal"] == "manual"
        row["has_source"] = bids.has_source(key)
        row["history"] = bids.history_notes(row.get("work_type"), row.get("organisation"), row.get("value"))
        return shape(row)

    @app.post("/api/tenders/summary", dependencies=auth)
    def tender_summary(body: SummaryBody) -> dict:
        row = tender_for_ai(body.key)
        if row.get("summary") and not body.refresh:
            return {"summary": row["summary"], "cached": True}
        text = ai_call(ai.summarise, row)
        store.set_ai_text(body.key, "summary", text)
        return {"summary": text, "cached": False}

    @app.post("/api/tenders/document", dependencies=auth)
    def tender_document(body: DocBody):
        return run_job("tender-document", body.key, lambda: do_tender_document(body))

    def do_tender_document(body: DocBody) -> dict:
        row = tender_for_ai(body.key)
        doc = ai_call(ai.read_document, row, body.text, body.pdf_base64)
        store.set_ai_text(body.key, "doc", json.dumps(doc))
        keep_source(body.key, body.text, body.pdf_base64)
        return {"doc": doc}
