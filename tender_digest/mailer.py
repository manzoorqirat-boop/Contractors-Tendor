"""Sends through Brevo's transactional email API. The API key comes from BREVO_API_KEY and is never logged."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("tender_digest.mailer")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class MailError(RuntimeError):
    pass


def send(
    api_key: str,
    sender_email: str,
    sender_name: str,
    recipients: list[str],
    subject: str,
    html: str,
    text: str,
    session: requests.Session | None = None,
    attachments: list[dict] | None = None,
) -> list[str]:
    """One message per recipient, so nobody sees the others' addresses. Returns the addresses that were accepted.

    Raises MailError if none were, so the run is marked failed and tomorrow's digest still includes these tenders.
    """
    http = session or requests.Session()
    accepted: list[str] = []
    failures: list[str] = []
    for address in recipients:
        try:
            response = http.post(
                BREVO_URL,
                headers={"api-key": api_key, "accept": "application/json", "content-type": "application/json"},
                json={
                    "sender": {"name": sender_name, "email": sender_email},
                    "to": [{"email": address}],
                    "subject": subject,
                    "htmlContent": html,
                    "textContent": text,
                    **({"attachment": attachments} if attachments else {}),
                },
                timeout=30,
            )
        except requests.RequestException as e:
            failures.append(f"{address}: {type(e).__name__}")
            continue
        if response.status_code in (200, 201):
            accepted.append(address)
        else:
            # Brevo's error body says why (unverified sender, IP not allowed, bad address). It never contains the key.
            failures.append(f"{address}: HTTP {response.status_code} {response.text[:200]}")
    for f in failures:
        log.error("send failed: %s", f)
    if not accepted:
        raise MailError("; ".join(failures) or "no recipients")
    return accepted
