"""Settings, all from environment variables so nothing secret lives in the code or the repository."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORTALS = Path(__file__).with_name("portals.json")

# Which departments' tender lists are downloaded at all. Everything else on the portal is ignored.
DEFAULT_ORG_INCLUDE = (
    r"public works|\bpwd\b|cpwd|\br\s*&\s*b\b|roads?\b|buildings?\b|irrigation|water resources|water supply|jal\b|"
    r"municipal|mahanagar|nagar (palika|nigam|parishad)|development authority|housing|urban|rural|panchayat|"
    r"pmgsy|highway|nhai|construction|engineering|civil|railway|metro|cada\b|command area|nigam|vikas"
)

# Departments whose work is civil by nature: their tenders are kept without a keyword match.
DEFAULT_STRONG_CIVIL_ORG = (
    r"public works|\bpwd\b|cpwd|\br\s*&\s*b\b|roads? (and|&) buildings?|buildings? (and|&) roads?|irrigation|"
    r"water resources|pmgsy|highway"
)

# Anything else is kept only if its title, department or detail page mentions civil work.
DEFAULT_CIVIL_KEYWORDS = (
    r"civil|construction|building|road\b|roads\b|bridge|culvert|drain|sewer|sewage|pipeline|water supply|plaster|"
    r"flooring|repair|renovation|retaining|pavement|paving|footpath|compound wall|boundary wall|school|hospital|hostel|"
    r"market|foundation|rcc|masonry|tiles?\b|roof|waterproof|earthwork|embankment|canal|dam\b|structure|stadium|"
    r"cc road|bt road|bituminous|minor original works|\bmow\b|\bmor\b|upgrad|widening|strengthening|lining"
)

# A tender whose portal category says one of these is not civil work, even if its text mentions a civil word,
# unless the category also names civil work (e.g. "Civil Works - Electrical" would still be kept).
DEFAULT_NON_CIVIL_CATEGORY = r"electrical|mechanical|services|goods|supply|fabrication|software|hardware|furniture|medical|vehicle"
DEFAULT_CIVIL_CATEGORY = r"civil|construction|building|road|bridge|canal|water works|sewer|irrigation"

DEFAULT_EXCLUDE = r"manpower|housekeeping|security guard|vehicle hire|hiring of vehicle|stationery|software|laptop|computer"


@dataclass
class Portal:
    key: str
    name: str
    state: str
    base_url: str  # e.g. https://mahatenders.gov.in/nicgep
    org_include: str | None = None  # overrides the default for this portal


@dataclass
class Settings:
    portals: list[Portal]
    recipients: list[str]
    brevo_api_key: str
    mail_from: str
    mail_from_name: str
    state_path: str
    org_include: str = DEFAULT_ORG_INCLUDE
    strong_civil_org: str = DEFAULT_STRONG_CIVIL_ORG
    civil_keywords: str = DEFAULT_CIVIL_KEYWORDS
    exclude: str = DEFAULT_EXCLUDE
    non_civil_category: str = DEFAULT_NON_CIVIL_CATEGORY
    max_new_per_portal: int = 25  # rows shown in the email body per portal; the attached CSV always has all of them
    max_soon_per_portal: int = 10
    min_value: float = 0.0  # rupees; 0 keeps everything, tenders with no stated value are always kept
    lookback_days: int = 2  # on the first run (or for tenders never seen), ignore anything published longer ago
    closing_soon_days: int = 3
    request_delay: float = 0.6  # seconds between requests, to stay polite to government servers
    max_detail_fetches: int = 400  # per portal per run
    subject_prefix: str = "Civil tenders"
    extra: dict = field(default_factory=dict)


def _split(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;\s]+", value or "") if p.strip()]


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def load_portals(path: str | Path | None = None, only: list[str] | None = None) -> list[Portal]:
    data = json.loads(Path(path or os.environ.get("PORTALS_FILE") or DEFAULT_PORTALS).read_text())
    portals = [Portal(**p) for p in data]
    if only:
        portals = [p for p in portals if p.key in only]
    return portals


def load_settings(require_mail: bool = True) -> Settings:
    env = os.environ.get
    recipients = _split(env("RECIPIENTS", ""))
    api_key = env("BREVO_API_KEY", "")
    mail_from = env("MAIL_FROM", "")
    if require_mail:
        missing = [n for n, v in (("RECIPIENTS", recipients), ("BREVO_API_KEY", api_key), ("MAIL_FROM", mail_from)) if not v]
        if missing:
            raise SystemExit("Missing settings: " + ", ".join(missing) + ". See the README.")
    return Settings(
        portals=load_portals(only=_split(env("PORTALS", "")) or None),
        recipients=recipients,
        brevo_api_key=api_key,
        mail_from=mail_from,
        mail_from_name=env("MAIL_FROM_NAME", "Tender Digest"),
        state_path=env("STATE_PATH", "tender_state.db"),
        org_include=env("ORG_INCLUDE", DEFAULT_ORG_INCLUDE),
        strong_civil_org=env("STRONG_CIVIL_ORG", DEFAULT_STRONG_CIVIL_ORG),
        civil_keywords=env("CIVIL_KEYWORDS", DEFAULT_CIVIL_KEYWORDS),
        exclude=env("EXCLUDE_KEYWORDS", DEFAULT_EXCLUDE),
        non_civil_category=env("NON_CIVIL_CATEGORY", DEFAULT_NON_CIVIL_CATEGORY),
        max_new_per_portal=int(env("MAX_NEW_PER_PORTAL", "25")),
        max_soon_per_portal=int(env("MAX_SOON_PER_PORTAL", "10")),
        min_value=float(env("MIN_VALUE", "0") or 0),
        lookback_days=int(env("LOOKBACK_DAYS", "2")),
        closing_soon_days=int(env("CLOSING_SOON_DAYS", "3")),
        request_delay=float(env("REQUEST_DELAY", "0.6")),
        max_detail_fetches=int(env("MAX_DETAIL_FETCHES", "400")),
        subject_prefix=env("SUBJECT_PREFIX", "Civil tenders"),
    )
