"""Deciding which tenders are civil work. Pure functions over the settings, easy to test."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .config import DEFAULT_CIVIL_CATEGORY, Settings
from .gepnic import Tender


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


def wants_organisation(name: str, include: str) -> bool:
    return bool(_rx(include).search(name))


def is_civil(tender: Tender, settings: Settings, detail_known: bool) -> bool:
    """Civil when the title, description or category says so. Detail pages are the reliable source: a PWD department
    also issues electrical and supply tenders, and many titles are only an estimate number.

    Without a detail page (fetch limit reached or the page failed), a department that is civil by nature is trusted.
    """
    text = " ".join([tender.title, tender.description, tender.category, tender.sub_category])
    if settings.exclude and _rx(settings.exclude).search(text):
        return False
    if (
        tender.category
        and settings.non_civil_category
        and _rx(settings.non_civil_category).search(tender.category)
        and not _rx(DEFAULT_CIVIL_CATEGORY).search(tender.category)
    ):
        return False
    if _rx(settings.civil_keywords).search(text):
        return True
    return not detail_known and bool(_rx(settings.strong_civil_org).search(tender.organisation))


def meets_value(tender: Tender, settings: Settings) -> bool:
    """A tender with no stated value is kept: dropping it silently could hide a real opportunity."""
    return tender.value is None or tender.value >= settings.min_value


def is_recent(tender: Tender, now: datetime, lookback_days: int) -> bool:
    return tender.published is None or tender.published >= now - timedelta(days=lookback_days)


def closing_soon(tender: Tender, now: datetime, days: int) -> bool:
    return tender.closing is not None and now <= tender.closing <= now + timedelta(days=days)
