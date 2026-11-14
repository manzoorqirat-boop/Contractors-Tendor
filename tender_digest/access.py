"""Can the app read a portal on its own? If not, why, and what to do instead.

The app reads only public department lists. It never tries to get past a captcha, a login or an OTP. A portal that
needs one of those is not an error to fix: it goes on the "check manually" list, with the reason.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from .gepnic import GepnicClient, PortalError

MANUAL_STEP = "Open it in your own browser and check it by hand."

# kind -> (short label, what it means and what to do)
KINDS: dict[str, tuple[str, str]] = {
    "ok": ("Works", ""),
    "captcha": ("Captcha", f"Its tender lists ask for a captcha, which this app will not get around. {MANUAL_STEP}"),
    "login": ("Login needed", f"Its tender lists need an account. {MANUAL_STEP}"),
    "otp": ("OTP login needed", f"Its tender lists need a mobile OTP, which only you can enter. {MANUAL_STEP}"),
    "blocked": ("Refuses this server", f"The portal refuses requests from this server (often it blocks data-centre addresses). If it opens in your browser, {MANUAL_STEP[0].lower()}{MANUAL_STEP[1:]}"),
    "different_software": ("Not readable by the app", f"This is not a portal type the app can read (or the address is wrong). {MANUAL_STEP}"),
    "unreachable": ("Not reachable", "It did not answer. This is often temporary: check again later. If it keeps failing, check it by hand."),
    "unknown": ("Not checked yet", "Press Check to find out whether the app can read it."),
}
NEEDS_MANUAL = {"captcha", "login", "otp", "blocked", "different_software", "unreachable"}


def describe(kind: str) -> dict[str, Any]:
    label, advice = KINDS.get(kind, KINDS["unknown"])
    return {"kind": kind if kind in KINDS else "unknown", "label": label, "advice": advice, "manual": kind in NEEDS_MANUAL}


def is_public_host(host: str) -> bool:
    """False when the name points at this machine or a private network. A name that does not resolve is left to fail normally."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def probe(base_url: str, delay: float = 0.6, timeout: int = 20) -> dict[str, Any]:
    """One polite request for the portal's department list. Returns kind, detail, departments, tenders. Never raises."""
    host = (urlparse(base_url).hostname or "").lower()
    if not is_public_host(host):
        return {"kind": "blocked", "detail": "The address points to a private network, so it was not contacted", "departments": 0, "tenders": 0}
    try:
        orgs = GepnicClient(base_url, delay=delay, timeout=timeout).organisations(attempts=1)
    except PortalError as e:
        return {"kind": e.kind, "detail": str(e)[:300], "departments": 0, "tenders": 0}
    except Exception as e:  # noqa: BLE001 - a check must always give an answer
        return {"kind": "unreachable", "detail": f"{type(e).__name__}: {str(e)[:200]}", "departments": 0, "tenders": 0}
    return {"kind": "ok", "detail": "", "departments": len(orgs), "tenders": sum(c for _, c, _ in orgs)}
