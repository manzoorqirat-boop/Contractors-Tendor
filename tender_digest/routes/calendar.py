"""The calendar feed: a private address that calendar apps subscribe to."""
from __future__ import annotations

import hmac
import secrets

from fastapi import FastAPI, HTTPException, Request, Response

from .. import mailer, reminders
from ..runner import NotConfigured
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bids = ctx.bids
    config = ctx.config
    manager = ctx.manager
    now = ctx.now
    public_base = ctx.public_base
    store = ctx.store


    def calendar_state(request: Request) -> dict:
        token = store.get_settings().get("calendar_token")
        return {"enabled": bool(token), "url": f"{public_base(request)}/api/calendar/{token}.ics" if token else ""}

    @app.get("/api/calendar", dependencies=auth)
    def get_calendar(request: Request) -> dict:
        return calendar_state(request)

    @app.post("/api/calendar/token", dependencies=auth)
    def new_calendar_token(request: Request) -> dict:
        """Makes (or replaces) the secret address. Replacing it stops every calendar that used the old one."""
        store.put_settings({"calendar_token": secrets.token_urlsafe(24)})
        return calendar_state(request)

    @app.delete("/api/calendar", dependencies=auth)
    def stop_calendar(request: Request) -> dict:
        store.put_settings({"calendar_token": None})
        return calendar_state(request)

    @app.get("/api/calendar/{token}.ics")
    def calendar_feed(token: str, request: Request) -> Response:
        # No login: calendar apps cannot sign in. The long random address is the secret, and the feed holds deadlines only.
        have = store.get_settings().get("calendar_token")
        if not have or not hmac.compare_digest(token.encode(), str(have).encode()):
            raise HTTPException(404, "Not found")
        return Response(reminders.build_ics(bids.list(), bids.vault_list(), now(), config.tz_name, config.app_url or public_base(request)),
                        media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": 'inline; filename="tendai-deadlines.ics"'})

    @app.post("/api/settings/test-email", dependencies=auth)
    def test_email() -> dict:
        try:
            return {"sent_to": manager.send_test()}
        except NotConfigured as e:
            raise HTTPException(422, str(e)) from None
        except mailer.MailError as e:
            # Brevo's reason (unverified sender, IP restriction, bad key) is what the person needs to fix it; it never contains the key.
            raise HTTPException(502, f"Brevo refused the email: {str(e)[:300]}") from None
