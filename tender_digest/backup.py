"""Backups of everything the app keeps: the tender database, the BOQ database and the vault files.

Two kinds:
  * a daily copy of the two databases into a `backups` folder next to them (kept a week). It guards against a bad edit or a damaged
    file. It lives on the same volume, so it does NOT protect against losing the volume itself;
  * a full download (a zip with both databases and every vault file) that a person saves somewhere else. That is the real safety net.

Databases are copied with SQLite's own backup call, so a copy is consistent even while the service is working. Every copy is checked
with SQLite's integrity check before it is trusted.

Restore (run on the server, with the service stopped or the volume otherwise idle):
    python -m tender_digest.backup restore backup.zip --target-dir /data [--force]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import offsite

log = logging.getLogger("tender_digest.backup")

MAIN_NAME, BOQ_NAME, VAULT_NAME = "tender_state.db", "boq.db", "vault"
SNAPSHOT_RE = re.compile(r"^(tender_state|boq)-(\d{4}-\d{2}-\d{2})\.db$")


class BackupError(Exception):
    pass


def copy_database(src: sqlite3.Connection, lock: Any, dest: Path) -> None:
    """A consistent copy of a live database, checked before it is kept. The copy is built beside `dest` and moved into place at the end."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    try:
        with lock:
            src.backup(out)
        verdict = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        out.close()
    if verdict != "ok":
        tmp.unlink(missing_ok=True)
        raise BackupError(f"The copy of {dest.name} did not pass SQLite's integrity check: {verdict}")
    tmp.replace(dest)


class Backups:
    def __init__(self, main: Any, boq: Any, state_path: str, vault_dir: Path | None, keep_days: int = 7,
                 remote: offsite.OffsiteConfig | None = None, remote_problem: str = "", session: Any = None):
        """`main` and `boq` are the two stores (each has .db and .lock). `remote` is the off-site bucket, if one is set up."""
        self.main, self.boq, self.vault_dir, self.keep_days = main, boq, vault_dir, keep_days
        self.remote, self.remote_problem, self.session = remote, remote_problem, session
        self.enabled = state_path != ":memory:"
        self.folder = Path(state_path).expanduser().parent / "backups" if self.enabled else None
        self.busy = threading.Lock()

    # ---- the daily copy ------------------------------------------------------------------------------------------

    def daily(self, now: datetime) -> bool:
        """Makes today's copy of both databases unless it exists, and removes copies older than `keep_days`. True when a copy was made."""
        if not self.enabled or self.folder is None:
            return False
        day = now.date().isoformat()
        if (self.folder / f"tender_state-{day}.db").exists() and (self.folder / f"boq-{day}.db").exists():
            return False
        if not self.busy.acquire(blocking=False):
            return False
        try:
            copy_database(self.main.db, self.main.lock, self.folder / f"tender_state-{day}.db")
            copy_database(self.boq.db, self.boq.lock, self.folder / f"boq-{day}.db")
            self._prune(now)
            log.info("daily backup of both databases written to %s", self.folder)
            return True
        finally:
            self.busy.release()

    # ---- the copy outside Railway ----------------------------------------------------------------------------------

    RETRY_AFTER = timedelta(minutes=30)

    def housekeeping(self, now: datetime) -> None:
        """Run by the scheduler every half minute: today's local copy, then the off-site upload when one is due."""
        self.daily(now)
        self.upload_offsite(now)

    def remote_state(self) -> dict[str, Any]:
        raw = self.main.get_settings().get("offsite")
        return raw if isinstance(raw, dict) else {}

    def upload_offsite(self, now: datetime, force: bool = False) -> dict[str, Any]:
        """Sends today's database copies (and the full zip when the last one is a week old). Failures are recorded and retried, never raised."""
        state = self.remote_state()
        if self.remote is None or not self.enabled or self.folder is None:
            return state
        day = now.date().isoformat()
        if not force:
            if state.get("last_daily") == day:
                return state
            tried = state.get("tried")
            if tried and now - datetime.fromisoformat(tried) < self.RETRY_AFTER:
                return state
        state["tried"] = now.isoformat(timespec="seconds")
        try:
            self.daily(now)
            bucket = offsite.Bucket(self.remote, self.session) if self.session else offsite.Bucket(self.remote)
            offsite.send_daily(bucket, self.folder, day)
            state["last_daily"] = day
            if force or offsite.weekly_due(bucket, now):
                zip_path = self.build_zip(now)
                try:
                    offsite.send_weekly(bucket, zip_path, day)
                finally:
                    zip_path.unlink(missing_ok=True)
                state["last_weekly"] = day
            state.update(last_ok=now.isoformat(timespec="seconds"), last_error=None)
            log.info("off-site backup sent to %s", self.remote.bucket)
        except (offsite.OffsiteError, BackupError, OSError) as e:
            state["last_error"] = str(e)[:300]
            log.warning("off-site backup failed: %s", e)
        self.main.put_settings({"offsite": state})
        return state

    def _prune(self, now: datetime) -> None:
        assert self.folder is not None
        cutoff = now.date().toordinal() - self.keep_days
        for f in self.folder.iterdir():
            m = SNAPSHOT_RE.match(f.name)
            if m and datetime.strptime(m.group(2), "%Y-%m-%d").date().toordinal() <= cutoff:
                f.unlink(missing_ok=True)

    def status(self) -> dict[str, Any]:
        copies = []
        if self.folder and self.folder.is_dir():
            for f in sorted(self.folder.iterdir(), reverse=True):
                m = SNAPSHOT_RE.match(f.name)
                if m:
                    copies.append({"name": f.name, "day": m.group(2), "size": f.stat().st_size})
        days = sorted({c["day"] for c in copies}, reverse=True)
        remote = offsite.describe(self.remote, self.remote_state())
        if self.remote_problem:
            remote["last_error"] = self.remote_problem
        return {"enabled": self.enabled, "keep_days": self.keep_days, "latest_day": days[0] if days else None, "days": days, "files": len(copies), "offsite": remote}

    # ---- the full download ---------------------------------------------------------------------------------------

    def build_zip(self, now: datetime) -> Path:
        """Both databases and every vault file in one zip, in the system temp folder. The caller deletes it after sending."""
        if not self.busy.acquire(blocking=False):
            raise BackupError("A backup is already being made. Try again in a minute")
        out = Path(tempfile.mkstemp(prefix="tendai-backup-", suffix=".zip")[1])
        try:
            with tempfile.TemporaryDirectory(prefix="tendai-snap-") as tmp:
                main_copy, boq_copy = Path(tmp) / MAIN_NAME, Path(tmp) / BOQ_NAME
                copy_database(self.main.db, self.main.lock, main_copy)
                copy_database(self.boq.db, self.boq.lock, boq_copy)
                files = 0
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(main_copy, MAIN_NAME)
                    z.write(boq_copy, BOQ_NAME)
                    if self.vault_dir and self.vault_dir.is_dir():
                        for f in sorted(self.vault_dir.rglob("*")):
                            if f.is_file():
                                z.write(f, f"{VAULT_NAME}/{f.relative_to(self.vault_dir).as_posix()}")
                                files += 1
                    z.writestr("manifest.json", json.dumps({"app": "TendAI", "made": now.isoformat(timespec="seconds"), "vault_files": files, "databases": [MAIN_NAME, BOQ_NAME]}, indent=2))
            return out
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        finally:
            self.busy.release()


# ---- restore -------------------------------------------------------------------------------------------------------

def _integrity(path: Path) -> str:
    """SQLite's verdict on a database file: "ok", or what is wrong (including "not a database")."""
    try:
        c = sqlite3.connect(path)
        try:
            return str(c.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        return str(e)


def restore(zip_path: Path, target_dir: Path, force: bool = False) -> list[str]:
    """Unpacks a backup zip into `target_dir`. Refuses to overwrite existing data unless `force`, which first moves it aside."""
    if not zipfile.is_zipfile(zip_path):
        raise BackupError("That file is not a zip")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if MAIN_NAME not in names or "manifest.json" not in names:
            raise BackupError("That zip is not a TendAI backup (tender_state.db or manifest.json is missing)")
        bad = z.testzip()
        if bad:
            raise BackupError(f"The zip is damaged ({bad} cannot be read)")
        for n in names:  # nothing may land outside the target folder
            if n.startswith("/") or ".." in Path(n).parts:
                raise BackupError(f"The zip contains an unsafe path: {n}")
        for name in (MAIN_NAME, BOQ_NAME):  # check the databases INSIDE the zip first, so a bad backup never displaces good data
            if name in names:
                with tempfile.TemporaryDirectory(prefix="tendai-check-") as tmp:
                    probe = Path(tmp) / name
                    probe.write_bytes(z.read(name))
                    verdict = _integrity(probe)
                if verdict != "ok":
                    raise BackupError(f"{name} in the backup does not pass SQLite's integrity check ({verdict}). Nothing was restored")
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = [t for t in (MAIN_NAME, BOQ_NAME, VAULT_NAME) if (target_dir / t).exists()]
        if existing and not force:
            raise BackupError(f"{', '.join(existing)} already exist in {target_dir}. Add --force to move them aside and restore")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for t in existing:
            (target_dir / t).rename(target_dir / f"{t}.before-restore-{stamp}")
        for n in names:
            if n == "manifest.json" or n.endswith("/"):
                continue
            dest = target_dir / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(dest, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    return [t for t in (MAIN_NAME, BOQ_NAME, VAULT_NAME) if (target_dir / t).exists()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tender_digest.backup", description="Restore a TendAI backup zip.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore", help="unpack a backup zip into the data folder")
    r.add_argument("zip", type=Path)
    r.add_argument("--target-dir", type=Path, required=True, help="the folder that holds tender_state.db (on Railway: /data)")
    r.add_argument("--force", action="store_true", help="move existing data aside (name.before-restore-TIME) instead of refusing")
    args = ap.parse_args(argv)
    try:
        done = restore(args.zip, args.target_dir, args.force)
    except BackupError as e:
        print(f"Not restored: {e}", file=sys.stderr)
        return 1
    print(f"Restored {', '.join(done)} into {args.target_dir}. Restart the service.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
