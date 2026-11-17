"""Reads each portal and records what is new in the store. Sending is a separate step."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import filters, gem
from .config import Portal, Settings
from .gepnic import GepnicClient, PortalError, Tender, apply_detail
from .store import SeenStore

log = logging.getLogger("tender_digest.collector")


@dataclass
class PortalReport:
    portal: Portal
    search_url: str = ""
    organisations_read: int = 0
    listed: int = 0
    new_civil: int = 0
    errors: list[str] = field(default_factory=list)
    access: str = "ok"  # ok, or why the app could not read it: captcha, login, otp, blocked, different_software, unreachable
    access_detail: str = ""

    @property
    def failed(self) -> bool:
        return self.organisations_read == 0 and bool(self.errors)


ClientFactory = Callable[[Portal, Settings], Any]
Progress = Callable[[str], None]
# Called with (tender, client) for a tender already known, so the bid workspace can look again at tenders being bid for.
Watcher = Callable[[Tender, Any], None]


def default_client(portal: Portal, settings: Settings):
    """The reader for a portal: GeM has its own, everything else is NIC GePNIC software."""
    if gem.is_gem(portal.base_url):
        return gem.GemClient(portal.base_url, delay=settings.request_delay)
    return GepnicClient(portal.base_url, delay=settings.request_delay)


def collect(
    settings: Settings, store: SeenStore, now: datetime, client_factory: ClientFactory = default_client, progress: Progress | None = None,
    watcher: Watcher | None = None,
) -> list[PortalReport]:
    say = progress or (lambda _m: None)
    reports = []
    for portal in settings.portals:
        report = PortalReport(portal)
        say(f"{portal.name}: reading the department list")
        client = client_factory(portal, settings)
        report.search_url = client.search_url
        try:
            _collect_portal(portal, settings, client, store, now, report, say, watcher)
        except PortalError as e:
            report.errors.append(f"could not read the portal: {e}")
            report.access, report.access_detail = e.kind, str(e)[:300]
            log.warning("%s: %s", portal.key, e)
            say(f"{portal.name}: could not be read ({e})")
        store.put_portal_status(portal.key, report.access, report.access_detail, now, report.organisations_read, report.listed)
        say(f"{portal.name}: done, {report.listed} listed, {report.new_civil} new civil")
        reports.append(report)
    return reports


def _collect_portal(
    portal: Portal, settings: Settings, client: Any, store: SeenStore, now: datetime, report: PortalReport, say: Progress,
    watcher: Watcher | None = None,
) -> None:
    include = portal.org_include or settings.org_include
    fetched = 0
    dept_error: PortalError | None = None
    for name, count, link in client.organisations():
        if count == 0 or not filters.wants_organisation(name, include):
            continue
        try:
            tenders = client.tenders_of(link, portal.key)
        except PortalError as e:
            report.errors.append(f"{name}: {e}")
            dept_error = e
            continue
        report.organisations_read += 1
        report.listed += len(tenders)
        say(f"{portal.name}: {name} ({len(tenders)} tender{'' if len(tenders) == 1 else 's'})")

        for tender in tenders:
            if store.is_seen(tender.key):
                if watcher:
                    try:
                        watcher(tender, client)
                    except Exception:  # watching a bid must never stop the collection
                        log.exception("bid watch failed for %s", tender.key)
                continue
            if not filters.is_recent(tender, now, settings.lookback_days):
                store.skip(tender.key, now)
                continue

            detail_known = False
            if fetched < settings.max_detail_fetches:
                fetched += 1
                if fetched % 50 == 0:
                    say(f"{portal.name}: read {fetched} tender detail pages, {report.new_civil} civil so far")
                try:
                    apply_detail(tender, client.detail(tender))
                    detail_known = True
                except PortalError as e:
                    # Not remembered, so tomorrow retries it while it is still recent.
                    report.errors.append(f"{tender.tender_id}: detail page unavailable ({e})")
                    log.info("detail failed for %s: %s", tender.key, e)
                    continue

            if filters.is_civil(tender, settings, detail_known) and filters.meets_value(tender, settings):
                store.keep(tender, now)
                report.new_civil += 1
            elif detail_known:
                store.skip(tender.key, now)
    if report.organisations_read == 0 and dept_error is not None:
        report.access, report.access_detail = dept_error.kind, str(dept_error)[:300]
