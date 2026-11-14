"""Reads each portal and records what is new in the store. Sending is a separate step."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import filters
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

    @property
    def failed(self) -> bool:
        return self.organisations_read == 0 and bool(self.errors)


ClientFactory = Callable[[Portal, Settings], GepnicClient]
Progress = Callable[[str], None]


def default_client(portal: Portal, settings: Settings) -> GepnicClient:
    return GepnicClient(portal.base_url, delay=settings.request_delay)


def collect(
    settings: Settings, store: SeenStore, now: datetime, client_factory: ClientFactory = default_client, progress: Progress | None = None
) -> list[PortalReport]:
    say = progress or (lambda _m: None)
    reports = []
    for portal in settings.portals:
        report = PortalReport(portal)
        say(f"{portal.name}: reading the department list")
        client = client_factory(portal, settings)
        report.search_url = client.search_url
        try:
            _collect_portal(portal, settings, client, store, now, report, say)
        except PortalError as e:
            report.errors.append(f"could not read the portal: {e}")
            log.warning("%s: %s", portal.key, e)
            say(f"{portal.name}: could not be read ({e})")
        say(f"{portal.name}: done, {report.listed} listed, {report.new_civil} new civil")
        reports.append(report)
    return reports


def _collect_portal(
    portal: Portal, settings: Settings, client: GepnicClient, store: SeenStore, now: datetime, report: PortalReport, say: Progress
) -> None:
    include = portal.org_include or settings.org_include
    fetched = 0
    for name, count, link in client.organisations():
        if count == 0 or not filters.wants_organisation(name, include):
            continue
        try:
            tenders = client.tenders_of(link, portal.key)
        except PortalError as e:
            report.errors.append(f"{name}: {e}")
            continue
        report.organisations_read += 1
        report.listed += len(tenders)
        say(f"{portal.name}: {name} ({len(tenders)} tender{'' if len(tenders) == 1 else 's'})")

        for tender in tenders:
            if store.is_seen(tender.key):
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
