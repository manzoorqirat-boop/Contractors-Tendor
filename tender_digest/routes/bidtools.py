"""The bid pack, letter templates, comparing tenders, hand-recorded corrigenda."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import ai, clauses, letters, pack, pdftext
from ..api_common import (
    Corrigendum,
    LetterFill,
    SourceBody,
    TemplateBody,
    TemplateRestore,
)
from ..bids import BidError
from ..bids import _when as stored_date
from ..digest import format_inr
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bid_as_tender = ctx.bid_as_tender
    bid_error = ctx.bid_error
    bid_full = ctx.bid_full
    bid_or_404 = ctx.bid_or_404
    ai_call = ctx.ai_call
    bids = ctx.bids
    company_facts = ctx.company_facts
    letter_book = ctx.letter_book
    now = ctx.now
    portal_names = ctx.portal_names
    run_job = ctx.run_job
    shape = ctx.shape
    store = ctx.store


    @app.get("/api/bids/{bid_id}/pack", dependencies=auth)
    def bid_pack(bid_id: int):
        """A zip of the papers this bid needs, in checklist order, with an index of what is in it and what is missing."""
        bid = bid_or_404(bid_id)
        path, summary = pack.build(bid, bids.with_matches(bid, now()), bids, now())
        stem = pack.safe_name(bid.get("tender_id") or f"bid-{bid_id}", 40)
        return FileResponse(path, media_type="application/zip", filename=f"bid-pack-{stem}.zip", background=BackgroundTask(path.unlink, missing_ok=True),
                            headers={"X-Pack-Files": str(summary["files"]), "X-Pack-Attention": str(summary["needs_attention"])})

    def template_error(e: letters.LetterError) -> HTTPException:
        return HTTPException(422, str(e))

    @app.get("/api/letter-templates", dependencies=auth)
    def letter_templates() -> dict:
        facts = [{"key": f"company_{letters.slug(f['label'])}", "label": f["label"]} for f in company_facts() if letters.slug(f.get("label", ""))]
        fields = [{"key": k, "label": v} for k, v in letters.BID_FIELDS.items()]
        return {"templates": letter_book.list(), "fields": fields + facts}

    @app.post("/api/letter-templates", dependencies=auth)
    def letter_template_create(body: TemplateBody, request: Request) -> dict:
        try:
            return letter_book.create(body.name, body.body, request.state.user.username, now(), body.note)
        except letters.LetterError as e:
            raise template_error(e) from None

    @app.put("/api/letter-templates/{template_id}", dependencies=auth)
    def letter_template_save(template_id: int, body: TemplateBody, request: Request) -> dict:
        try:
            return letter_book.save(template_id, body.body, request.state.user.username, now(), body.name, body.note)
        except letters.LetterError as e:
            raise (HTTPException(404, str(e)) if str(e) == "No such template" else template_error(e)) from None

    @app.get("/api/letter-templates/{template_id}/versions", dependencies=auth)
    def letter_template_versions(template_id: int) -> dict:
        if not letter_book.get(template_id):
            raise HTTPException(404, "No such template")
        return {"versions": letter_book.versions(template_id)}

    @app.post("/api/letter-templates/{template_id}/restore", dependencies=auth)
    def letter_template_restore(template_id: int, body: TemplateRestore, request: Request) -> dict:
        if not letter_book.get(template_id):
            raise HTTPException(404, "No such template")
        try:
            return letter_book.restore(template_id, body.version, request.state.user.username, now())
        except letters.LetterError as e:
            raise template_error(e) from None

    @app.delete("/api/letter-templates/{template_id}", dependencies=auth)
    def letter_template_delete(template_id: int) -> dict:
        if not letter_book.delete(template_id):
            raise HTTPException(404, "No such template")
        return {"removed": template_id}

    @app.post("/api/bids/{bid_id}/letter", dependencies=auth)
    def bid_letter(bid_id: int, body: LetterFill) -> dict:
        """A template filled in from this bid and the company facts. Placeholders with no value stay in the text and are listed."""
        bid = bid_or_404(bid_id)
        tpl = letter_book.get(body.template_id)
        if not tpl:
            raise HTTPException(404, "No such template")
        today = now()

        def day(v: str | None) -> str:
            d = stored_date(v, end_of_day=False) if v else None
            return d.strftime("%d %b %Y" + (", %I:%M %p" if v and len(v) > 10 else "")).replace(" 0", " ") if d else ""

        values = {"tender_id": bid.get("tender_id") or "", "title": bid.get("description") or bid.get("title") or "", "organisation": bid.get("organisation") or "",
                  "portal": portal_names.get(bid.get("portal") or "", bid.get("portal") or ""), "location": bid.get("location") or "", "closing": day(bid.get("closing")),
                  "opening": day(bid.get("opening")), "prebid": day(bid.get("prebid_date")), "emd": format_inr(bid.get("emd")) if bid.get("emd") else "",
                  "value": format_inr(bid.get("value")) if bid.get("value") else "", "today": today.strftime("%d %b %Y").lstrip("0")}
        for f in company_facts():
            values[f"company_{letters.slug(f.get('label', ''))}"] = str(f.get("value", ""))
        text, missing = letters.render(tpl["body"], values)
        return {"text": text, "missing": missing, "template": tpl["name"], "version": tpl["version"]}

    @app.get("/api/tenders/compare", dependencies=auth)
    def tenders_compare(keys: list[str] = Query(..., max_length=4)) -> dict:
        """Two to four tenders side by side, with the derived figures a bidder weighs (EMD as a share of value, days left)."""
        keys = list(dict.fromkeys(keys))
        if not 2 <= len(keys) <= 4:
            raise HTTPException(422, "Choose two to four tenders to compare")
        out = []
        for k in keys:
            row = store.get_tender(k)
            if not row:
                raise HTTPException(404, "One of those tenders is no longer in the list")
            row = shape(row)
            closing = stored_date(row.get("closing"), end_of_day=False) if row.get("closing") else None
            value, emd = row.get("value"), row.get("emd")
            bid_id = bids.id_for_key(k)
            out.append({**{f: row.get(f) for f in ("key", "tender_id", "title", "description", "organisation", "portal_name", "category", "location", "work_type", "value", "value_text",
                                                   "emd", "published", "closing", "opening", "fit", "fit_reason", "starred", "manual")},
                        "emd_pct": round(emd / value * 100, 2) if value and emd else None,
                        "days_left": round((closing - now()).total_seconds() / 86400, 1) if closing else None, "bid_id": bid_id})
        return {"tenders": out}

    @app.post("/api/bids/{bid_id}/corrigenda", dependencies=auth)
    def bid_corrigendum(bid_id: int, body: Corrigendum, request: Request) -> dict:
        """Records a corrigendum (and the closing date it set) read on the portal by hand."""
        bid_or_404(bid_id)
        try:
            bids.record_corrigendum(bid_id, body.note, body.closing, now(), request.state.user.username)
        except BidError as e:
            raise bid_error(e) from None
        return bid_full(bid_or_404(bid_id))

    def source_text(body: SourceBody) -> str:
        text = body.text if body.text.strip() else pdftext.extract(body.pdf_base64)
        if not text.strip() and not body.pdf_base64:
            raise HTTPException(422, "Paste the text or choose a PDF first")
        return text

    @app.post("/api/bids/{bid_id}/clauses", dependencies=auth)
    def bid_clauses(bid_id: int, body: SourceBody) -> dict:
        """The conditions that decide a tender (earnest money, completion period, liquidated damages and so on), found by fixed rules, each with the sentence it came from.
        No AI and no API key. They are saved with the bid, so a later corrigendum can be compared with them."""
        bid_or_404(bid_id)
        text = source_text(body)
        found = clauses.find(text)
        bids.set_json(bid_id, "clauses", {"clauses": found, "source_checked": len(text.strip()) >= 200, "at": now().isoformat(timespec="seconds")}, now())   # kept apart from the AI reading, so it never clears a person's "checked" mark on that
        note = "" if text.strip() else "This PDF has no text layer (it is a scan), so nothing could be read from it here. Paste the text, or use the AI reading, which can read scans."
        return {"clauses": found, "chars": len(text), "note": note}

    @app.post("/api/bids/{bid_id}/corrigenda/read", dependencies=auth)
    def bid_corrigendum_read(bid_id: int, body: SourceBody):
        """Reads a corrigendum: what it changes. The rules compare its clauses with the tender's (no AI needed); with an API key the AI also lists every change with its impact
        and a suggested action. Nothing is recorded: the page offers to record it."""
        return run_job("bid-corrigendum", str(bid_id), lambda: do_corrigendum_read(bid_id, body))

    def do_corrigendum_read(bid_id: int, body: SourceBody) -> dict:
        bid = bid_or_404(bid_id)
        text = source_text(body)
        doc = bid.get("doc") or {}
        found = clauses.find(text)
        known = (bid.get("clauses") or {}).get("clauses") or doc.get("clauses") or []
        out: dict = {"rules": clauses.changes(known, found), "clauses": found, "ai": None, "ai_error": "", "has_text": bool(text.strip()), "had_document": bool(known)}
        if ai.configured():
            try:
                out["ai"] = ai_call(ai.read_corrigendum, bid_as_tender(bid), doc, body.text, body.pdf_base64)
            except HTTPException as e:
                out["ai_error"] = str(e.detail)
        return out
