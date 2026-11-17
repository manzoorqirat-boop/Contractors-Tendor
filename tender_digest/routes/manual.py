"""A tender the app could not collect: added by hand from its notice."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query

from .. import ai, alerts
from ..api_common import (
    MAX_VIEWS,
    VIEW_KEYS,
    AskBody,
    ListingBody,
    ManualBody,
    ManualEdit,
    StarBody,
    SummaryBody,
    ViewsBody,
    clean_ask,
)
from ..gepnic import Tender
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    ai_call = ctx.ai_call
    auth = ctx.auth
    bids = ctx.bids
    budget = ctx.budget
    config = ctx.config
    keep_source = ctx.keep_source
    now = ctx.now
    portal_names = ctx.portal_names
    profile = ctx.profile
    refresh_priorities = ctx.refresh_priorities
    run_job = ctx.run_job
    store = ctx.store
    tender_for_ai = ctx.tender_for_ai


    def when_or_422(v: str | None, label: str) -> datetime | None:
        v = (v or "").strip()
        if not v:
            return None
        try:
            d = datetime.fromisoformat(v)
        except ValueError:
            raise HTTPException(422, f"{label} should look like 2026-12-02 15:00") from None
        if not 2000 <= d.year <= 2100:
            raise HTTPException(422, f"{label} is not a believable date")
        if d.tzinfo is not None:  # "...Z" or "+00:00": convert to local time, do not just drop the offset
            d = d.astimezone(ZoneInfo(config.tz_name))
        return d.replace(tzinfo=None)

    def check_listing(published: datetime | None, closing: datetime | None, opening: datetime | None, value: float | None, emd: float | None) -> None:
        """Rules between the fields of a hand-added tender. Each message says what to change."""
        if published and closing and published > closing:
            raise HTTPException(422, "The publish date is after the closing date. Check both dates")
        if opening and closing and opening < closing:
            raise HTTPException(422, "Bids cannot open before the tender closes. Check the opening and closing dates")
        if value is not None and emd is not None and emd > value:
            raise HTTPException(422, "The EMD is larger than the tender value. Check both amounts")

    def check_tender_id(tid: str) -> None:
        if re.search(r"[<>\x00-\x1f\x7f]", tid):
            raise HTTPException(422, "The Tender ID cannot contain < or > or control characters. Copy it exactly as the portal shows it")

    def clean_money(v: float | None, label: str) -> float | None:
        if v is None:
            return None
        if not 0 < v < 1e13:
            raise HTTPException(422, f"{label} must be a positive amount in rupees")
        return float(v)

    @app.post("/api/tenders/manual/read", dependencies=auth)
    def manual_read(body: ListingBody):
        return run_job("manual-read", None, lambda: do_manual_read(body))

    def do_manual_read(body: ListingBody) -> dict:
        """Reads the listing details out of a notice, for the person to check and correct. Saves nothing."""
        return {"listing": ai_call(ai.read_listing, body.text, body.pdf_base64)}

    @app.get("/api/tenders/manual/check", dependencies=auth)
    def manual_check(tender_id: str = Query(..., max_length=80)) -> dict:
        """Is this Tender ID already held, from a portal or added earlier?"""
        found = store.find_by_tender_id(tender_id.strip())
        return {"existing": [{"key": f["key"], "portal_name": portal_names.get(f["portal"], f["portal"]), "organisation": f["organisation"], "manual": f["portal"] == "manual"} for f in found]}

    @app.post("/api/tenders/manual", dependencies=auth)
    def manual_create(body: ManualBody):
        return run_job("manual-create", body.tender_id.strip(), lambda: do_manual_create(body))

    def do_manual_create(body: ManualBody) -> dict:
        with manual_lock:  # the "already there?" check and the save are one step, so two people cannot both add the same Tender ID
            return _manual_create(body)

    manual_lock = threading.Lock()

    def _manual_create(body: ManualBody) -> dict:
        tid = body.tender_id.strip()[:80]
        if not tid:
            raise HTTPException(422, "The Tender ID is needed: it is how you find the tender on its portal")
        if not (body.title.strip() or body.description.strip()):
            raise HTTPException(422, "Give the work a name or description")
        closing = when_or_422(body.closing, "The closing date")
        if closing is None:
            raise HTTPException(422, "The closing date is needed, so deadlines and reminders work")
        check_tender_id(tid)
        if closing <= now():
            raise HTTPException(422, "The closing date has already passed. Bids can no longer be filed, so check the date")
        if store.find_by_tender_id(tid):
            raise HTTPException(409, "A tender with this ID is already in the app. Open it from the Tenders page instead of adding it again")
        desc = body.description.strip()[:500]
        published, opening = when_or_422(body.published, "The publish date"), when_or_422(body.opening, "The opening date")
        value, emd = clean_money(body.value, "The value"), clean_money(body.emd, "The EMD")
        check_listing(published, closing, opening, value, emd)
        t = Tender(portal="manual", tender_id=tid, title=(body.title.strip() or desc)[:160], reference=body.reference.strip()[:120], organisation=body.organisation.strip()[:300],
                   published=published, closing=closing, opening=opening,
                   description=desc or body.title.strip()[:500], category="", value=value, location=body.location.strip()[:120],
                   emd=emd, extra={"portal_name": body.portal_name.strip()[:80]})
        store.keep(t, now())
        store.mark_emailed([t.key])  # you added it yourself: it does not belong in "new since the last digest"
        keep_source(t.key, body.text, body.pdf_base64)
        out: dict[str, Any] = {"key": t.key, "doc_read": False, "doc_error": "", "source_kept": bids.has_source(t.key)}
        if body.read_document and (body.text.strip() or body.pdf_base64):
            if not ai.configured():
                out["doc_error"] = "The AI assistant is off, so the notice was saved but not read."
            else:
                try:
                    budget.spend()
                    doc = ai.read_document(tender_for_ai(t.key), body.text, body.pdf_base64)
                    store.set_ai_text(t.key, "doc", json.dumps(doc))
                    out["doc_read"] = True
                except ai.AIError as e:
                    out["doc_error"] = str(e)
        return out

    def manual_or_404(key: str) -> Tender:
        t = store.tender_object(key)
        if not t or t.portal != "manual":
            raise HTTPException(404, "That tender was not added by hand")
        return t

    @app.patch("/api/tenders/manual", dependencies=auth)
    def manual_edit(body: ManualEdit, key: str = Query(..., max_length=300)) -> dict:
        t = manual_or_404(key)
        old_closing = t.closing
        changed: dict[str, Any] = {}
        for f in ("title", "description", "reference", "organisation", "location"):
            v = getattr(body, f)
            if v is not None:
                setattr(t, f, v.strip()[:500 if f == "description" else 300])
                changed[f] = getattr(t, f)
        for f, label in (("published", "The publish date"), ("closing", "The closing date"), ("opening", "The opening date")):
            v = getattr(body, f)
            if v is not None:
                d = when_or_422(v, label)
                if f == "closing" and d is None:
                    raise HTTPException(422, "The closing date cannot be empty")
                setattr(t, f, d)
                changed[f] = d.isoformat(timespec="seconds") if d else None
        for f, label, clear in (("value", "The value", body.clear_value), ("emd", "The EMD", body.clear_emd)):
            if clear:
                setattr(t, f, None)
                changed[f] = None
            elif getattr(body, f) is not None:
                setattr(t, f, clean_money(getattr(body, f), label))
                changed[f] = getattr(t, f)
        if not (t.title or t.description):
            raise HTTPException(422, "Give the work a name or description")
        if t.closing != old_closing and t.closing and t.closing <= now():
            raise HTTPException(422, "The closing date has already passed. Bids can no longer be filed, so check the date")
        check_listing(t.published, t.closing, t.opening, t.value, t.emd)
        store.keep(t, now())
        bids.sync_listing(key, changed)
        return {"key": key}

    @app.delete("/api/tenders/manual", dependencies=auth)
    def manual_delete(key: str = Query(..., max_length=300)) -> dict:
        manual_or_404(key)
        if bids.id_for_key(key):
            raise HTTPException(409, "This tender has a bid. Remove the bid first")
        store.delete_tender(key)
        bids.delete_source(key)
        return {"removed": key}

    @app.post("/api/tenders/brief", dependencies=auth)
    def tender_brief(body: SummaryBody) -> dict:
        row = tender_for_ai(body.key)
        if row.get("brief") and not body.refresh:
            return {"brief": json.loads(row["brief"]), "cached": True}
        doc = json.loads(row["doc"]) if row.get("doc") else None
        hist = bids.history_notes(row.get("work_type"), row.get("organisation"), row.get("value"))
        out = ai_call(ai.brief, row, profile(), doc, *([hist] if hist else []))  # history is passed only when there is some
        store.set_ai_text(body.key, "brief", json.dumps(out))
        return {"brief": out, "cached": False}

    @app.post("/api/ask", dependencies=auth)
    def ask(body: AskBody) -> dict:
        question = body.question.strip()
        if not question:
            raise HTTPException(422, "Type a question first")
        fac = store.facets(now())
        raw = ai_call(ai.ask, question, [{"key": k, "name": n} for k, n in portal_names.items()],
                      {k: [f["value"] for f in fac.get(k, [])] for k in ("category", "department", "location")}, now().date().isoformat())
        return clean_ask(raw, set(portal_names))

    @app.get("/api/ai/status", dependencies=auth)
    def ai_status() -> dict:
        return {"configured": ai.configured(), "model": ai.model(), "fast_model": ai.fast_model(), "profile_set": bool(profile().strip()), **store.ai_counts(now()),
                "calls_today": budget.used_today(), "daily_limit": config.ai_per_day}

    @app.post("/api/ai/test", dependencies=auth)
    def ai_test() -> dict:
        ai_call(ai.ping)
        return {"ok": True, "model": ai.model(), "fast_model": ai.fast_model()}

    @app.post("/api/ai/score", dependencies=auth)
    def ai_score():
        return run_job("ai-score", "", lambda: do_ai_score())

    def do_ai_score() -> dict:
        if not ai.configured():
            raise HTTPException(503, "AI features are off. Set ANTHROPIC_API_KEY on the server to turn them on.")
        budget.spend(-(-45 // ai.BATCH))  # one request per batch of tenders
        saved = store.get_settings()
        res = ai.enrich(store, now(), profile(), bool(saved.get("ai_hide_noncivil", False)), 45)
        if res["error"] and not res["scored"]:
            raise HTTPException(502, res["error"])
        return {**res, **store.ai_counts(now())}

    @app.post("/api/tenders/star", dependencies=auth)
    def star(body: StarBody) -> dict:
        if not store.set_starred(body.key, body.starred, now()):
            raise HTTPException(404, "No such tender")
        refresh_priorities()
        return {"key": body.key, "starred": body.starred}

    @app.get("/api/views", dependencies=auth)
    def get_views() -> dict:
        return {"views": store.get_settings().get("views", [])}

    @app.put("/api/views", dependencies=auth)
    def put_views(body: ViewsBody) -> dict:
        views = []
        for v in body.views[:MAX_VIEWS]:
            name = v.name.strip()
            if not name or len(name) > 40 or len(v.id) > 40:
                raise HTTPException(422, "A view needs a name of up to 40 characters")
            if any(k not in VIEW_KEYS for k in v.filters):
                raise HTTPException(422, "A view contains a setting that is not known")
            views.append({"id": v.id, "name": name, "filters": v.filters, "alert": v.alert})
        if sum(1 for v in views if v["alert"]) > alerts.MAX_ALERT_VIEWS:
            raise HTTPException(422, f"At most {alerts.MAX_ALERT_VIEWS} saved searches can email you. Turn one off first")
        if len(json.dumps(views)) > 30_000:
            raise HTTPException(422, "Saved views are too large")
        store.put_settings({"views": views})
        return {"views": views}
