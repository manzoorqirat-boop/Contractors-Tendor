"""What is kept about a person: a copy, and erasure."""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request, Response

from .. import accounts, privacy
from ..accounts import Principal
from ..api_common import (
    EraseBody,
)
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bids = ctx.bids
    now = ctx.now
    people = ctx.people
    store = ctx.store


    def download(data: dict, name: str) -> Response:
        return Response(json.dumps(data, indent=2, ensure_ascii=False, default=str), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}.json"'})

    @app.get("/api/account/export", dependencies=auth)
    def export_own_data(request: Request):
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login is set by the server's variables and has no stored account")
        return download(privacy.export_person(people, bids, user.id), f"my-tendai-data-{user.username}")

    @app.get("/api/users/{user_id}/export", dependencies=auth)
    def export_person_data(user_id: int):
        if user_id == accounts.OWNER_ID:
            raise HTTPException(422, "The owner login is set by the server's variables and has no stored account")
        try:
            data = privacy.export_person(people, bids, user_id)
        except privacy.PrivacyError as e:
            raise HTTPException(404, str(e)) from None
        return download(data, f"tendai-data-{data['account']['username']}")

    @app.post("/api/users/{user_id}/erase", dependencies=auth)
    def erase_person_data(user_id: int, body: EraseBody, request: Request) -> dict:
        """Removes who a person was (name, email, password, sign-ins, and their name on comments and the activity log). The person must be deactivated first."""
        me: Principal = request.state.user
        if user_id == accounts.OWNER_ID or user_id == me.id:
            raise HTTPException(422, "You cannot erase your own account or the owner login")
        with people.lock:
            row = people.db.execute("SELECT username, active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No such person")
        if row["active"]:
            raise HTTPException(422, "Turn the account off first (Active off), then erase it")
        if body.confirm.strip().lower() != row["username"]:
            raise HTTPException(422, "Type the person's username exactly to confirm")
        try:
            out = privacy.erase_person(people, bids, store, user_id, now())
        except privacy.PrivacyError as e:
            raise HTTPException(422, str(e)) from None
        return out
