"""Write-ahead logging: it is on, it can be turned off, and the things that touch the database file (a backup, a restore) are safe with it."""
import logging
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from tender_digest import backup, dbsetup
from tender_digest.backup import Backups, copy_database
from tender_digest.boq.service import BoqService
from tender_digest.store import SeenStore
from tests.test_web import tender

DAY = datetime(2026, 11, 20, 3, 0)


def mode(db) -> str:
    return str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()


def count(path: Path, sql: str = "SELECT count(*) FROM seen") -> int:
    c = sqlite3.connect(path)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def test_the_main_and_the_boq_databases_open_in_wal_mode(tmp_path):
    s = SeenStore(str(tmp_path / "tender_state.db"))
    assert s.journal_mode == "wal" and mode(s.db) == "wal" and s.db.execute("PRAGMA synchronous").fetchone()[0] == 1       # 1 = NORMAL
    boq = BoqService.open(str(tmp_path / "tender_state.db"))
    assert mode(boq.store.db) == "wal"


def test_the_setting_is_kept_in_the_file_so_a_second_opening_finds_it_already_on(tmp_path):
    path = str(tmp_path / "t.db")
    SeenStore(path).db.close()
    assert mode(sqlite3.connect(path)) == "wal"


def test_the_in_memory_test_database_is_left_alone():
    s = SeenStore(":memory:")
    assert s.journal_mode == "memory" and mode(s.db) == "memory"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_it_can_be_turned_off(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DB_WAL", value)
    s = SeenStore(str(tmp_path / "t.db"))
    assert not dbsetup.wal_wanted() and s.journal_mode == "delete"


def test_a_file_system_that_refuses_wal_only_costs_a_warning(caplog):
    class Refuses:
        def execute(self, sql):
            return type("C", (), {"fetchone": staticmethod(lambda: ("delete",))})()
    with caplog.at_level(logging.WARNING, logger="tender_digest.dbsetup"):
        assert dbsetup.tune(Refuses(), "/mnt/odd/t.db") == "delete"
    assert "did not allow write-ahead logging" in caplog.text


def test_saves_wait_in_the_wal_file_until_the_database_is_checkpointed(tmp_path):
    path = tmp_path / "t.db"
    s = SeenStore(str(path))
    for i in range(20):
        s.keep(tender(i), DAY)
    assert (tmp_path / "t.db-wal").exists() and (tmp_path / "t.db-wal").stat().st_size > 0       # the rows are in the side file, not yet in t.db itself
    assert count(path) == 20                                                                      # and another reader sees them all


def test_a_backup_taken_while_rows_are_still_in_the_wal_holds_every_row(tmp_path):
    path = tmp_path / "t.db"
    s = SeenStore(str(path))
    for i in range(20):
        s.keep(tender(i), DAY)
    copy = tmp_path / "copy.db"
    copy_database(s.db, s.lock, copy)                                                             # SQLite's own copy, which includes the WAL
    assert count(copy) == 20 and not list(tmp_path.glob("copy.db.part*"))                         # nothing half-made is left behind
    plain = tmp_path / "just-the-file.db"
    plain.write_bytes(path.read_bytes())                                                          # copying only the main file would NOT have been enough
    try:
        assert count(plain) < 20                                                                  # some rows are missing...
    except sqlite3.OperationalError as e:
        assert "no such table" in str(e)                                                          # ...or, as here, even the tables are still in the WAL


def test_restore_moves_the_old_side_files_aside_so_they_cannot_meet_the_restored_database(tmp_path):
    # a running app with rows in its WAL, and a backup of a different, smaller database
    live = tmp_path / "volume"
    s = SeenStore(str(live / "tender_state.db"))
    for i in range(30):
        s.keep(tender(i), DAY)
    boq = BoqService.open(str(live / "tender_state.db"))
    assert (live / "tender_state.db-wal").exists()

    other = tmp_path / "elsewhere"
    small = SeenStore(str(other / "tender_state.db"))
    small.keep(tender(900), DAY)
    zip_path = Backups(small, BoqService.open(str(other / "tender_state.db")).store, str(other / "tender_state.db"), None).build_zip(DAY)
    try:
        backup.restore(zip_path, live, force=True)
    finally:
        zip_path.unlink()

    left = sorted(p.name for p in live.iterdir() if "-wal" in p.name or "-shm" in p.name)
    assert not (live / "tender_state.db-wal").exists() and not (live / "tender_state.db-shm").exists()
    assert any(n.startswith("tender_state.db-wal.before-restore-") for n in left)                  # kept, not deleted
    assert count(live / "tender_state.db") == 1                                                    # the backup's data, untouched by the old WAL
    reopened = SeenStore(str(live / "tender_state.db"))
    assert reopened.count() == 1 and mode(reopened.db) == "wal"
    boq.store.db.close()
    s.db.close()


def test_a_backup_zip_still_holds_exactly_the_two_databases_and_the_vault(tmp_path):
    state = tmp_path / "tender_state.db"
    s = SeenStore(str(state))
    s.keep(tender(1), DAY)
    boq = BoqService.open(str(state))
    z = Backups(s, boq.store, str(state), None).build_zip(DAY)
    try:
        with zipfile.ZipFile(z) as f:
            assert set(f.namelist()) == {"tender_state.db", "boq.db", "manifest.json"}             # no -wal or -shm in the zip
    finally:
        z.unlink()


def test_both_databases_are_closed_and_the_wal_folded_back_when_the_app_shuts_down(tmp_path):
    from fastapi.testclient import TestClient

    from tender_digest import web
    from tender_digest.runner import RunManager
    from tests.test_web import PASSWORD, settings

    path = tmp_path / "tender_state.db"
    store, env = SeenStore(str(path)), settings()
    for i in range(10):
        store.keep(tender(i), DAY)
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    assert (tmp_path / "tender_state.db-wal").stat().st_size > 0                       # saves are waiting in the side file
    with TestClient(app) as client:                                                      # entering and leaving runs the app's start-up and shutdown
        assert client.get("/api/health").status_code == 200
    with pytest.raises(sqlite3.ProgrammingError):
        store.db.execute("SELECT 1")                                                     # closed
    assert not (tmp_path / "tender_state.db-wal").exists() or (tmp_path / "tender_state.db-wal").stat().st_size == 0   # a clean close folds the WAL into the database
    assert count(path) == 10                                                             # and nothing was lost
