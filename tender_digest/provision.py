"""Setting up one deployment per customer, quickly and without mistakes.

Each customer gets their own copy of the app (own service, own volume, own database), so one customer can never see another's tenders or papers.
That is the safe way to sell it today; it only needs the setup to be fast and hard to get wrong. Two commands:

  python -m tender_digest.provision new --name "Acme Infra" --url https://tend.acme.in --recipients a@acme.in,b@acme.in --mail-from tenders@acme.in
      Writes customers/acme-infra/: the environment variables to paste into the new service (strong, random passwords and keys, never reused),
      and a go-live checklist for that customer. Nothing is printed to the screen that is secret.

  TENDAI_PASSWORD=... python -m tender_digest.provision verify --url https://tend.acme.in
      Checks a running deployment from outside: reachable over https, login works, the data folder is on a volume, encryption and backups are on,
      email is set up, and how many portals are proven readable from that server. Exit status 1 if anything must be fixed before handing over.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

# No 0/O, 1/l/I: a password is read out or typed from a printout.
_PASSWORD_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_password(groups: int = 5, size: int = 4) -> str:
    """A readable password: groups of letters and digits, e.g. kT7m-Xp4Q-9wRe-HcN2-bY6d (about 115 bits)."""
    return "-".join("".join(secrets.choice(_PASSWORD_CHARS) for _ in range(size)) for _ in range(groups))


def slug_of(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if not slug:
        raise ValueError("Give the customer a name with letters or digits in it")
    return slug


def clean_emails(raw: str) -> list[str]:
    out = []
    for part in re.split(r"[,;\s]+", raw or ""):
        if not part:
            continue
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", part):
            raise ValueError(f"'{part}' does not look like an email address")
        out.append(part)
    return list(dict.fromkeys(out))


def clean_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.path or p.query or p.fragment:
        raise ValueError("The address must be the site's https address with nothing after it, for example https://tend.acme.in")
    return url


def make_secrets() -> dict[str, str]:
    """Fresh for every customer: never copy one customer's values to another."""
    return {"APP_PASSWORD": new_password(), "SECRET_KEY": secrets.token_hex(32), "DATA_ENCRYPTION_KEY": secrets.token_urlsafe(32)}


def env_file(name: str, url: str, recipients: list[str], mail_from: str, generated: dict[str, str], username: str = "admin", tz: str = "Asia/Kolkata",
             brevo_key: str = "", portals: str = "") -> str:
    lines = [
        f"# TendAI settings for {name}. These are SECRETS: paste them into the customer's own service, keep a copy in your password manager, never commit this file.",
        "# Each customer has their own values. Do not reuse this file for another customer.",
        "",
        f"APP_USERNAME={username}",
        f"APP_PASSWORD={generated['APP_PASSWORD']}",
        f"SECRET_KEY={generated['SECRET_KEY']}",
        "# Keep this key somewhere other than the server too: without it the stored company papers and a restored backup cannot be read.",
        f"DATA_ENCRYPTION_KEY={generated['DATA_ENCRYPTION_KEY']}",
        f"APP_URL={url}",
        "STATE_PATH=/data/tender_state.db",
        "COOKIE_SECURE=true",
        f"TZ_NAME={tz}",
        f"RECIPIENTS={','.join(recipients)}",
        f"MAIL_FROM={mail_from}",
        f"MAIL_FROM_NAME={name}",
        f"BREVO_API_KEY={brevo_key}",
    ]
    if portals:
        lines.append(f"PORTALS={portals}")
    lines += [
        "",
        "# Off-site backup (recommended): a private bucket in the customer's name or yours, with its own access key.",
        "# BACKUP_S3_BUCKET=",
        "# BACKUP_S3_ACCESS_KEY=",
        "# BACKUP_S3_SECRET_KEY=",
        "# BACKUP_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com",
        "# BACKUP_S3_PREFIX=" + slug_of(name) + "/",
        "",
    ]
    return "\n".join(lines)


def checklist(name: str, url: str, slug: str, has_brevo: bool) -> str:
    return f"""# Go-live checklist: {name}

Site: {url}   (one service, one volume, one database for this customer only)

## Before you start
- [ ] A new, separate service for this customer. Never put two customers in one service or one volume.
- [ ] `{slug}.env` is stored in your password manager. It is not in git, email or chat.

## Create the service
- [ ] New service from the repository; it builds the `Dockerfile`.
- [ ] Add a **Volume** mounted at `/data`. Without it a redeploy erases everything.
- [ ] Keep it at **one replica**.
- [ ] Paste the variables from `{slug}.env`.{"" if has_brevo else " Add `BREVO_API_KEY` (not set in the file) and verify the sender address in Brevo."}
- [ ] Point the customer's domain at the service; wait for https to work.

## Check it from outside
- [ ] `TENDAI_PASSWORD=<the password> python -m tender_digest.provision verify --url {url}` shows no FAIL lines.
  It checks https, login, the volume, encryption, backups, email and portal coverage.

## First setup, with the customer
- [ ] Sign in as `admin`; **Settings > Daily email**: recipients, then *Send a test email* and confirm it arrives.
- [ ] **Settings > AI & logo**: upload the customer's logo picture (PNG, JPEG or WebP under 400 KB) so their sign-in screen carries their brand; pick the company colour.
- [ ] **Settings > Portals**: tick the portals the customer bids in; press **Test all switched-on portals** (Coverage proof).
  Only the portals shown as *Proven* are covered. Tell the customer which others need checking by hand. Download the CSV for your records.
- [ ] **Runs > Run now**: the first run takes 20 to 40 minutes. Confirm the numbers look right.
- [ ] **Settings > Backup**: *Send a full backup now* once, if off-site backup is set up. Download one backup and keep it.
- [ ] Create each person's own account (**Settings > People**); the shared admin password is for you.
- [ ] Optional: owner two-step sign-in (`OWNER_TOTP_SECRET`).

## Hand over
- [ ] Send the **password in a different channel** from the address.
- [ ] Tell the customer: what the app can and cannot read (the coverage CSV), that portals can change without notice (the Dashboard warns when one stops working), and how to reach you.
- [ ] Put the customer, address, date and who set it up in your own records.

## Every month
- [ ] Open Settings > Portals: any portal marked *Stopped working* or *Finding nothing*?
- [ ] Settings > Backup: latest day is recent and the off-site copy succeeded.
- [ ] Check the app's repository for updates and redeploy.
"""


def write_kit(out_root: Path, name: str, url: str, recipients: list[str], mail_from: str, username: str = "admin", tz: str = "Asia/Kolkata",
              brevo_key: str = "", portals: str = "", force: bool = False) -> dict[str, Path]:
    slug = slug_of(name)
    folder = out_root / slug
    if folder.exists() and any(folder.iterdir()) and not force:
        raise FileExistsError(f"{folder} already exists. Passwords are generated once: use --force only if you really want new ones (the live service would need them too)")
    folder.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)
    guard = out_root / ".gitignore"
    if not guard.exists():
        guard.write_text("# Customer secrets: never commit.\n*\n", encoding="utf-8")
    env_path, list_path = folder / f"{slug}.env", folder / f"{slug}-go-live.md"
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # readable by you alone from the first byte
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(env_file(name, url, recipients, mail_from, make_secrets(), username, tz, brevo_key, portals))
    os.chmod(env_path, 0o600)
    list_path.write_text(checklist(name, url, slug, bool(brevo_key)), encoding="utf-8")
    return {"folder": folder, "env": env_path, "checklist": list_path}


# ---- verify a running deployment ---------------------------------------------------------------------------------------

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def verify(url: str, password: str, username: str = "admin", session: Any = None, timeout: int = 20) -> list[tuple[str, str, str]]:
    """Checks a deployment from outside. Returns [(PASS|WARN|FAIL, what, detail)]. Reads only; changes nothing on the site."""
    http = session or requests.Session()
    results: list[tuple[str, str, str]] = []

    def add(status: str, what: str, detail: str = "") -> None:
        results.append((status, what, detail))

    def get(path: str) -> Any:
        return http.get(url + path, timeout=timeout)

    if not url.startswith("https://"):
        add(FAIL, "Served over https", "The address must start with https:// (passwords and tender data travel over it)")
    else:
        add(PASS, "Served over https")
    try:
        r = get("/api/health")
    except requests.RequestException as e:
        add(FAIL, "The site answers", f"{type(e).__name__}: {str(e)[:160]}")
        return results
    if r.status_code != 200:
        add(FAIL, "The site answers", f"Health check returned {r.status_code}: the database cannot be read or its folder is not writable. Is the /data volume mounted?")
        return results
    add(PASS, "The site answers")
    if url.startswith("https://"):
        add(PASS if "strict-transport-security" in {k.lower() for k in r.headers} else WARN, "Browsers are told to always use https (HSTS)")

    try:
        login = http.post(url + "/api/login", json={"username": username, "password": password}, timeout=timeout)
    except requests.RequestException as e:
        add(FAIL, "Sign in works", f"{type(e).__name__}: {str(e)[:160]}")
        return results
    if login.status_code != 200:
        add(FAIL, "Sign in works", "Wrong username or password, or the owner two-step code is on (this check cannot enter it)" if login.status_code == 401 else f"Returned {login.status_code}")
        return results
    add(PASS, "Sign in works")

    def read(path: str) -> dict[str, Any] | None:
        try:
            resp = get(path)
            return resp.json() if resp.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None

    backup = read("/api/backup/status")
    if backup is None:
        add(WARN, "Backups and encryption", "Could not read the backup status (the account may not be an administrator)")
    else:
        warning = backup.get("storage_warning")
        add(FAIL if warning else PASS, "Data is kept on a volume", str(warning)[:200] if warning else "")
        add(PASS if backup.get("latest_day") else WARN, "A daily backup copy exists", "" if backup.get("latest_day") else "None yet: it is made the first night, so check again tomorrow")
        enc = backup.get("encryption") or {}
        if not enc.get("enabled"):
            add(FAIL, "Stored papers are encrypted", "DATA_ENCRYPTION_KEY is not set")
        elif enc.get("problem"):
            add(FAIL, "Stored papers are encrypted", str(enc["problem"])[:200])
        elif enc.get("plain_files") or enc.get("plain_secrets"):
            add(WARN, "Stored papers are encrypted", f"{enc.get('plain_files', 0)} file(s) and {enc.get('plain_secrets', 0)} secret(s) are still plain; they are sealed at the next restart")
        else:
            add(PASS, "Stored papers are encrypted")
        off = backup.get("offsite") or {}
        if not off.get("configured"):
            add(WARN, "Off-site backup", "Not set up: losing the volume would lose everything")
        else:
            add(PASS if off.get("last_ok") and not off.get("last_error") else WARN, "Off-site backup", str(off.get("last_error") or "") if off.get("last_error") else ("" if off.get("last_ok") else "Set up, but no upload has succeeded yet"))

    setup = read("/api/setup")
    if setup is not None:
        mail = next((s for s in setup.get("steps", []) if s.get("key") == "email"), None)
        if mail is not None:
            add(PASS if mail.get("done") else FAIL, "Daily email is set up (sender and recipients)", "" if mail.get("done") else "Set the recipients in Settings and BREVO_API_KEY / MAIL_FROM on the service")

    cov = read("/api/portals/coverage")
    if cov is not None:
        s = cov.get("summary", {})
        on = [r for r in cov.get("rows", []) if r.get("enabled")]
        proven_on = [r for r in on if r.get("verdict") == "proven"]
        if not on:
            add(FAIL, "Portals are switched on and proven readable", "No portal is switched on")
        elif not proven_on:
            add(FAIL, "Portals are switched on and proven readable", "None of the switched-on portals is proven readable from this server yet: Settings > Portals > Test all switched-on portals")
        elif len(proven_on) < len(on):
            names = ", ".join(r["name"] for r in on if r.get("verdict") != "proven")
            add(WARN, "Portals are switched on and proven readable", f"{len(proven_on)} of {len(on)} proven. Not proven: {names[:200]}")
        else:
            add(PASS, "Portals are switched on and proven readable", f"{len(proven_on)} of {len(on)} ({s.get('portals', 0)} in the list)")
    brand = read("/api/branding")
    if brand is not None:
        add(PASS if brand.get("logo_url") else WARN, "The customer's logo picture is uploaded", "" if brand.get("logo_url") else "Settings > AI & logo > Logo picture: the sign-in screen shows the letters until then")
    health = read("/api/portal-health")
    if health is not None:
        bad = health.get("problems") or []
        add(WARN if bad else PASS, "No portal has stopped working", ", ".join(p["name"] for p in bad)[:200])
    return results


def format_results(results: list[tuple[str, str, str]]) -> str:
    lines = [f"{status:<5} {what}" + (f": {detail}" if detail else "") for status, what, detail in results]
    fails, warns = sum(1 for r in results if r[0] == FAIL), sum(1 for r in results if r[0] == WARN)
    lines.append("")
    lines.append("Ready to hand over." if not fails and not warns else f"{fails} to fix before handing over, {warns} to look at." if fails else f"No blockers; {warns} to look at.")
    return "\n".join(lines)


# ---- command line ------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None, out: Any = None, session: Any = None) -> int:
    say = out or (lambda text: print(text, flush=True))
    parser = argparse.ArgumentParser(prog="python -m tender_digest.provision", description="One deployment per customer: generate the setup, then check it")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="write a customer's settings file and go-live checklist")
    new.add_argument("--name", required=True, help="the customer's name")
    new.add_argument("--url", required=True, help="the site's https address, e.g. https://tend.acme.in")
    new.add_argument("--recipients", required=True, help="who gets the daily email (commas)")
    new.add_argument("--mail-from", required=True, help="the sender address, verified in Brevo")
    new.add_argument("--username", default="admin")
    new.add_argument("--timezone", default="Asia/Kolkata")
    new.add_argument("--portals", default="", help="portal keys to switch on at the start (default: the built-in four)")
    new.add_argument("--brevo-key-env", metavar="NAME", help="read the Brevo key from this environment variable (never typed on the command line)")
    new.add_argument("--out", default="customers", help="folder for customer kits (default ./customers, which is kept out of git)")
    new.add_argument("--force", action="store_true", help="replace an existing kit (generates new passwords)")
    chk = sub.add_parser("verify", help="check a running deployment from outside")
    chk.add_argument("--url", required=True)
    chk.add_argument("--username", default="admin")
    chk.add_argument("--password-env", default="TENDAI_PASSWORD", metavar="NAME", help="environment variable holding the admin password (default TENDAI_PASSWORD)")
    args = parser.parse_args(argv)

    if args.command == "new":
        try:
            recipients = clean_emails(args.recipients)
            if not recipients:
                raise ValueError("Give at least one recipient")
            (mail_from,) = clean_emails(args.mail_from) or [""]
            if not mail_from:
                raise ValueError("Give the sender address")
            paths = write_kit(Path(args.out), args.name.strip(), clean_url(args.url), recipients, mail_from, args.username.strip() or "admin", args.timezone,
                              os.environ.get(args.brevo_key_env, "") if args.brevo_key_env else "", args.portals.strip(), args.force)
        except (ValueError, FileExistsError) as e:
            say(f"Not created: {e}")
            return 2
        say(f"Written to {paths['folder']}/")
        say(f"  {paths['env'].name}        the variables for the customer's service (secret; readable by you only)")
        say(f"  {paths['checklist'].name}  the go-live checklist")
        say("Follow the checklist. The passwords are in the .env file; they were not printed here.")
        return 0

    password = os.environ.get(args.password_env, "")
    if not password:
        say(f"Set the admin password in the {args.password_env} environment variable first (it is not taken on the command line, so it stays out of shell history).")
        return 2
    try:
        url = clean_url(args.url) if args.url.startswith("https://") else args.url.rstrip("/")
    except ValueError as e:
        say(str(e))
        return 2
    results = verify(url, password, args.username, session)
    say(format_results(results))
    return 1 if any(r[0] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
