"""Single sign-on (OpenID Connect): the provider signs people in, this app starts their session."""
from __future__ import annotations

import hmac
import secrets
import time
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .. import accounts, appsettings, sso
from ..accounts import AccountError
from ..api_common import (
    COOKIE,
    SESSION_SECONDS,
    log,
    make_token,
    token_claims,
)
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    client_of = ctx.client_of
    config = ctx.config
    current_user = ctx.current_user
    env = ctx.env
    limiter = ctx.limiter
    now = ctx.now
    people = ctx.people
    public_base = ctx.public_base
    session_id = ctx.session_id
    signing_key = ctx.signing_key
    sso_session = ctx.sso_session
    store = ctx.store


    sso_provider = None
    sso_problem = ""
    try:
        sso_cfg = sso.config_from_env()
        sso_provider = sso.Provider(sso_cfg, sso_session) if (sso_cfg and sso_session is not None) else (sso.Provider(sso_cfg) if sso_cfg else None)
    except sso.SSOError as e:
        sso_problem = e.message
        log.warning("single sign-on is not active: %s", e.message)

    SSO_COOKIE = "td_sso"

    def sso_redirect_uri(request: Request) -> str:
        return f"{public_base(request)}/api/sso/callback"

    def sso_fail(request: Request, err: sso.SSOError | str, code: str = "failed") -> RedirectResponse:
        message = err.message if isinstance(err, sso.SSOError) else err
        code = err.code if isinstance(err, sso.SSOError) else code
        people.log(now(), "?", None, f"Failed SSO sign-in ({code})", client=client_of(request))
        resp = RedirectResponse(f"/?sso_error={urlencode({'m': message})[2:]}", status_code=303)
        resp.delete_cookie(SSO_COOKIE, path="/")
        return resp

    @app.get("/api/sso/status")
    def sso_status() -> dict:
        return {"enabled": sso_provider is not None, "button_text": sso_provider.cfg.button_text if sso_provider else ""}

    @app.get("/api/sso/login")
    def sso_login(request: Request):
        if sso_provider is None:
            raise HTTPException(404, "Single sign-on is not set up")
        client = client_of(request)
        if limiter.blocked(client):
            raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
        try:
            url, remembered = sso_provider.start(sso_redirect_uri(request))
        except sso.SSOError as e:
            return sso_fail(request, e)
        resp = RedirectResponse(url, status_code=303)
        # Lax, not Strict: the provider sends the browser back here from another site, and a Strict cookie would not come along
        resp.set_cookie(SSO_COOKIE, make_token(signing_key, ttl=600, extra={"st": remembered["state"], "nn": remembered["nonce"], "vf": remembered["verifier"]}),
                        max_age=600, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")
        return resp

    @app.get("/api/sso/callback")
    def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if sso_provider is None:
            raise HTTPException(404, "Single sign-on is not set up")
        client = client_of(request)
        claims = token_claims(signing_key, request.cookies.get(SSO_COOKIE))
        if error:
            return sso_fail(request, "The provider did not sign you in" + (": access was denied" if error == "access_denied" else ""), error[:30])
        if not claims or not code or not hmac.compare_digest(str(claims.get("st", "")).encode(), state.encode()):
            limiter.failed(client)
            return sso_fail(request, sso.SSOError("state", "That sign-in link is not valid or has expired. Start again"))
        try:
            who_claims = sso_provider.finish(code, sso_redirect_uri(request), {"nonce": claims["nn"], "verifier": claims["vf"]})
            who = people.sso_login(who_claims, sso_provider.cfg.default_role, now())
        except (sso.SSOError, AccountError) as e:
            return sso_fail(request, e if isinstance(e, sso.SSOError) else sso.SSOError("account", str(e)))
        sid = secrets.token_urlsafe(24)
        store.session_start(sid, time.time(), SESSION_SECONDS, who.id, client, accounts.describe_agent(request.headers.get("user-agent", "")))
        people.record_login(who.id, now())
        people.log(now(), who.username, who.role, "Signed in with SSO", client=client)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, make_token(signing_key, sid=sid), max_age=SESSION_SECONDS, httponly=True, secure=config.cookie_secure, samesite="strict", path="/")
        resp.delete_cookie(SSO_COOKIE, path="/")
        return resp

    @app.get("/api/sso/info", dependencies=auth)
    def sso_info(request: Request) -> dict:
        """What an administrator needs to set SSO up or check it: the address to register with the provider, and who is allowed."""
        c = sso_provider.cfg if sso_provider else None
        return {"enabled": c is not None, "problem": sso_problem, "issuer": c.issuer if c else "", "client_id": c.client_id if c else "", "redirect_uri": sso_redirect_uri(request),
                "allowed_domains": c.allowed_domains if c else [], "default_role": c.default_role if c else "viewer"}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        who = current_user(request)
        if who:
            people.log(now(), who.username, who.role, "Signed out", client=client_of(request))
        sid = session_id(request)
        if sid:
            store.session_end(sid)  # the cookie stops working for good, even if someone copied it
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/branding")
    def branding() -> dict:
        # Public on purpose: the login screen shows the logo before anyone has signed in. Nothing secret is in it.
        cur = appsettings.current(env, store.get_settings())
        return {"logo_text": cur["logo_text"], "accent": cur["accent"], "logo_url": ctx.logo_url()}

    @app.get("/api/me")
    def me(request: Request) -> dict:
        who = current_user(request)
        return {"authenticated": who is not None, "user": who.public() if who else None}
