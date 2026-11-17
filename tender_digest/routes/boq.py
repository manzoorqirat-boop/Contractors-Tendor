"""BOQ: catalogue, upload and match, costing, risk."""
from __future__ import annotations

import base64
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response

from .. import ai, appsettings, sealing
from ..api_common import (
    BoqConfirm,
    BoqCost,
    BoqUpload,
    CatalogueBody,
    RiskBody,
    SimBody,
    VaultBody,
)
from ..bids import VAULT_KINDS, BidError
from ..boq import advisor
from ..boq.service import BoqError
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bid_as_tender = ctx.bid_as_tender
    bid_error = ctx.bid_error
    bid_or_404 = ctx.bid_or_404
    bids = ctx.bids
    boq = ctx.boq
    boq_call = ctx.boq_call
    company_facts = ctx.company_facts
    keep_source = ctx.keep_source
    now = ctx.now
    portal_names = ctx.portal_names
    profile = ctx.profile
    run_job = ctx.run_job
    store = ctx.store


    @app.get("/api/boq/catalogue", dependencies=auth)
    def boq_catalogue() -> dict:
        return boq.catalogue()

    @app.post("/api/boq/catalogue", dependencies=auth)
    def boq_catalogue_import(body: CatalogueBody) -> dict:
        return boq_call(boq.import_catalogue, body.kind, body.csv_text)

    @app.get("/api/bids/{bid_id}/boq", dependencies=auth)
    def bid_boq(bid_id: int, include_review: bool = False) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.view, bid["key"], include_review)

    @app.post("/api/bids/{bid_id}/boq", dependencies=auth)
    def bid_boq_upload(bid_id: int, body: BoqUpload):
        return run_job("boq-upload", str(bid_id), lambda: do_bid_boq_upload(bid_id, body))

    def do_bid_boq_upload(bid_id: int, body: BoqUpload) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.analyse, bid["key"], body.filename, body.file_base64, pdf_reader=ai.read_boq_pdf if ai.configured() else None, uses_ai=True)

    @app.post("/api/bids/{bid_id}/boq/lines/{line_no}", dependencies=auth)
    def bid_boq_confirm(bid_id: int, line_no: int, body: BoqConfirm, request: Request) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.confirm, bid["key"], line_no, body.item_code, request.state.user.username)

    @app.post("/api/bids/{bid_id}/boq/lines/{line_no}/cost", dependencies=auth)
    def bid_boq_cost(bid_id: int, line_no: int, body: BoqCost) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.set_cost, bid["key"], line_no, body.cost)

    @app.get("/api/bids/{bid_id}/risks", dependencies=auth)
    def bid_risks(bid_id: int) -> dict:
        bid = bid_or_404(bid_id)
        return boq.risks(bid["key"])

    @app.post("/api/bids/{bid_id}/risks", dependencies=auth)
    def bid_risks_read(bid_id: int, body: RiskBody):
        return run_job("risks-read", str(bid_id), lambda: do_bid_risks_read(bid_id, body))

    def do_bid_risks_read(bid_id: int, body: RiskBody) -> dict:
        bid = bid_or_404(bid_id)
        text, pdf = body.text, body.pdf_base64
        if not text.strip() and not pdf:
            text, pdf = bids.load_source(bid["key"])  # the notice kept when the tender was added or its document read
            if not text and not pdf:
                raise HTTPException(422, "No notice is saved for this tender yet. Upload it or paste its text.")
        out = boq_call(boq.read_risks, bid["key"], bid_as_tender(bid), text, pdf, uses_ai=True)
        if body.text.strip() or body.pdf_base64:
            keep_source(bid["key"], body.text, body.pdf_base64)
        return out

    @app.get("/api/bids/{bid_id}/advice", dependencies=auth)
    def bid_advice(bid_id: int, min_margin: float = Query(3.0, ge=0, lt=50), target_margin: float = Query(8.0, gt=0, lt=50), include_review: bool = False) -> dict:
        """Quote advice: your cost range (with risk allowances when they have been read) against how past L1 prices compared with their estimates."""
        bid = bid_or_404(bid_id)
        mode, notes = "with_risks", []
        try:
            sim = boq.simulate(bid["key"], None, None, None, None, include_review)
            low, high, est, coverage = sim["cost_low"], sim["cost_high"], sim.get("estimate"), sim["coverage_pct"]
        except BoqError as e:
            if "risks" not in str(e).lower():
                raise HTTPException(422, str(e)) from None
            pricing = boq.price(bid["key"], include_review, "")
            if pricing is None:
                raise HTTPException(422, "Upload the BOQ first: the advisor starts from your costed BOQ.") from None
            mode, low, high, est, coverage = "boq_only", pricing["projected_cost"], pricing["projected_cost"], pricing["estimate_total"], pricing["coverage_pct"]
            notes.append("Risk allowances are not included yet. Read the tender document for risks to add them to your cost.")
        if not est:
            raise HTTPException(422, "The BOQ has no estimate to compare your quote with.")
        if coverage < 50:
            notes.append(f"Only {coverage:.0f}% of the BOQ value is priced from your catalogue; the rest is carried at the buyer's estimate.")
        outcomes, basis = bids.l1_outcomes(bid.get("work_type"), bid.get("value"))
        try:
            out = advisor.advise(low, high, est, outcomes, min_margin, target_margin, basis)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        out.update({"mode": mode, "coverage_pct": coverage, "my_value": bid.get("my_value")})
        out["notes"] = notes + out["notes"]
        return out

    @app.post("/api/bids/{bid_id}/risks/simulate", dependencies=auth)
    def bid_risks_simulate(bid_id: int, body: SimBody) -> dict:
        bid = bid_or_404(bid_id)
        return boq_call(boq.simulate, bid["key"], body.estimate, body.exposure, body.l1_price, body.quoted_pct, body.include_review)

    @app.get("/api/vault", dependencies=auth)
    def vault_list() -> dict:
        today = now()
        docs = [{**d, "status": bids.doc_status(d, today)} for d in bids.vault_list()]
        return {"documents": docs, "kinds": [{"key": k, "label": v[0]} for k, v in VAULT_KINDS.items()], "facts": company_facts()}

    @app.post("/api/vault", dependencies=auth)
    def vault_add(body: VaultBody) -> dict:
        data = None
        if body.file_base64:
            try:
                data = base64.b64decode(body.file_base64, validate=True)
            except ValueError:
                raise HTTPException(422, "That file could not be read") from None
        try:
            return bids.vault_add(body.model_dump(), data, body.filename, now())
        except BidError as e:
            raise bid_error(e) from None

    @app.patch("/api/vault/{doc_id}", dependencies=auth)
    def vault_update(doc_id: int, body: dict[str, Any]) -> dict:
        try:
            return bids.vault_update(doc_id, body)
        except BidError as e:
            raise HTTPException(404 if "No such" in str(e) else 422, str(e)) from None

    @app.delete("/api/vault/{doc_id}", dependencies=auth)
    def vault_delete(doc_id: int) -> dict:
        if not bids.vault_delete(doc_id):
            raise HTTPException(404, "No such document")
        return {"removed": doc_id}

    @app.get("/api/vault/{doc_id}/file", dependencies=auth)
    def vault_file(doc_id: int):
        try:
            found = bids.vault_bytes(doc_id)
        except sealing.SealError as e:
            raise HTTPException(500, str(e)) from None
        if not found:
            raise HTTPException(404, "No file is stored for that document")
        content, name = found
        return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="' + re.sub(r'[^\w.\- ()]', "_", name) + '"'})

    @app.get("/api/company-profile", dependencies=auth)
    def get_company_profile() -> dict:
        """The company description the AI uses for fit scores and drafts. Its own route, so bidders can keep it without access to the rest of Settings."""
        return {"company_profile": profile()}

    @app.put("/api/company-profile", dependencies=auth)
    def put_company_profile(body: dict[str, Any]) -> dict:
        try:
            cleaned = appsettings.clean({"company_profile": body.get("company_profile")}, list(portal_names))
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        store.put_settings({"company_profile": cleaned["company_profile"]})
        return {"company_profile": profile()}

    @app.put("/api/company-facts", dependencies=auth)
    def put_company_facts(body: dict[str, Any]) -> dict:
        raw = body.get("facts")
        if not isinstance(raw, list) or len(raw) > 40:
            raise HTTPException(422, "Facts must be a list of up to 40 items")
        facts = []
        for f in raw:
            label, value = str((f or {}).get("label", "")).strip()[:60], str((f or {}).get("value", "")).strip()[:160]
            if label and value:
                facts.append({"label": label, "value": value})
        store.put_settings({"company_facts": facts})
        return {"facts": facts}
