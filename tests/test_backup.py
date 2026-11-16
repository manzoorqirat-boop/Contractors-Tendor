"""Backups: a daily copy of both databases, a full download, and a restore that gets everything back."""
import io
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tender_digest import backup, web
from tender_digest.backup import BackupError, Backups
from tender_digest.boq.service import BoqService
from tender_digest.runner import Scheduler
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, settings, tender

DAY = datetime(2026, 11, 20, 3, 0)


@pytest.fixture
def real(tmp_path, monkeypatch):
    monkeypatch.delenv("BOQ_DB_PATH", raising=False)
    monkeypatch.delenv("VAULT_DIR", raising=False)
    state = tmp_path / "data" / "tender_state.db"
    store = SeenStore(str(state))
    store.keep(tender(1), DAY)
    boq = BoqService.open(str(state))
    boq.import_catalogue("items", "erp_item_code,item_name,primary_uom,standard_base_rate\nX1,Test item,Cum,100\n")
    vault = state.parent / "vault"
    (vault / "sources").mkdir(parents=True)
    (vault / "gst.pdf").write_bytes(b"%PDF-1.4 gst certificate")
    (vault / "sources" / "abc.pdf").write_bytes(b"%PDF-1.4 notice")
    return type("R", (), {"store": store, "boq": boq, "state": state, "vault": vault, "dir": state.parent, "backups": Backups(store, boq.store, str(state), vault)})


def rows(db: Path, sql: str):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_a_daily_copy_is_made_once_a_day_and_old_ones_are_removed(real):
    b = real.backups
    assert b.daily(DAY) is True and b.daily(DAY) is False  # the second call the same day does nothing
    assert sorted(f.name for f in (real.dir / "backups").iterdir()) == ["boq-2026-11-20.db", "tender_state-2026-11-20.db"]
    assert rows(real.dir / "backups" / "tender_state-2026-11-20.db", "SELECT count(*) FROM seen")[0][0] == 1
    assert rows(real.dir / "backups" / "boq-2026-11-20.db", "SELECT count(*) FROM item_master")[0][0] == 1
    for d in range(1, 12):
        b.daily(DAY + timedelta(days=d))
    days = b.status()["days"]
    assert len(days) == 7 and days[0] == "2026-12-01" and days[-1] == "2026-11-25"  # a week is kept, the rest are gone
    assert not list((real.dir / "backups").glob("*.part"))  # no half-written files left behind


def test_nothing_is_written_for_the_in_memory_test_database():
    b = Backups(SeenStore(":memory:"), None, ":memory:", None)
    assert b.daily(DAY) is False and b.status()["enabled"] is False


def test_the_full_zip_holds_both_databases_and_every_vault_file(real):
    path = real.backups.build_zip(DAY)
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            assert {"tender_state.db", "boq.db", "manifest.json", "vault/gst.pdf", "vault/sources/abc.pdf"} == names
            assert b"gst certificate" in z.read("vault/gst.pdf")
            assert '"vault_files": 2' in z.read("manifest.json").decode()
    finally:
        path.unlink()


def test_restore_brings_everything_back_into_a_fresh_folder(real, tmp_path):
    path = real.backups.build_zip(DAY)
    target = tmp_path / "new-volume"
    done = backup.restore(path, target)
    path.unlink()
    assert done == ["tender_state.db", "boq.db", "vault"]
    assert rows(target / "tender_state.db", "SELECT count(*) FROM seen")[0][0] == 1
    assert rows(target / "boq.db", "SELECT count(*) FROM item_master")[0][0] == 1
    assert (target / "vault" / "gst.pdf").read_bytes() == b"%PDF-1.4 gst certificate"
    again = SeenStore(str(target / "tender_state.db"))  # and the app opens the restored database
    assert again.search(DAY, {"status": "all"}, "closing", 5, 0)[1] == 1


def test_restore_never_overwrites_by_accident_and_moves_data_aside_when_forced(real, tmp_path):
    path = real.backups.build_zip(DAY)
    target = tmp_path / "t"
    backup.restore(path, target)
    (target / "vault" / "extra.txt").write_text("made after the backup")
    with pytest.raises(BackupError) as e:
        backup.restore(path, target)
    assert "already exist" in str(e.value) and (target / "vault" / "extra.txt").exists()  # untouched
    backup.restore(path, target, force=True)
    path.unlink()
    aside = [p.name for p in target.iterdir() if "before-restore" in p.name]
    assert len(aside) == 3 and not (target / "vault" / "extra.txt").exists()
    assert any((target / n / "extra.txt").exists() for n in aside if n.startswith("vault"))  # the old vault is kept, not deleted


def test_restore_refuses_zips_that_are_not_backups_or_are_unsafe(tmp_path):
    def make(name, files):
        p = tmp_path / name
        with zipfile.ZipFile(p, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return p

    with pytest.raises(BackupError, match="not a zip"):
        backup.restore(tmp_path / "x.txt" if (tmp_path / "x.txt").write_text("hi") else None, tmp_path / "t1")
    with pytest.raises(BackupError, match="not a TendAI backup"):
        backup.restore(make("other.zip", {"hello.txt": "x"}), tmp_path / "t2")
    with pytest.raises(BackupError, match="unsafe path"):
        backup.restore(make("evil.zip", {"tender_state.db": "x", "manifest.json": "{}", "vault/../../escape.txt": "x"}), tmp_path / "t3")
    assert not (tmp_path / "escape.txt").exists()
    keep = tmp_path / "t4"
    keep.mkdir()
    (keep / "tender_state.db").write_text("good data on the volume")
    with pytest.raises(BackupError, match="integrity"):
        backup.restore(make("bad.zip", {"tender_state.db": "this is not a database", "manifest.json": "{}"}), keep, force=True)
    assert (keep / "tender_state.db").read_text() == "good data on the volume"  # not moved aside, not replaced


def test_the_command_line_restore_reports_in_plain_words(real, tmp_path, capsys):
    path = real.backups.build_zip(DAY)
    assert backup.main(["restore", str(path), "--target-dir", str(tmp_path / "cli")]) == 0
    assert "Restart the service" in capsys.readouterr().out
    assert backup.main(["restore", str(path), "--target-dir", str(tmp_path / "cli")]) == 1
    assert "Add --force" in capsys.readouterr().err
    path.unlink()


def make_client(real):
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), settings(state_path=str(real.state)), real.store)
    c = TestClient(app)
    return c


def test_the_download_route_needs_a_login_and_sends_a_zip(real):
    c = make_client(real)
    assert c.get("/api/backup").status_code == 401 and c.get("/api/backup/status").status_code == 401
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    r = c.get("/api/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="tendai-backup-' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.testzip() is None and "tender_state.db" in z.namelist()
    st = c.get("/api/backup/status").json()
    assert st["enabled"] is True and st["keep_days"] == 7


def test_a_backup_in_progress_makes_a_second_request_wait_politely(real):
    real.backups.busy.acquire()
    with pytest.raises(BackupError, match="already being made"):
        real.backups.build_zip(DAY)
    real.backups.busy.release()


def test_the_scheduler_makes_the_daily_copy_and_survives_a_backup_failure(real):
    from tender_digest.runner import RunManager
    from tests.test_web import FakeSender
    manager = RunManager(settings(), real.store, lambda p, s: None, FakeSender(), background=False)
    calls = []
    Scheduler(manager, housekeeping=lambda now: calls.append(now)).tick()
    assert len(calls) == 1

    def boom(now):
        raise RuntimeError("disk full")

    Scheduler(manager, housekeeping=boom).tick()  # logged, not raised
