"""How the app's SQLite files are opened.

Write-ahead logging (WAL) with synchronous=NORMAL makes saving much faster than SQLite's default (about 8 times on 1,500 tenders saved one at a
time, more on a network disk), because a save no longer waits for the disk twice. What it changes:

  * Two small side files, `name.db-wal` and `name.db-shm`, sit beside the database while the app runs. They are part of the database: copying or
    replacing only `name.db` while they exist is unsafe. The backup uses SQLite's own copy (safe), and `backup.restore` moves the side files aside.
  * If the whole machine loses power the last few saves can be lost, but the database is never corrupted. An app crash loses nothing.

Set DB_WAL=0 to go back to SQLite's default (for example on a network file system that cannot do WAL).
"""
from __future__ import annotations

import logging
import os
import sqlite3

log = logging.getLogger("tender_digest.dbsetup")

SIDE_FILES = ("-wal", "-shm")   # what WAL keeps next to the database file


def wal_wanted() -> bool:
    return os.environ.get("DB_WAL", "1").strip().lower() not in ("0", "false", "no", "off")


def tune(db: sqlite3.Connection, path: str) -> str:
    """Puts a freshly opened connection into WAL mode where that is wanted and possible. Returns the journal mode now in use."""
    if path == ":memory:":
        return "memory"
    if not wal_wanted():
        return str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    mode = str(db.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
    if mode == "wal":
        db.execute("PRAGMA synchronous=NORMAL")
    else:  # some file systems refuse; the app works the same, only slower
        log.warning("%s: this file system did not allow write-ahead logging (got %r), so saves will be slower", os.path.basename(path), mode)
    return mode
