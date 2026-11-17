"""Portals: add your own, and see which ones the app cannot read."""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Response

from .. import appsettings, catalogue, reminders
from ..api_common import (
    WebhookTest,
)
from ..config import Portal
from ..gepnic import PortalError
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    builtin_keys = ctx.builtin_keys
    env = ctx.env
    manager = ctx.manager
    now = ctx.now
    portal_entry = ctx.portal_entry
    portals_now = ctx.portals_now
    probe = ctx.probe
    refresh_portals = ctx.refresh_portals
    store = ctx.store


    @app.get("/api/portals", dependencies=auth)
    def list_portals() -> dict:
        saved, statuses = store.get_settings(), store.portal_statuses()
        entries = [portal_entry(p, saved, statuses) for p in portals_now()]
        return {"portals": entries, "manual": [e for e in entries if e["manual"]]}

    @app.post("/api/portals", dependencies=auth)
    def add_portal(body: dict[str, Any]) -> dict:
        saved = store.get_settings()
        existing = appsettings.all_portals(env, saved)
        try:
            new = appsettings.clean_portal(body, {p.key for p in existing})
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        same = next((p for p in existing if p.base_url.rstrip("/").lower() == new["base_url"].lower()), None)
        if same:
            raise HTTPException(422, f"That address is already in the list as '{same.name}'")
        result = probe(new["base_url"])
        # Added even when the app cannot read it: it then appears on the "check manually" list with the reason.
        store.put_settings({"custom_portals": [*saved.get("custom_portals", []), new]})
        store.put_portal_status(new["key"], result["kind"], result["detail"], now(), result["departments"], result["tenders"])
        if result["kind"] == "ok":
            store.put_settings({"portals": [*appsettings.current(env, saved)["portals"], new["key"]]})
        refresh_portals()
        return portal_entry(Portal(**new), store.get_settings(), store.portal_statuses())

    @app.get("/api/portals/catalogue", dependencies=auth)
    def portal_catalogue() -> dict:
        """Known government portals, each marked as already added or not, for the tick-list in Settings > Portals."""
        return {"entries": catalogue.listing({p.base_url for p in portals_now()})}

    @app.post("/api/portals/bulk", dependencies=auth)
    def add_portals_bulk(body: dict[str, Any]) -> dict:
        """Adds several catalogue portals at once. They are saved but not yet switched on: each one is then checked (POST .../check?enable=true),
        so a portal that cannot be read never makes a daily run slower or fail."""
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 40 or not all(isinstance(i, str) for i in ids):
            raise HTTPException(422, "Choose between 1 and 40 portals")
        saved = store.get_settings()
        existing = appsettings.all_portals(env, saved)
        taken, urls = {p.key for p in existing}, {p.base_url.rstrip("/").lower() for p in existing}
        customs, added, skipped = list(saved.get("custom_portals", [])), [], []
        for entry_id in dict.fromkeys(ids):
            entry = catalogue.find(entry_id)
            if not entry:
                skipped.append({"id": entry_id, "reason": "Not in the list of known portals"})
                continue
            if entry["base_url"].rstrip("/").lower() in urls:
                skipped.append({"id": entry_id, "reason": "Already added"})
                continue
            try:
                new = appsettings.clean_portal(entry, taken)
            except appsettings.SettingsError as e:
                skipped.append({"id": entry_id, "reason": str(e)})
                continue
            customs.append(new)
            taken.add(new["key"])
            urls.add(new["base_url"].lower())
            added.append(new["key"])
        if added:
            store.put_settings({"custom_portals": customs})
            refresh_portals()
        return {"added": added, "skipped": skipped}

    @app.get("/api/portals/{key}/sample", dependencies=auth)
    def portal_sample(key: str):
        """What the portal actually returns to this server, saved as a file. For fixing or writing a reader for a portal the app cannot read yet."""
        portal = next((p for p in portals_now() if p.key == key), None)
        if not portal:
            raise HTTPException(404, "No such portal")
        client = manager.client_factory(portal, env)
        fetch = getattr(client, "sample", None)
        if fetch is None:
            raise HTTPException(422, "This portal's reader cannot make a sample")
        try:
            ctype, body = fetch()
        except PortalError as e:
            raise HTTPException(502, f"The portal could not be read: {e}") from None
        ext = "json" if "json" in ctype else "html"
        return Response(body, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="sample-{re.sub(r"[^a-z0-9-]", "", key)}.{ext}"'})

    @app.post("/api/portals/{key}/check", dependencies=auth)
    def check_portal(key: str, enable: bool = False) -> dict:
        """Checks one portal. With enable=true, a portal that turns out to be readable is also switched on for the daily run."""
        portal = next((p for p in portals_now() if p.key == key), None)
        if not portal:
            raise HTTPException(404, "No such portal")
        result = probe(portal.base_url)
        store.put_portal_status(key, result["kind"], result["detail"], now(), result["departments"], result["tenders"])
        if enable and result["kind"] == "ok":
            on = appsettings.current(env, store.get_settings())["portals"]
            if key not in on:
                store.put_settings({"portals": [*on, key]})
        return portal_entry(portal, store.get_settings(), store.portal_statuses())

    @app.delete("/api/portals/{key}", dependencies=auth)
    def remove_portal(key: str) -> dict:
        if key in builtin_keys:
            raise HTTPException(422, "Built-in portals cannot be removed. Switch them off in Settings instead")
        saved = store.get_settings()
        customs = saved.get("custom_portals", [])
        if key not in {p.get("key") for p in customs}:
            raise HTTPException(404, "No such portal")
        update: dict[str, Any] = {"custom_portals": [p for p in customs if p.get("key") != key]}
        if "portals" in saved:
            update["portals"] = [k for k in saved["portals"] if k != key]
        store.put_settings(update)
        store.delete_portal_status(key)
        refresh_portals()
        return {"removed": key}

    @app.post("/api/settings/test-webhook", dependencies=auth)
    def test_webhook(body: WebhookTest) -> dict:
        url = (body.url or "").strip() or appsettings.current(env, store.get_settings())["webhook_url"]
        if not url:
            raise HTTPException(422, "Type the webhook address first")
        try:
            reminders.post_webhook(url, "TendAI test: deadline reminders will arrive here.")
        except reminders.WebhookError as e:
            raise HTTPException(502, str(e)) from None
        return {"ok": True}
