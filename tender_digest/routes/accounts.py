"""People: accounts, passwords, activity, and the forgotten-password steps."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request

from .. import accounts
from ..accounts import AccountError, Principal
from ..api_common import (
    PasswordChange,
    ResetConfirm,
    ResetRequest,
    TwoStepCode,
    TwoStepOff,
)
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    client_of = ctx.client_of
    config = ctx.config
    manager = ctx.manager
    notify = ctx.notify
    now = ctx.now
    people = ctx.people
    public_base = ctx.public_base
    session_id = ctx.session_id
    store = ctx.store


    @app.post("/api/account/password", dependencies=auth)
    def change_password(body: PasswordChange, request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login is set by the server's APP_PASSWORD variable. Change it there")
        try:
            people.change_own_password(user.id, body.current, body.new)
        except AccountError as e:
            raise HTTPException(422, str(e)) from None
        store.sessions_end_for(user.id, except_sid=session_id(request))  # every other device is signed out
        return {"ok": True}

    @app.post("/api/account/2fa/start", dependencies=auth)
    def two_step_start(request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login's two-step code is set on the server with OWNER_TOTP_SECRET")
        secret = people.totp_start(user.id)
        return {"secret": secret, "uri": accounts.totp_uri(secret, user.username)}

    @app.post("/api/account/2fa/enable", dependencies=auth)
    def two_step_enable(body: TwoStepCode, request: Request) -> dict:
        try:
            people.totp_enable(request.state.user.id, body.code)
        except AccountError as e:
            raise HTTPException(422, str(e)) from None
        return {"ok": True}

    @app.post("/api/account/2fa/disable", dependencies=auth)
    def two_step_disable(body: TwoStepOff, request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner or people.authenticate(user.username, body.password) is None:
            raise HTTPException(422, "The password is not right")
        people.totp_off(user.id)
        return {"ok": True}

    @app.post("/api/account/sessions/end-others", dependencies=auth)
    def end_other_sessions(request: Request) -> dict:
        store.sessions_end_for(request.state.user.id, except_sid=session_id(request))
        return {"ok": True}

    @app.get("/api/account/sessions", dependencies=auth)
    def my_sessions(request: Request) -> dict:
        """The devices this person is signed in on."""
        mine = session_id(request)
        rows = store.sessions_of(request.state.user.id, time.time(), config.idle_seconds)
        return {"sessions": [{"ref": r["ref"], "device": r["agent"] or "Unknown device", "client": r["client"], "created": r["created"], "last_seen": r["last_seen"],
                              "current": r["sid"] == mine} for r in rows]}

    @app.delete("/api/account/sessions/{ref}", dependencies=auth)
    def end_session(ref: str, request: Request) -> dict:
        if not store.session_end_ref(request.state.user.id, ref):
            raise HTTPException(404, "That sign-in is already over")
        return {"ok": True}

    # ---- forgotten password: public, so each step is throttled and says the same thing whoever asks ---------------------

    reset_hits: dict[str, list[float]] = {}

    def reset_throttle(client: str, kind: str, limit: int) -> None:
        t = time.time()
        hits = [x for x in reset_hits.get(f"{kind}:{client}", []) if t - x < 900]
        if len(hits) >= limit:
            reset_hits[f"{kind}:{client}"] = hits
            raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")
        hits.append(t)
        reset_hits[f"{kind}:{client}"] = hits
        if len(reset_hits) > 5000:  # never grows without bound
            for k in [k for k, v in reset_hits.items() if not v or t - v[-1] > 900]:
                reset_hits.pop(k, None)

    @app.post("/api/password-reset/request")
    def password_reset_request(body: ResetRequest, request: Request) -> dict:
        reset_throttle(client_of(request), "ask", 6)
        if not manager.mail_configured:
            raise HTTPException(503, "Email is not set up on this server, so a reset link cannot be sent. Ask an administrator to set a new password for you.")
        found = people.reset_request(body.who, time.time())
        if found:
            user, token = found
            link = f"{public_base(request)}/#/reset/{token}"
            notify([user.email], "Reset your password",
                   f"Someone asked to reset the password for {user.username}.\n\nOpen this link to choose a new one (it works once and stops working in {people.RESET_MINUTES} minutes):\n{link}\n\n"
                   "If you did not ask for this, ignore this email: your password has not changed.")
            people.log(now(), user.username, user.role, "Asked for a password reset link", client=client_of(request))
        return {"ok": True, "message": "If that matches an account with an email address, a link to choose a new password is on its way."}

    @app.post("/api/password-reset/confirm")
    def password_reset_confirm(body: ResetConfirm, request: Request) -> dict:
        reset_throttle(client_of(request), "use", 10)
        try:
            user = people.reset_confirm(body.token, body.password, time.time())
        except AccountError as e:
            raise HTTPException(422, str(e)) from None
        store.sessions_end_for(user.id)  # whoever had the old password is signed out
        people.log(now(), user.username, user.role, "Chose a new password from an emailed link", client=client_of(request))
        return {"ok": True}
