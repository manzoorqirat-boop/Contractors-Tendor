"""The bid workspace: pipeline, checklist, eligibility, drafts, changes, results."""
from __future__ import annotations

import base64
import json
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from .. import ai, appsettings
from ..accounts import Principal
from ..api_common import (
    BidCreate,
    BulkBids,
    DocBody,
    DocChecked,
    DraftBody,
    ItemBody,
    SummaryBody,
    log,
)
from ..bids import STAGE_LABELS, STAGES, VAULT_KINDS, BidError, clean_patch, locate_on_portal, portal_facts
from ..boq.portal import fetch_boq, fetch_risks
from ..digest import format_inr
from ..gepnic import PortalError, parse_date, pick_nit
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    ai_call = ctx.ai_call
    auth = ctx.auth
    bid_as_tender = ctx.bid_as_tender
    bid_error = ctx.bid_error
    bid_full = ctx.bid_full
    bid_link = ctx.bid_link
    bid_or_404 = ctx.bid_or_404
    bids = ctx.bids
    boq = ctx.boq
    boq_call = ctx.boq_call
    budget = ctx.budget
    company_facts = ctx.company_facts
    env = ctx.env
    keep_source = ctx.keep_source
    manager = ctx.manager
    notify = ctx.notify
    now = ctx.now
    people = ctx.people
    portal_names = ctx.portal_names
    portals_now = ctx.portals_now
    profile = ctx.profile
    refresh_priorities = ctx.refresh_priorities
    run_job = ctx.run_job
    store = ctx.store


    @app.get("/api/bids", dependencies=auth)
    def list_bids(stage: str | None = None) -> dict:
        if stage and stage not in STAGES:
            raise HTTPException(422, "Unknown stage")
        rows = bids.list(stage)
        for r in rows:
            r["portal_name"] = portal_names.get(r["portal"] or "", r["portal"])
            r["value_text"] = format_inr(r["value"])
        counts = {s: 0 for s in STAGES}
        for r in bids.list():
            counts[r["stage"]] += 1
        return {"bids": rows, "counts": counts, "stages": [{"key": k, "label": STAGE_LABELS[k]} for k in STAGES], "unseen": bids.unseen_total()}

    @app.post("/api/bids/bulk", dependencies=auth)
    def create_bids_bulk(body: BulkBids) -> dict:
        """Starts a bid on every ticked tender that has none yet."""
        created, existing, missing = [], [], []
        for key in dict.fromkeys(body.keys):
            try:
                bid, made = bids.create_from_tender(key, now())
            except BidError:
                missing.append(key)
                continue
            if made:
                bids.generate_items(bid["id"], now())
                created.append(bid["id"])
            else:
                existing.append(bid["id"])
        if created:
            refresh_priorities()
        return {"created": created, "existing": existing, "missing": missing}

    @app.post("/api/bids", dependencies=auth)
    def create_bid(body: BidCreate) -> dict:
        try:
            bid, created = bids.create_from_tender(body.key, now())
        except BidError as e:
            raise HTTPException(404, str(e)) from None
        if created:
            bids.generate_items(bid["id"], now())
            refresh_priorities()
        return {"id": bid["id"], "created": created}

    @app.get("/api/bids/alerts", dependencies=auth)
    def bid_alerts(days: int = Query(7, ge=1, le=60)) -> dict:
        return {"alerts": bids.alerts(now(), days), "unseen": bids.unseen_total()}

    @app.get("/api/bids/stats", dependencies=auth)
    def bid_stats() -> dict:
        return bids.stats()

    @app.get("/api/bids/{bid_id}", dependencies=auth)
    def get_bid(bid_id: int) -> dict:
        return bid_full(bid_or_404(bid_id))

    @app.patch("/api/bids/{bid_id}", dependencies=auth)
    def patch_bid(bid_id: int, body: dict[str, Any], request: Request) -> dict:
        current = bid_or_404(bid_id)
        user: Principal = request.state.user
        new_stage = body.get("stage")
        if (appsettings.current(env, store.get_settings())["require_signoff"] and current["stage"] == "interested" and new_stage in ("preparing", "submitted")
                and not user.can("admin") and (current.get("signoff") or {}).get("state") != "approved"):
            raise HTTPException(422, "This bid needs an administrator's approval before it moves on. Use 'Ask for approval' on the Team tab.")
        try:
            bids.update(bid_id, clean_patch(body), now())
        except BidError as e:
            raise bid_error(e) from None
        if "stage" in body:
            refresh_priorities()
        return bid_full(bid_or_404(bid_id))

    @app.delete("/api/bids/{bid_id}", dependencies=auth)
    def delete_bid(bid_id: int) -> dict:
        if not bids.delete(bid_id):
            raise HTTPException(404, "No such bid")
        return {"removed": bid_id}

    @app.post("/api/bids/{bid_id}/items", dependencies=auth)
    def add_bid_item(bid_id: int, body: ItemBody) -> dict:
        bid_or_404(bid_id)
        try:
            return bids.add_item(bid_id, body.text, body.kind, now())
        except BidError as e:
            raise bid_error(e) from None

    @app.post("/api/bids/{bid_id}/items/generate", dependencies=auth)
    def generate_bid_items(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return {"added": bids.generate_items(bid_id, now())}

    @app.patch("/api/bids/{bid_id}/items/{item_id}", dependencies=auth)
    def patch_bid_item(bid_id: int, item_id: int, body: dict[str, Any], request: Request) -> dict:
        bid = bid_or_404(bid_id)
        before = bids.get_item(bid_id, item_id)
        try:
            item = bids.update_item(bid_id, item_id, body)
        except BidError as e:
            raise bid_error(e) from None
        who = (item.get("owner") or "").strip().lower()
        if "owner" in body and who and before and (before.get("owner") or "").strip().lower() != who and who != request.state.user.username.lower():
            notify(people.emails_for([who]), f"{request.state.user.name} gave you a task on a bid",
                   f"{request.state.user.name} assigned you: {item['text']}\n\nBid: {(bid['description'] or bid['title'])[:120]} (ID {bid['tender_id']})\nCloses: {bid.get('closing') or 'not set'}{bid_link(bid_id)}")
        return item

    @app.delete("/api/bids/{bid_id}/items/{item_id}", dependencies=auth)
    def delete_bid_item(bid_id: int, item_id: int) -> dict:
        if not bids.delete_item(bid_id, item_id):
            raise HTTPException(404, "No such item")
        return {"removed": item_id}

    @app.post("/api/bids/{bid_id}/changes/seen", dependencies=auth)
    def bid_changes_seen(bid_id: int) -> dict:
        bid_or_404(bid_id)
        bids.mark_seen(bid_id)
        return {"ok": True}

    @app.post("/api/bids/{bid_id}/document", dependencies=auth)
    def bid_document(bid_id: int, body: DocBody):
        return run_job("bid-document", str(bid_id), lambda: do_bid_document(bid_id, body))

    def do_bid_document(bid_id: int, body: DocBody) -> dict:
        bid = bid_or_404(bid_id)
        doc = ai_call(ai.read_document, bid_as_tender(bid), body.text, body.pdf_base64)
        keep_source(bid["key"], body.text, body.pdf_base64)
        bids.set_json(bid_id, "doc", doc, now())
        if store.get_tender(bid["key"]):
            store.set_ai_text(bid["key"], "doc", json.dumps(doc))
        return {"doc": doc, "added": bids.generate_items(bid_id, now())}

    fetch_lock = threading.Lock()

    @app.post("/api/bids/{bid_id}/fetch", dependencies=auth)
    def bid_fetch(bid_id: int):
        return run_job("bid-fetch", str(bid_id), lambda: do_bid_fetch(bid_id))

    def do_bid_fetch(bid_id: int) -> dict:
        """Looks the tender up again on its portal: the public fee/class/date fields, and the tender notice (NIT) when the portal gives it without a login."""
        bid = bid_or_404(bid_id)
        if bid["portal"] == "manual":
            raise HTTPException(422, "This tender was added by hand, so there is nothing to fetch from a portal. Upload its notice instead.")
        portal = next((p for p in portals_now() if p.key == bid["portal"]), None)
        if not portal:
            raise HTTPException(404, "That portal is no longer in your list")
        if not fetch_lock.acquire(blocking=False):
            raise HTTPException(409, "Another fetch is in progress. Try again in a moment")
        try:
            client = manager.client_factory(portal, env)
            try:
                t = locate_on_portal(client, portal.key, bid["organisation"] or "", bid["tender_id"])
                if t is None:
                    raise HTTPException(404, "The tender is no longer listed on the portal. It may have closed or been withdrawn")
                full = getattr(client, "detail_full", None)
                pairs, corrigenda, links = full(t) if full else (client.detail(t), [], [])
            except PortalError as e:
                raise HTTPException(502, f"The portal could not be read: {e}") from None
            facts = portal_facts(pairs)
            bids.set_json(bid_id, "portal_facts", facts, now())
            patch = {}
            for label, field in (("Clarification End Date", "query_deadline"), ("Pre Bid Meeting Date", "prebid_date")):
                d = parse_date(pairs.get(label, ""))
                if d and not bid.get(field):
                    patch[field] = d.isoformat(timespec="seconds")
            if patch:
                bids.update(bid_id, patch, now())
            state, message = "no_link", "The portal lists no downloadable notice for this tender."
            nit_pdf: bytes | None = None
            nit = pick_nit(links)
            if nit and hasattr(client, "download"):
                try:
                    data, _ctype = client.download(nit["href"])
                except PortalError as e:
                    state, message = "failed", f"The portal would not give the notice: {e}"
                else:
                    nit_pdf = data if data.startswith(b"%PDF") else None
                    if nit_pdf:
                        try:
                            bids.save_source(bid["key"], nit_pdf)   # so the risk step can read it again without an upload
                        except OSError:
                            log.warning("could not keep the notice for %s", bid["key"])
                    if not data.startswith(b"%PDF"):
                        state, message = "login", "The portal asks for a login or captcha before it gives documents, which this app never gets past. Download the notice yourself and upload it here."
                    elif not ai.configured():
                        state, message = "ai_off", "The notice was available, but the AI assistant is off, so it was not read."
                    else:
                        try:
                            budget.spend()
                            doc = ai.read_document(bid_as_tender(bid), "", base64.b64encode(data).decode())
                            bids.set_json(bid_id, "doc", doc, now())
                            if store.get_tender(bid["key"]):
                                store.set_ai_text(bid["key"], "doc", json.dumps(doc))
                            state, message = "read", "Read the tender notice from the portal."
                        except ai.AIError as e:
                            state, message = "ai_failed", str(e)
            added = bids.generate_items(bid_id, now())
            boq_out = boq_call(fetch_boq, client, boq, bid["key"], links, nit["href"] if nit else None, uses_ai=True)
            risks_out = boq_call(fetch_risks, boq, bid["key"], bid_as_tender(bid), nit_pdf, uses_ai=True)
            return {"facts": facts, "added": added, "nit": state, "message": message, "corrigenda": corrigenda, "boq": boq_out, "risks": risks_out}
        finally:
            fetch_lock.release()

    @app.post("/api/bids/{bid_id}/doc-checked", dependencies=auth)
    def bid_doc_checked(bid_id: int, body: DocChecked, request: Request) -> dict:
        """Records that a person compared what the AI read from the tender notice with the notice itself (or takes that back)."""
        bid = bid_or_404(bid_id)
        if not bid.get("doc"):
            raise HTTPException(422, "No tender document has been read for this bid yet")
        mark = {"by": request.state.user.username, "at": now().isoformat(timespec="seconds")} if body.checked else None
        bids.set_json(bid_id, "doc_checked", mark, now())
        # set_json("doc_checked") must not clear itself: only a new "doc" does that
        return {"doc_checked": mark}

    @app.post("/api/bids/{bid_id}/brief", dependencies=auth)
    def bid_brief(bid_id: int, body: SummaryBody) -> dict:
        bid = bid_or_404(bid_id)
        if bid.get("brief") and not body.refresh:
            return {"brief": bid["brief"], "cached": True}
        hist = bids.history_notes(bid.get("work_type"), bid.get("organisation"), bid.get("value"))
        out = ai_call(ai.brief, bid_as_tender(bid), profile(), bid.get("doc"), *([hist] if hist else []))
        bids.set_json(bid_id, "brief", out, now())
        return {"brief": out, "cached": False}

    @app.post("/api/bids/{bid_id}/eligibility", dependencies=auth)
    def bid_eligibility(bid_id: int) -> dict:
        bid = bid_or_404(bid_id)
        if not ai.configured():
            raise HTTPException(503, "AI features are off. Set ANTHROPIC_API_KEY on the server to turn them on.")
        doc = bid.get("doc") or {}
        if not (doc.get("requirements") or doc.get("financial")):
            raise HTTPException(422, "The tender document has no eligibility conditions yet. Read the tender document first.")
        vault = [{**d, "kind": VAULT_KINDS.get(d["kind"], ("Other",))[0]} for d in bids.vault_list()]
        out = ai_call(ai.eligibility, bid_as_tender(bid), profile(), company_facts(), vault, bid.get("doc") or {})
        out["checked"] = now().isoformat(timespec="seconds")
        bids.set_json(bid_id, "eligibility", out, now())
        return out

    @app.post("/api/bids/{bid_id}/draft", dependencies=auth)
    def bid_draft(bid_id: int, body: DraftBody) -> dict:
        bid = bid_or_404(bid_id)
        if body.kind not in ai.DRAFT_KINDS:
            raise HTTPException(422, "Unknown kind of draft")
        if body.kind == "custom" and not body.instructions.strip():
            raise HTTPException(422, "Say what to write")
        drafts = bid.get("drafts") or {}
        if drafts.get(body.kind) and not body.refresh and body.kind != "custom":
            return {"kind": body.kind, "text": drafts[body.kind], "cached": True}
        text = ai_call(ai.draft, body.kind, bid_as_tender(bid), profile(), company_facts(), bid.get("doc"), body.instructions)
        drafts[body.kind] = text
        bids.set_json(bid_id, "drafts", drafts, now())
        return {"kind": body.kind, "text": text, "cached": False}

    # ---- vault: reusable company papers and the facts about your company ---------------------------------------------
