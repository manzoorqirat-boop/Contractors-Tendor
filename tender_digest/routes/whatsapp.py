"""The WhatsApp assistant's webhook (called by Meta, so it has no login: every call is checked by signature) and its status page for administrators."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import ai, whatsapp
from ..accounts import ROLES
from ..api_common import clean_ask
from .context import Ctx

log = logging.getLogger("tender_digest.whatsapp")


class TryBody(BaseModel):
    text: str = Field("", max_length=500)
    role: str = "viewer"


def register(app: FastAPI, ctx: Ctx) -> None:
    auth = ctx.auth
    ai_call = ctx.ai_call
    bids = ctx.bids
    config = ctx.config
    money = ctx.money
    now = ctx.now
    portal_names = ctx.portal_names
    store = ctx.store
    cfg = whatsapp.Config.from_env()
    guard = whatsapp.Guard()

    def ai_filters(question: str) -> dict[str, Any]:
        if not ai.configured():
            return {}
        fac = store.facets(now())
        raw = ai_call(ai.ask, question, [{"key": k, "name": n} for k, n in portal_names.items()], {k: [f["value"] for f in fac.get(k, [])] for k in ("category", "department", "location")}, now().date().isoformat())
        return clean_ask(raw, set(portal_names))

    assistant = whatsapp.Assistant(store, bids, money, now, ai_filters if ai.configured() else None)

    def answer(sender: str, text: str) -> None:
        role = cfg.allowed[sender]
        try:
            reply = assistant.reply(text, role)
        except Exception:
            log.exception("assistant failed")
            reply = "Something went wrong on my side. Try again in a moment."
        whatsapp.send_text(cfg, sender, reply)

    @app.get("/api/whatsapp/webhook")
    def whatsapp_verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"), challenge: str = Query("", alias="hub.challenge")):
        """Meta's one-time check when the webhook is registered: it sends our verify token and expects its challenge back."""
        import hmac
        if not cfg.verify_token or mode != "subscribe" or not hmac.compare_digest(token.encode(), cfg.verify_token.encode()):
            raise HTTPException(403, "Verification failed")
        return PlainTextResponse(re.sub(r"[^\w-]", "", challenge)[:100])

    @app.post("/api/whatsapp/webhook")
    async def whatsapp_receive(request: Request, background: BackgroundTasks) -> dict:
        """Messages from Meta. Accepted only when signed with the app secret; answered only for listed numbers; always 200 so Meta does not keep resending."""
        if not cfg.enabled:
            raise HTTPException(503, "The WhatsApp assistant is not set up on this server")
        raw = await request.body()
        if not whatsapp.verify_signature(cfg.app_secret, raw, request.headers.get("x-hub-signature-256")):
            raise HTTPException(403, "Bad signature")
        try:
            payload = json.loads(raw)
        except ValueError:
            return {"ok": True}
        handled = 0
        for m in whatsapp.parse_incoming(payload, cfg.phone_number_id):
            if m.sender not in cfg.allowed:
                continue                                   # not on the list: no reply, no trace of who we are
            if not guard.allow(m.id, m.sender):
                continue
            background.add_task(answer, m.sender, m.text)
            handled += 1
        return {"ok": True, "handled": handled}

    @app.get("/api/whatsapp/status", dependencies=auth)
    def whatsapp_status() -> dict:
        """For the Settings page: is it set up, what is missing, and the address to give Meta. Numbers are shown only as their last four digits."""
        base = config.app_url or ""
        return {"enabled": cfg.enabled, "missing": cfg.missing(), "webhook_url": f"{base}/api/whatsapp/webhook" if base else "/api/whatsapp/webhook", "app_url_set": bool(base),
                "numbers": [{"ending": n[-4:], "role": r} for n, r in cfg.allowed.items()], "version": cfg.version}

    @app.post("/api/whatsapp/try", dependencies=auth)
    def whatsapp_try(body: TryBody) -> dict:
        """A preview for administrators: the same answers the WhatsApp assistant gives, as if the sender had this role, with nothing sent to Meta. Works without any WhatsApp set-up."""
        role = body.role if body.role in ROLES else "viewer"
        try:
            reply = assistant.reply(body.text, role)
        except Exception:
            log.exception("assistant preview failed")
            reply = "Something went wrong on my side. Try again in a moment."
        return {"reply": reply, "role": role}
