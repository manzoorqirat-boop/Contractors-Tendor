"""Backup, runs and settings."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import appsettings, sealing
from ..api_common import (
    RunBody,
)
from ..backup import BackupError
from ..runner import Busy, NotConfigured
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    backups = ctx.backups
    bids = ctx.bids
    encryption_problem = ctx.encryption_problem
    env = ctx.env
    manager = ctx.manager
    now = ctx.now
    people = ctx.people
    portal_names = ctx.portal_names
    portals_now = ctx.portals_now
    refresh_priorities = ctx.refresh_priorities
    run_job = ctx.run_job
    sealer = ctx.sealer
    storage_note = ctx.storage_note
    store = ctx.store


    @app.get("/api/backup/status", dependencies=auth)
    def backup_status() -> dict:
        return {**backups.status(), "storage_warning": storage_note,
                "encryption": sealing.describe(sealer, encryption_problem, bids.plain_files() if sealer.enabled else 0, people.plain_secrets() if sealer.enabled else 0)}

    @app.post("/api/backup/offsite-now", dependencies=auth)
    def backup_offsite_now():
        """Sends a full backup to the off-site bucket now, instead of waiting for tonight."""
        if backups.remote is None:
            raise HTTPException(422, backups.remote_problem or "Off-site backup is not set up. See the README (BACKUP_S3_BUCKET and the other BACKUP_S3_ variables)")
        def work() -> dict:
            state = backups.upload_offsite(now(), force=True)
            if state.get("last_error"):
                raise HTTPException(502, state["last_error"])
            return {"ok": True, **backups.status()["offsite"]}
        return run_job("offsite", "now", work)

    @app.get("/api/backup", dependencies=auth)
    def backup_download():
        """Everything the app keeps (both databases and the vault files) as one zip, to save somewhere safe."""
        try:
            path = backups.build_zip(now())
        except BackupError as e:
            raise HTTPException(409, str(e)) from None
        return FileResponse(path, media_type="application/zip", filename=f"tendai-backup-{now().strftime('%Y-%m-%d')}.zip", background=BackgroundTask(path.unlink, missing_ok=True))

    # ---- runs ------------------------------------------------------------------------------------------------

    @app.get("/api/runs", dependencies=auth)
    def runs() -> dict:
        current = store.running_run()
        return {"runs": store.list_runs(30), "running": current["id"] if current else None}

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def run_detail(run_id: int) -> dict:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "No such run")
        return run

    @app.post("/api/runs", status_code=202, dependencies=auth)
    def start_run(body: RunBody) -> dict:
        if body.portals is not None:
            unknown = [p for p in body.portals if p not in portal_names]
            if unknown or not body.portals:
                raise HTTPException(422, "Choose at least one known portal")
        try:
            return {"id": manager.start("manual", body.portals, body.send_email)}
        except Busy as e:
            raise HTTPException(409, str(e)) from None
        except NotConfigured as e:
            raise HTTPException(422, str(e)) from None

    # ---- settings --------------------------------------------------------------------------------------------

    @app.get("/api/settings", dependencies=auth)
    def get_settings() -> dict:
        return {**appsettings.current(env, store.get_settings()), "mail_configured": manager.mail_configured, "mail_from": env.mail_from,
                "available_portals": [{"key": p.key, "name": p.name} for p in portals_now()]}

    @app.put("/api/settings", dependencies=auth)
    def put_settings(body: dict[str, Any]) -> dict:
        try:
            store.put_settings(appsettings.clean(body, list(portal_names)))
        except appsettings.SettingsError as e:
            raise HTTPException(422, str(e)) from None
        refresh_priorities()   # a setting such as "learn from my choices" changes the order
        return get_settings()
