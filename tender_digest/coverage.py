"""Which portals can this installation really read? A test that goes past "the site answers" to "tenders and their dates come out".

Whether a portal works depends on where the app runs: a government site may refuse a data-centre address that it accepts from an office, or change its
layout without notice. So the only proof is a test run on the customer's own server. `deep_probe` does that for one portal: it reads the department
list, then the tenders of the busiest department, then a few detail pages, and counts what came out. `python -m tender_digest.coverage` runs it for every
portal and prints, and optionally saves, the result; the Settings page shows the saved results, with a CSV for the customer's records.

Verdicts:
  proven    tenders were listed, nearly all with an ID and a closing date, and a detail page was read
  partial   tenders were listed, but dates are missing or no detail page could be read: the reader needs attention
  empty     the portal answers but listed no tenders (it may truly have none open; try again another day)
  failed    could not be read: the reason (captcha, login, refuses this server, not reachable, different software...)
  untested  never tested here
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from datetime import datetime
from typing import Any

from . import access
from .config import Portal
from .gepnic import PortalError, apply_detail, is_public_host

SAMPLE_TENDERS = 3        # how many detail pages are read (each is one polite request)
PROVEN_SHARE = 0.9        # share of listed tenders that must carry an ID and a closing date
VERDICTS = {
    "proven": "Proven: tenders and their closing dates were read",
    "partial": "Partly: it answers, but some details could not be read",
    "empty": "Answers, but listed no tenders",
    "failed": "Cannot be read",
    "untested": "Not tested yet",
}


def _share(n: int, total: int) -> int:
    return round(100 * n / total) if total else 0


def deep_probe(portal: Portal, client_factory: Any = None, delay: float = 0.6, timeout: int = 20, clock: Any = time.monotonic) -> dict[str, Any]:
    """Reads one portal as the daily run would, but only a little of it. Never raises. The result is JSON-safe."""
    started = clock()
    out: dict[str, Any] = {
        "verdict": "failed", "kind": "unknown", "detail": "", "departments": 0, "advertised": 0, "department": "", "listed": 0,
        "with_id_pct": 0, "with_closing_pct": 0, "with_value_pct": 0, "details_read": 0, "details_tried": 0, "seconds": 0.0,
    }
    host = (portal.base_url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]).lower()
    if not is_public_host(host):
        return {**out, "kind": "blocked", "detail": "The address points to a private network, so it was not contacted", "seconds": 0.0}
    try:
        if client_factory:
            client = client_factory(portal)
        else:
            from . import gem
            from .gepnic import GepnicClient
            client = (gem.GemClient(portal.base_url, delay=delay, timeout=timeout) if gem.is_gem(portal.base_url)
                      else GepnicClient(portal.base_url, delay=delay, timeout=timeout))
        orgs = client.organisations(attempts=1)
        out.update(kind="ok", departments=len(orgs), advertised=sum(c for _, c, _ in orgs))
        busy = max((o for o in orgs if o[1] > 0), key=lambda o: o[1], default=None)
        if busy is None:
            out.update(verdict="empty", detail="The department list has no open tenders.")
            return {**out, "seconds": round(clock() - started, 1)}
        out["department"] = busy[0]
        tenders = client.tenders_of(busy[2], portal.key)
        out["listed"] = len(tenders)
        if not tenders:
            out.update(verdict="empty", detail=f"{busy[0]} says it has {busy[1]} tenders but none could be read from its list.")
            return {**out, "seconds": round(clock() - started, 1)}
        out["with_id_pct"] = _share(sum(1 for t in tenders if t.tender_id), len(tenders))
        out["with_closing_pct"] = _share(sum(1 for t in tenders if t.closing), len(tenders))
        values = 0
        for t in tenders[:SAMPLE_TENDERS]:
            out["details_tried"] += 1
            try:
                apply_detail(t, client.detail(t))
            except PortalError as e:
                out["detail"] = f"A detail page could not be read: {str(e)[:160]}"
                continue
            out["details_read"] += 1
            values += 1 if t.value is not None else 0
        out["with_value_pct"] = _share(values, out["details_read"])
        solid = out["with_id_pct"] >= 100 * PROVEN_SHARE and out["with_closing_pct"] >= 100 * PROVEN_SHARE
        out["verdict"] = "proven" if solid and out["details_read"] > 0 else "partial"
        if out["verdict"] == "partial" and not out["detail"]:
            out["detail"] = ("Closing dates or IDs are missing on many tenders: the portal's layout may have changed." if not solid
                             else "No detail page could be read.")
    except PortalError as e:
        out.update(verdict="failed", kind=e.kind, detail=str(e)[:300])
    except Exception as e:  # a test must always give an answer
        out.update(verdict="failed", kind="unreachable", detail=f"{type(e).__name__}: {str(e)[:200]}")
    out["seconds"] = round(clock() - started, 1)
    return out


def record(store: Any, portal: Portal, result: dict[str, Any], at: datetime) -> None:
    """Saves a deep test: the full result for the report, and the plain status so the rest of the app (and portal health) learns from it too."""
    store.put_portal_coverage(portal.key, result, at)
    store.put_portal_status(portal.key, result["kind"] if result["verdict"] == "failed" else "ok", result["detail"] if result["verdict"] == "failed" else "",
                            at, result["departments"], result["advertised"], "check")


def report(saved: dict[str, dict[str, Any]], portals: list[Portal], enabled: set[str]) -> dict[str, Any]:
    """The test results ({key: {result, checked}}, as the store keeps them) for every portal, the ones never tested shown as such, and a summary."""
    rows = []
    for p in portals:
        r = saved.get(p.key)
        result = r["result"] if r else {}
        verdict = result.get("verdict", "untested")
        rows.append({
            "key": p.key, "name": p.name, "state": p.state, "base_url": p.base_url, "enabled": p.key in enabled,
            "verdict": verdict, "verdict_text": VERDICTS[verdict], "checked": r["checked"] if r else None,
            **{k: result.get(k, 0 if k not in ("kind", "detail", "department") else "") for k in
               ("kind", "detail", "department", "departments", "advertised", "listed", "with_id_pct", "with_closing_pct", "with_value_pct", "details_read", "details_tried", "seconds")},
            "label": access.describe(result["kind"])["label"] if result.get("kind") and verdict == "failed" else "",
        })
    count = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    return {"rows": rows, "summary": {"portals": len(rows), **count, "tested": len(rows) - count["untested"]}}


def to_csv(rep: dict[str, Any]) -> str:
    from .digest import safe_cell
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Portal", "State", "Address", "Switched on", "Result", "Why not (if it failed)", "Departments", "Open tenders advertised", "Department sampled",
                "Tenders listed", "With ID %", "With closing date %", "Detail pages read", "Detail pages tried", "Tested at", "Notes"])
    for r in rep["rows"]:
        w.writerow([safe_cell(c) for c in [
            r["name"], r["state"], r["base_url"], "yes" if r["enabled"] else "no", r["verdict_text"], r["label"], r["departments"], r["advertised"], r["department"],
            r["listed"], r["with_id_pct"], r["with_closing_pct"], r["details_read"], r["details_tried"], r["checked"] or "", r["detail"],
        ]])
    return "﻿" + out.getvalue()


# ---- the command line, run on the customer's own server ---------------------------------------------------------------

def main(argv: list[str] | None = None, probe: Any = None, out: Any = None) -> int:
    """python -m tender_digest.coverage [--catalogue] [--only a,b] [--csv FILE] [--no-save]"""
    from . import appsettings, catalogue
    from .config import load_settings
    from .store import SeenStore

    parser = argparse.ArgumentParser(prog="python -m tender_digest.coverage", description="Test which portals this server can really read")
    parser.add_argument("--catalogue", action="store_true", help="also test the known portals that are not added yet (nothing is added or switched on)")
    parser.add_argument("--only", help="comma-separated portal keys")
    parser.add_argument("--csv", metavar="FILE", help="write the report as a CSV file")
    parser.add_argument("--no-save", action="store_true", help="do not keep the results in the database (the Settings page then does not show them)")
    parser.add_argument("--delay", type=float, default=0.6, help="seconds between requests to a portal (be polite)")
    args = parser.parse_args(argv)
    say = out or (lambda text: print(text, flush=True))

    settings = load_settings(require_mail=False)
    store = SeenStore(settings.state_path)
    try:
        saved = store.get_settings()
        portals = appsettings.all_portals(settings, saved)
        added = {p.key for p in portals}
        if args.catalogue:
            have = {p.base_url.rstrip("/").lower() for p in portals}
            taken = set(added)
            for e in catalogue.listing(set()):
                if e["base_url"].rstrip("/").lower() not in have:
                    try:
                        new = Portal(**appsettings.clean_portal({"name": e["name"], "state": e["state"], "base_url": e["base_url"]}, taken))
                    except appsettings.SettingsError as err:  # e.g. an address this installation's allowed host endings do not cover
                        say(f"Skipped {e['name']}: {err}")
                        continue
                    taken.add(new.key)
                    portals.append(new)
        if args.only:
            wanted = {k.strip() for k in args.only.split(",") if k.strip()}
            portals = [p for p in portals if p.key in wanted]
        if not portals:
            say("No portals to test.")
            return 1
        run = probe or (lambda p: deep_probe(p, delay=args.delay))
        now = datetime.now()
        results: dict[str, dict[str, Any]] = dict(store.portal_coverages()) if not args.no_save else {}
        for i, p in enumerate(portals, 1):
            r = run(p)
            say(f"[{i}/{len(portals)}] {p.name}: {VERDICTS[r['verdict']]}" + (f" ({r['detail']})" if r["detail"] else "")
                + (f" - {r['listed']} tenders listed from {r['department']}, {r['with_closing_pct']}% with a closing date" if r["listed"] else ""))
            results[p.key] = {"result": r, "checked": now.isoformat(timespec="seconds")}
            if not args.no_save and p.key in added:   # portals not added are tested and reported, but never stored or switched on
                record(store, p, r, now)
        tested = [p for p in portals if p.key in results]
        rep = report(results, tested, set(appsettings.current(settings, saved)["portals"]))
        s = rep["summary"]
        say(f"\n{s['proven']} of {s['portals']} portals proven readable from this server; {s['partial']} partly, {s['empty']} empty, {s['failed']} cannot be read.")
        if args.csv:
            with open(args.csv, "w", encoding="utf-8", newline="") as f:
                f.write(to_csv(rep))
            say(f"Report written to {args.csv}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
