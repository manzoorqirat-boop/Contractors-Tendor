"""The shared setup that every route module receives: the stores, helpers and settings built once in create_app."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..accounts import Accounts
    from ..backup import Backups
    from ..bids import BidBook
    from ..boq.service import BoqService
    from ..config import Settings
    from ..letters import Letters
    from ..money import MoneyBook
    from ..runner import RunManager
    from ..store import SeenStore
    from ..web import AppConfig


@dataclass
class Ctx:
    """Everything create_app builds once and the route modules share. Read-only by convention: change a setting through the store, not here."""

    ai_call: Callable[..., Any]
    api_tokens: Any
    auth: Any
    backups: Backups
    bid_as_tender: Callable[..., Any]
    bid_error: Callable[..., Any]
    bid_full: Callable[..., Any]
    bid_link: Callable[..., Any]
    bid_or_404: Callable[..., Any]
    bids: BidBook
    boq: BoqService
    boq_call: Callable[..., Any]
    budget: Any
    builtin_keys: Any
    client_of: Callable[..., Any]
    company_facts: Callable[..., Any]
    config: AppConfig
    current_user: Callable[..., Any]
    deep_probe: Callable[..., Any]
    encryption_problem: Any
    env: Settings
    hooks: Any
    jobs: Any
    keep_source: Callable[..., Any]
    letter_book: Letters
    limiter: Any
    manager: RunManager
    money: MoneyBook
    notify: Callable[..., Any]
    now: Callable[..., Any]
    owner: Any
    people: Accounts
    portal_entry: Callable[..., Any]
    portal_health: Callable[..., Any]
    portal_names: Any
    portal_search: Any
    portal_states: Any
    portals_now: Callable[..., Any]
    probe: Any
    profile: Callable[..., Any]
    public_base: Callable[..., Any]
    refresh_portals: Callable[..., Any]
    refresh_priorities: Callable[..., Any]
    run_job: Callable[..., Any]
    sealer: Any
    session_id: Callable[..., Any]
    shape: Callable[..., Any]
    signing_key: Any
    size_limits: Callable[..., Any]
    sso_session: Any
    storage_note: Any
    store: SeenStore
    tender_for_ai: Callable[..., Any]
