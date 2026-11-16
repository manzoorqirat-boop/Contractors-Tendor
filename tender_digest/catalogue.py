"""Government tender portals the app can be pointed at, so adding many is a tick-list instead of typing addresses.

Most Indian state portals run the same NIC GePNIC software the built-in ones use, so one reader covers them all. The addresses below are
written from public knowledge of how those sites are named. NONE of them has been opened from this server (it has no route to them), so each one
is checked the moment it is added: a portal that works is switched on, one that refuses or has moved is kept on the "check by hand" list with the reason.
A wrong address costs one failed check and nothing else.
"""
from __future__ import annotations

import re
from typing import Any

# (name, state or body, address, group)
_GEPNIC = "Same software as the built-in portals"
_OTHER = "Other kinds of portal"
ENTRIES: list[tuple[str, str, str, str]] = [
    ("CPWD (etender.cpwd.gov.in)", "Central", "https://etender.cpwd.gov.in/nicgep", _GEPNIC),
    ("PMGSY roads (pmgsytenders.gov.in)", "Central", "https://pmgsytenders.gov.in/nicgep", _GEPNIC),
    ("Defence (defproc.gov.in)", "Central", "https://defproc.gov.in/nicgep", _GEPNIC),
    ("Coal India (coalindiatenders.nic.in)", "Central", "https://coalindiatenders.nic.in/nicgep", _GEPNIC),
    ("Kerala (etenders)", "Kerala", "https://etenders.kerala.gov.in/nicgep", _GEPNIC),
    ("Tamil Nadu (TN Tenders)", "Tamil Nadu", "https://tntenders.gov.in/nicgep", _GEPNIC),
    ("Haryana (etenders)", "Haryana", "https://etenders.hry.nic.in/nicgep", _GEPNIC),
    ("Punjab (eProcurement Punjab)", "Punjab", "https://eproc.punjab.gov.in/nicgep", _GEPNIC),
    ("Rajasthan (eProc Rajasthan)", "Rajasthan", "https://eproc.rajasthan.gov.in/nicgep", _GEPNIC),
    ("Madhya Pradesh (MPTenders)", "Madhya Pradesh", "https://mptenders.gov.in/nicgep", _GEPNIC),
    ("Odisha (Tenders Odisha)", "Odisha", "https://tendersodisha.gov.in/nicgep", _GEPNIC),
    ("West Bengal (WB Tenders)", "West Bengal", "https://wbtenders.gov.in/nicgep", _GEPNIC),
    ("Assam (Assam Tenders)", "Assam", "https://assamtenders.gov.in/nicgep", _GEPNIC),
    ("Uttarakhand (UK Tenders)", "Uttarakhand", "https://uktenders.gov.in/nicgep", _GEPNIC),
    ("Himachal Pradesh (HP Tenders)", "Himachal Pradesh", "https://hptenders.gov.in/nicgep", _GEPNIC),
    ("Jammu & Kashmir (JK Tenders)", "Jammu & Kashmir", "https://jktenders.gov.in/nicgep", _GEPNIC),
    ("Jharkhand (Jharkhand Tenders)", "Jharkhand", "https://jharkhandtenders.gov.in/nicgep", _GEPNIC),
    ("Goa (eProcure Goa)", "Goa", "https://eprocure.goa.gov.in/nicgep", _GEPNIC),
    ("Chandigarh (etenders)", "Chandigarh", "https://etenders.chd.nic.in/nicgep", _GEPNIC),
    ("Puducherry (Pudu Tenders)", "Puducherry", "https://pudutenders.gov.in/nicgep", _GEPNIC),
    ("Ladakh (Ladakh Tenders)", "Ladakh", "https://tenders.ladakh.gov.in/nicgep", _GEPNIC),
    ("Andaman & Nicobar (eProcure)", "Andaman & Nicobar", "https://eprocure.andaman.gov.in/nicgep", _GEPNIC),
    ("Meghalaya (Meghalaya Tenders)", "Meghalaya", "https://meghalayatenders.gov.in/nicgep", _GEPNIC),
    ("Nagaland (Nagaland Tenders)", "Nagaland", "https://nagalandtenders.gov.in/nicgep", _GEPNIC),
    ("Manipur (Manipur Tenders)", "Manipur", "https://manipurtenders.gov.in/nicgep", _GEPNIC),
    ("Tripura (Tripura Tenders)", "Tripura", "https://tripuratenders.gov.in/nicgep", _GEPNIC),
    ("Mizoram (Mizoram Tenders)", "Mizoram", "https://mizoramtenders.gov.in/nicgep", _GEPNIC),
    ("Arunachal Pradesh (Arunachal Tenders)", "Arunachal Pradesh", "https://arunachaltenders.gov.in/nicgep", _GEPNIC),
    ("GeM bids (experimental reader)", "Central", "https://bidplus.gem.gov.in", _OTHER),
    ("Indian Railways (IREPS)", "Railways", "https://www.ireps.gov.in", _OTHER),
    ("Gujarat (nProcure)", "Gujarat", "https://tender.nprocure.com", _OTHER),
]

OTHER_NOTES = {
    "https://bidplus.gem.gov.in": "Experimental reader, written without being able to reach GeM.",
    "https://www.ireps.gov.in": "Needs a mobile OTP: it will land on the check-by-hand list.",
    "https://tender.nprocure.com": "Different software with no reader yet, and needs .nprocure.com in PORTAL_HOST_SUFFIXES.",
}


def _same(url: str) -> str:
    return url.strip().rstrip("/").lower()


def key_of(name: str) -> str:
    """The same key the Add a portal form would make for this name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]


def listing(existing_urls: set[str]) -> list[dict[str, Any]]:
    """Every catalogue entry with whether it is already in the list. existing_urls: the addresses of the portals the app already has."""
    have = {_same(u) for u in existing_urls}
    return [{"id": key_of(n), "name": n, "state": s, "base_url": u, "group": g, "added": _same(u) in have, "note": OTHER_NOTES.get(u, "")} for n, s, u, g in ENTRIES]


def find(entry_id: str) -> dict[str, str] | None:
    for n, s, u, _g in ENTRIES:
        if key_of(n) == entry_id:
            return {"name": n, "state": s, "base_url": u}
    return None
