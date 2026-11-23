"""Teamwork: who can be given work, comments on a bid, and approval to bid."""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Request

from ..accounts import AccountError, Principal
from ..api_common import (
    CommentBody,
    FieldHTTPException,
    OwnEmail,
    SignoffBody,
)
from ..bids import BidError
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bid_error = ctx.bid_error
    bid_link = ctx.bid_link
    bid_or_404 = ctx.bid_or_404
    bids = ctx.bids
    notify = ctx.notify
    now = ctx.now
    owner = ctx.owner
    people = ctx.people


    @app.get("/api/people", dependencies=auth)
    def people_directory() -> dict:
        """Names to pick from when assigning a task. No contact details."""
        found = people.directory()
        if not any(p["username"] == owner.username for p in found):
            found.append({"username": owner.username, "name": "Owner"})
        return {"people": found}

    @app.post("/api/account/email", dependencies=auth)
    def set_own_email(body: OwnEmail, request: Request) -> dict:
        user: Principal = request.state.user
        if user.owner:
            raise HTTPException(422, "The owner login has no stored email. Add yourself as a person in Settings > People to get notifications")
        try:
            return {"email": people.set_own_email(user.id, body.email)}
        except AccountError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/my-work", dependencies=auth)
    def my_work(request: Request) -> dict:
        """Open checklist items assigned to the signed-in person, on bids still in progress."""
        return {"items": bids.items_for_owner(request.state.user.username)}

    @app.get("/api/bids/{bid_id}/comments", dependencies=auth)
    def list_comments(bid_id: int) -> dict:
        bid_or_404(bid_id)
        return {"comments": bids.comments(bid_id)}

    @app.post("/api/bids/{bid_id}/comments", dependencies=auth)
    def add_comment(bid_id: int, body: CommentBody, request: Request) -> dict:
        bid = bid_or_404(bid_id)
        user: Principal = request.state.user
        try:
            c = bids.add_comment(bid_id, user.username, body.text, now())
        except BidError as e:
            raise bid_error(e) from None
        mentioned = {m.lower() for m in re.findall(r"@([A-Za-z0-9._-]{3,40})", body.text)}
        tell = (mentioned | {(bid.get("owner") or "").strip().lower()}) - {user.username.lower(), ""}
        notify(people.emails_for(sorted(tell)), f"{user.name} commented on a bid",
               f"{user.name} wrote on {(bid['description'] or bid['title'])[:100]} (ID {bid['tender_id']}):\n\n{body.text.strip()[:600]}{bid_link(bid_id)}")
        return c

    @app.delete("/api/bids/{bid_id}/comments/{comment_id}", dependencies=auth)
    def delete_comment(bid_id: int, comment_id: int, request: Request) -> dict:
        user: Principal = request.state.user
        if not bids.delete_comment(bid_id, comment_id, user.username, user.can("admin")):
            raise HTTPException(404, "No such comment, or it is not yours to remove")
        return {"removed": comment_id}

    @app.post("/api/bids/{bid_id}/signoff", dependencies=auth)
    def bid_signoff(bid_id: int, body: SignoffBody, request: Request) -> dict:
        """Asking for, giving, refusing or withdrawing approval to bid. Only administrators approve or decline."""
        bid = bid_or_404(bid_id)
        user: Principal = request.state.user
        cur = bid.get("signoff") or {}
        note = body.note.strip()
        if len(note) > 500:
            raise FieldHTTPException(422, f"The note can be at most 500 characters, and this is {len(note)}. Shorten it and try again", "note")
        title = (bid["description"] or bid["title"])[:100]
        stamp = now().isoformat(timespec="seconds")
        if body.action == "request":
            if bid["stage"] != "interested":
                raise HTTPException(422, "Approval is asked for while a bid is still at Interested")
            mark = {"state": "requested", "requested_by": user.username, "by": user.username, "at": stamp, "note": note}
            notify(people.admin_emails(), f"{user.name} asks approval to bid", f"{user.name} asks for approval to bid on {title} (ID {bid['tender_id']}).\n\n{note}{bid_link(bid_id)}")
        elif body.action == "withdraw":
            if not cur or (cur.get("requested_by") != user.username and not user.can("admin")):
                raise HTTPException(403, "Only the person who asked, or an administrator, can withdraw the request")
            mark = None
        elif body.action in ("approve", "decline"):
            if not user.can("admin"):
                raise HTTPException(403, "Only an administrator can approve or decline a bid")
            mark = {"state": "approved" if body.action == "approve" else "declined", "requested_by": cur.get("requested_by") or user.username, "by": user.username, "at": stamp, "note": note}
            if cur.get("requested_by") and cur["requested_by"] != user.username:
                notify(people.emails_for([cur["requested_by"]]), f"Bid {'approved' if body.action == 'approve' else 'declined'}: {title[:60]}",
                       f"{user.name} {'approved' if body.action == 'approve' else 'declined'} the bid on {title} (ID {bid['tender_id']}).\n\n{note}{bid_link(bid_id)}")
        else:
            raise HTTPException(422, "Unknown action")
        bids.set_json(bid_id, "signoff", mark, now())
        return {"signoff": mark}
