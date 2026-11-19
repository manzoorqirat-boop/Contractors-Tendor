"""The company's logo picture: uploaded by an administrator, shown to everyone (it is on the sign-in screen, so it must not need a login)."""
from __future__ import annotations

import base64
import binascii
import re

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from .. import branding
from .context import Ctx


class LogoBody(BaseModel):
    file_base64: str


def register(app: FastAPI, ctx: Ctx) -> None:
    auth = ctx.auth
    current_user = ctx.current_user
    now = ctx.now
    people = ctx.people
    store = ctx.store

    @app.get("/api/branding/logo")
    def logo_picture() -> Response:
        # Public on purpose, like /api/branding: the sign-in screen shows it. The address carries ?v=<when it changed>, so a long cache is safe.
        logo = store.get_logo()
        if not logo:
            raise HTTPException(404, "No logo picture has been uploaded")
        return Response(logo["data"], media_type=logo["mime"], headers={"Cache-Control": "public, max-age=604800", "Content-Disposition": 'inline; filename="logo"'})

    @app.put("/api/settings/logo", dependencies=auth)
    def upload_logo(body: LogoBody, request: Request) -> dict:
        raw = re.sub(r"^data:[^,]*,", "", body.file_base64.strip())
        try:
            data = base64.b64decode(raw, validate=True)
            mime, width, height = branding.check(data)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(422, str(e) if isinstance(e, branding.LogoError) else "The file could not be read as a picture") from None
        at = now()
        store.put_logo(mime, data, at)
        who = current_user(request)
        if who:
            people.log(at, who.username, who.role, "Changed the company logo picture")
        return {"ok": True, "width": width, "height": height, "bytes": len(data), "logo_url": ctx.logo_url()}

    @app.delete("/api/settings/logo", dependencies=auth)
    def remove_logo(request: Request) -> dict:
        removed = store.delete_logo()
        who = current_user(request)
        if removed and who:
            people.log(now(), who.username, who.role, "Removed the company logo picture")
        return {"ok": True, "removed": removed, "logo_url": None}
