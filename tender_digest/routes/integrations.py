"""Integrations: API tokens and outgoing webhooks (administrators)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request

from .. import accounts, events, tokens
from ..accounts import AccountError, Principal
from ..api_common import (
    HookBody,
    NewUser,
    TokenBody,
    UserEdit,
)
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    api_tokens = ctx.api_tokens
    auth = ctx.auth
    config = ctx.config
    hooks = ctx.hooks
    now = ctx.now
    owner = ctx.owner
    people = ctx.people
    store = ctx.store


    @app.get("/api/tokens", dependencies=auth)
    def list_tokens() -> dict:
        return {"tokens": api_tokens.list(), "roles": list(tokens.ROLES)}

    @app.post("/api/tokens", dependencies=auth)
    def create_token(body: TokenBody, request: Request) -> dict:
        try:
            record, raw = api_tokens.create(body.name, body.role, body.days, request.state.user.username, now())
        except tokens.TokenError as e:
            raise HTTPException(422, str(e)) from None
        return {"token": record, "secret": raw}

    @app.delete("/api/tokens/{token_id}", dependencies=auth)
    def revoke_token(token_id: int) -> dict:
        if not api_tokens.revoke(token_id):
            raise HTTPException(404, "No such active token")
        return {"revoked": token_id}

    def hook_error(e: events.HookError) -> HTTPException:
        return HTTPException(404 if "No such" in str(e) else 422, str(e))

    @app.get("/api/webhooks", dependencies=auth)
    def list_webhooks() -> dict:
        return {"webhooks": hooks.list(), "events": [{"key": k, "label": v} for k, v in events.EVENTS.items() if k != "test"]}

    @app.post("/api/webhooks", dependencies=auth)
    def create_webhook(body: HookBody) -> dict:
        try:
            hook, secret = hooks.create(body.name or "", body.url or "", body.events or [], now())
        except events.HookError as e:
            raise hook_error(e) from None
        return {"webhook": hook, "secret": secret}

    @app.patch("/api/webhooks/{hook_id}", dependencies=auth)
    def edit_webhook(hook_id: int, body: HookBody) -> dict:
        try:
            return hooks.update(hook_id, name=body.name, url=body.url, events=body.events, active=body.active)
        except events.HookError as e:
            raise hook_error(e) from None

    @app.post("/api/webhooks/{hook_id}/secret", dependencies=auth)
    def new_webhook_secret(hook_id: int) -> dict:
        try:
            return {"secret": hooks.new_secret(hook_id)}
        except events.HookError as e:
            raise hook_error(e) from None

    @app.post("/api/webhooks/{hook_id}/test", dependencies=auth)
    def test_webhook_hook(hook_id: int) -> dict:
        """Sends a test message to this webhook right now and says how it went."""
        if not hooks.get(hook_id):
            raise HTTPException(404, "No such webhook")
        hooks.emit("test", {"message": "This is a test from TendAI."}, now(), only_hook=hook_id)
        hooks.deliver(now(), timeout=8)
        state = hooks.get(hook_id) or {}
        if state.get("last_status") != "ok":
            raise HTTPException(502, state.get("last_error") or "The test message could not be delivered")
        return {"ok": True, "webhook": state}

    @app.delete("/api/webhooks/{hook_id}", dependencies=auth)
    def delete_webhook(hook_id: int) -> dict:
        if not hooks.delete(hook_id):
            raise HTTPException(404, "No such webhook")
        return {"removed": hook_id}

    @app.get("/api/users", dependencies=auth)
    def list_users() -> dict:
        owner_row = {"id": accounts.OWNER_ID, "username": owner.username, "name": "Owner (server login)", "role": "admin", "active": True, "must_change": False,
                     "created": None, "last_login": None, "owner": True}
        return {"users": [owner_row, *({**u, "owner": False} for u in people.list())],
                "roles": [{"key": r, "label": r.capitalize(), "text": accounts.ROLE_TEXT[r]} for r in accounts.ROLES], "min_password": accounts.MIN_PASSWORD}

    @app.post("/api/users", dependencies=auth)
    def create_user(body: NewUser) -> dict:
        try:
            return people.create(body.username, body.name, body.role, body.password, now(), reserved=config.username, email=body.email)
        except AccountError as e:
            raise HTTPException(422, str(e)) from None

    @app.patch("/api/users/{user_id}", dependencies=auth)
    def edit_user(user_id: int, body: UserEdit, request: Request) -> dict:
        me: Principal = request.state.user
        if user_id == accounts.OWNER_ID:
            raise HTTPException(422, "The owner login is set by the server's variables and cannot be changed here")
        if user_id == me.id and (body.active is False or (body.role is not None and body.role != me.role)):
            raise HTTPException(422, "You cannot remove your own access or change your own role. Ask another administrator")
        try:
            out = people.update(user_id, name=body.name, role=body.role, active=body.active, password=body.password, reset_two_step=body.reset_2fa, email=body.email)
        except AccountError as e:
            raise HTTPException(404 if "No such" in str(e) else 422, str(e)) from None
        if body.active is False or body.password is not None:
            store.sessions_end_for(user_id)  # signed out everywhere, now
        return out

    @app.get("/api/audit", dependencies=auth)
    def activity_log(limit: int = Query(100, ge=1, le=500), user: str = Query("", max_length=80), before: int | None = Query(None, ge=1)) -> dict:
        rows = people.activity(limit, user, before)
        return {"entries": rows, "next": rows[-1]["id"] if len(rows) == limit else None}
