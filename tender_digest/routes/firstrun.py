"""First run: what is set up, and sample data to try the app with."""
from __future__ import annotations

from fastapi import FastAPI

from .. import appsettings, sample
from .context import Ctx


def register(app: FastAPI, ctx: Ctx) -> None:
    """Adds this area's routes to the app. The code is the part of create_app that used to sit here, unchanged."""
    auth = ctx.auth
    bids = ctx.bids
    boq = ctx.boq
    company_facts = ctx.company_facts
    env = ctx.env
    manager = ctx.manager
    now = ctx.now
    profile = ctx.profile
    store = ctx.store


    @app.get("/api/setup", dependencies=auth)
    def setup_status() -> dict:
        """A short list of what a new installation still needs, worked out from what is stored. Nothing here is a setting: it only reads."""
        cur = appsettings.current(env, store.get_settings())
        runs = store.list_runs(1)
        sample_rows = store.count_portal("sample")
        steps = [
            {"key": "run", "title": "Collect tenders", "why": "Reads the portals for tenders. The first run takes 20 to 40 minutes and you can keep working while it goes.", "done": store.count() - sample_rows > 0 or bool(runs and runs[0]["status"] == "ok"), "href": "#/runs", "where": "Runs"},
            {"key": "profile", "title": "Describe your company", "why": "The AI assistant uses it to score tenders for fit and to write letters.", "done": len(profile().strip()) >= 20, "href": "#/vault", "where": "Vault > Company"},
            {"key": "facts", "title": "Add your company facts", "why": "Turnover, registration class and similar figures are checked against each tender's eligibility.", "done": len(company_facts()) >= 3, "href": "#/vault", "where": "Vault > Company"},
            {"key": "vault", "title": "Add your standard papers to the Vault", "why": "GST, PAN, registration, balance sheets: bids match their checklist to these.", "done": len(bids.vault_list()) >= 3, "href": "#/vault", "where": "Vault > Documents"},
            {"key": "email", "title": "Choose who gets the daily email", "why": "New civil tenders arrive in the inbox each morning.", "done": bool(manager.mail_configured and cur["recipients"]), "href": "#/settings", "where": "Settings > Daily email", "admin": True},
            {"key": "bid", "title": "Start your first bid", "why": "Open a tender and press Start bid to get the checklist, price tools and team tools.", "done": len([b for b in bids.list() if b["portal"] != "sample"]) > 0, "href": "#/tenders", "where": "Tenders"},
            {"key": "catalogue", "title": "Load your item catalogue (optional)", "why": "Lets a BOQ be costed from your own rates.", "done": (boq.catalogue(False)["counts"].get("item_master", 0) or 0) > 0, "href": "#/vault", "where": "Vault > Item catalogue", "optional": True},
        ]
        return {"steps": steps, "done": sum(1 for s in steps if s["done"]), "total": len(steps), "sample": {"loaded": sample_rows > 0, "tenders": sample_rows}}

    @app.post("/api/sample-data", dependencies=auth)
    def load_sample_data() -> dict:
        """Adds a few fake tenders so every page has something to show. Never emailed, never sent to the AI."""
        added = 0
        for t in sample.tenders(now()):
            if not store.get_tender(t.key):
                store.keep(t, now())
                store.mark_emailed([t.key])
                added += 1
        return {"added": added, "tenders": store.count_portal("sample")}

    @app.delete("/api/sample-data", dependencies=auth)
    def remove_sample_data() -> dict:
        """Removes the sample tenders and any bids (with their checklists, comments and BOQs) started on them."""
        removed_bids = 0
        for b in bids.list():
            if b["portal"] == "sample":
                boq.store.delete_boqs(b["key"])
                bids.delete_source(b["key"])
                removed_bids += bool(bids.delete(b["id"]))
        keys = store.delete_portal("sample")
        return {"tenders": len(keys), "bids": removed_bids}
