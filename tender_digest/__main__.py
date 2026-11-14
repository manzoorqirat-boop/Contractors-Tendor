"""python -m tender_digest [--dry-run] [--preview FILE] [--portals a,b] [--send-test]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import collector, digest, mailer
from .config import load_settings
from .store import SeenStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily civil tender digest")
    parser.add_argument("--dry-run", action="store_true", help="read the portals and build the email, but send nothing and mark nothing as sent")
    parser.add_argument("--preview", metavar="FILE", help="write the email's HTML to this file")
    parser.add_argument("--portals", help="comma-separated portal keys to read (default: all)")
    parser.add_argument("--send-test", action="store_true", help="send a short test email to the recipients and stop")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("tender_digest")
    if args.portals:
        os.environ["PORTALS"] = args.portals
    settings = load_settings(require_mail=not (args.dry_run or args.preview) or args.send_test)
    now = datetime.now(ZoneInfo(os.environ.get("TZ_NAME", "Asia/Kolkata"))).replace(tzinfo=None)

    if args.send_test:
        accepted = mailer.send(
            settings.brevo_api_key, settings.mail_from, settings.mail_from_name, settings.recipients,
            "Tender digest: test email", "<p>This is a test. The daily digest is set up for this address.</p>",
            "This is a test. The daily digest is set up for this address.",
        )
        log.info("test email accepted for %d recipient(s)", len(accepted))
        return 0

    store = SeenStore(settings.state_path)
    try:
        reports = collector.collect(settings, store, now)
        pending = store.pending(now)
        soon = store.closing_soon(now, settings.closing_soon_days)
        built = digest.build(now, pending, soon, reports, settings.subject_prefix, settings.max_new_per_portal, settings.max_soon_per_portal)
        log.info("%s", built.subject)
        for r in reports:
            log.info("%s: %d departments read, %d listed, %d new civil, %d problems", r.portal.key, r.organisations_read, r.listed, r.new_civil, len(r.errors))

        if args.preview:
            with open(args.preview, "w", encoding="utf-8") as f:
                f.write(built.html)
            log.info("preview written to %s", args.preview)
        if args.dry_run:
            return 0

        if not pending and not soon and not any(r.errors for r in reports) and os.environ.get("SEND_EMPTY", "true").lower() not in ("1", "true", "yes"):
            log.info("nothing to report and SEND_EMPTY is off; no email sent")
            return 0

        mailer.send(settings.brevo_api_key, settings.mail_from, settings.mail_from_name, settings.recipients, built.subject, built.html, built.text,
                    attachments=[{"name": built.csv_name, "content": built.csv_base64}] if built.csv_base64 else None)
        store.mark_emailed([t.key for t in pending])
        store.prune(now)
        if all(r.failed for r in reports):
            log.error("every portal failed; the email said so, but check the logs")
            return 1
        return 0
    except mailer.MailError as e:
        log.error("email could not be sent: %s", e)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
