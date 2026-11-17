"""Signing in and out, who is signed in."""
from __future__ import annotations

import hmac
import secrets
import time

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import accounts
from ..api_common import (
    COOKIE,
    SESSION_SECONDS,
    LoginBody,
    make_token,
    storage_ok,
)
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    client_of = ctx.client_of
    config = ctx.config
    env = ctx.env
    jobs = ctx.jobs
    limiter = ctx.limiter
    now = ctx.now
    owner = ctx.owner
    people = ctx.people
    signing_key = ctx.signing_key
    store = ctx.store


    @app.get("/api/jobs/{job_id}", dependencies=auth)
    def job_state(job_id: str) -> dict:
        found = jobs.get(job_id)
        if not found:
            raise HTTPException(404, "That action is no longer being tracked: the server restarted, or it finished long ago. Check the page, and run it again if needed")
        return found

    @app.get("/api/health")
    def health():
        # Public, so it says only yes or no. Railway restarts the service when this fails.
        if not storage_ok(store, env.state_path):
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict:
        client = client_of(request)
        if limiter.blocked(client):
            raise HTTPException(429, "Too many wrong attempts. Wait a few minutes and try again.")
        if limiter.under_attack():
            time.sleep(limiter.slow_seconds)  # many wrong guesses from many addresses: slow everyone down, lock nobody out
        typed = body.username.strip().lower()
        owner_name_ok = not config.username or hmac.compare_digest(typed.encode(), config.username.lower().encode())
        owner_pass_ok = hmac.compare_digest(body.password.encode(), config.password.encode())
        who = owner if (owner_name_ok and owner_pass_ok) else people.authenticate(typed, body.password)
        if not who:
            limiter.failed(client)
            people.log(now(), typed[:80] or "?", None, "Failed sign-in", client=client)
            raise HTTPException(401, "Wrong username or password")
        if (who.owner and config.owner_totp) or (not who.owner and who.two_step):
            code = body.code.strip()
            if not code:  # the password was right: ask for the code. Nothing is signed in yet.
                return JSONResponse({"detail": "Enter the 6-digit code from your authenticator app.", "need_code": True}, status_code=401)  # type: ignore[return-value]  # FastAPI sends a Response object as it is
            if who.owner:
                step = accounts.totp_match(config.owner_totp, code, time.time(), int(store.get_settings().get("owner_totp_last", 0)))
                if step is not None:
                    store.put_settings({"owner_totp_last": step})
                good = step is not None
            else:
                good = people.totp_check(who.id, code)
            if not good:
                limiter.failed(client)
                people.log(now(), who.username, who.role, "Failed sign-in (wrong two-step code)", client=client)
                raise HTTPException(401, "That code is not right. Wait for a new one from the app and try again.")
        limiter.succeeded(client)
        sid = secrets.token_urlsafe(24)
        store.session_start(sid, time.time(), SESSION_SECONDS, who.id, client, accounts.describe_agent(request.headers.get("user-agent", "")))
        if not who.owner:
            people.record_login(who.id, now())
        people.log(now(), who.username, who.role, "Signed in", client=client)
        response.set_cookie(COOKIE, make_token(signing_key, sid=sid), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        return {"ok": True}
