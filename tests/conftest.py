import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(errors="ignore")


@pytest.fixture(autouse=True)
def close_every_database(monkeypatch):
    """Each test makes its own stores and none of them closes: this closes them all when the test ends, so no connection outlives its test
    (and no "unclosed database" warning). In the running app there is one connection per database for the life of the process, closed at shutdown."""
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def tracked(*args, **kwargs):
        conn = real(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked)
    yield
    for conn in opened:
        try:
            conn.close()
        except sqlite3.Error:
            pass


@pytest.fixture(autouse=True)
def stores_in_files_when_asked(monkeypatch, tmp_path):
    """The tests make their stores in memory, which never uses the separate read connections (they need a real file in WAL mode). With
    TENDAI_TEST_FILE_DB=1 every in-memory store is made a file instead, so the whole suite runs the way the running app does:

        TENDAI_TEST_FILE_DB=1 python -m pytest -q
    """
    import itertools
    import os

    if os.environ.get("TENDAI_TEST_FILE_DB") != "1":
        yield
        return
    from tender_digest.store import SeenStore

    real_init, counter = SeenStore.__init__, itertools.count()

    def init(self, path):
        real_init(self, str(tmp_path / f"store-{next(counter)}.db") if path == ":memory:" else path)

    monkeypatch.setattr(SeenStore, "__init__", init)
    yield
