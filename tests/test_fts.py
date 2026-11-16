"""The full-text index: it must answer exactly like the row-by-row search it replaces, stay in step with the table, rank by relevance, and be safe."""
import random
import time
from datetime import datetime, timedelta

import pytest

from tender_digest import store as store_module
from tender_digest.gepnic import Tender
from tender_digest.store import SeenStore

NOW = datetime(2026, 11, 16, 10, 0)

TITLES = [
    "Construction of roads at Nashik", "Improvement of nala and storm water drain at Satpur", "Construction of minor bridge on river Godavari",
    "Repairs to school building at Igatpuri", "Standard drawings printing", "Renovation of hospital at Pune", "RCC road with paver blocks, Kothrud",
    "Supply and laying of 300 mm DI pipeline for water supply", "Construction of compound wall and fencing at Trimbak", "BT resurfacing of Peth road km 4 to 9",
    "Providing paver block flooring and kerb stone", "Minor irrigation check dam at Dindori", "Strengthening of culvert on SH 22", "Staff quarters G+3 with internal roads",
    "R&M of PHC building Sinnar", "Desilting of nullah, Ward 3", "Earthwork in embankment for canal lining", "Boundary wall to Anganwadi", "Hostel building repairs",
]
ORGS = ["PWD||Nashik Division", "Municipal||Nashik", "ZP||Works", "Water Resources||Dindori", "Health||Pune", "PMC||Pune"]

QUERIES = [
    "road", "roads", "road nashik", "bridge pune", "drain", "sewer", "nala", "drainage", "culvert", "flyover", "repair", "repairs", "renovation", "concrete", "rcc", "cc",
    '"storm water"', '"storm water" drain', "road -paver", "drain -paver", "repair -school", "compound wall", "boundary wall", "fencing", "water supply", "pipeline",
    "rd", "r&m", "R&M works", "building", "nashik", "t3", "T10", "2026_PWD", "hostel", "irrigation", "canal", "staff quarters", "pune hospital", "nonsense", "road road",
    "ROAD", "Roads at", "kerb", "school", "SH 22", "km 4", "g+3", "anganwadi", "desilting", "dindori check dam",
]


def tender(n, title, org):
    return Tender(portal="maha", tender_id=f"T{n}", title=title, reference="", description=title + " work", organisation=org, category="Works", location="Nashik",
                  value=1e6 * (n + 1), emd=None, published=NOW, closing=NOW + timedelta(days=5 + n % 20), opening=None)


@pytest.fixture
def store():
    s = SeenStore(":memory:")
    for i, title in enumerate(TITLES * 2):                       # 38 tenders
        s.keep(tender(i, title, ORGS[i % len(ORGS)]), NOW)
    return s


def ids(store, q, sort="closing"):
    return sorted(r["tender_id"] for r in store.search(NOW, {"q": q, "status": "all"}, sort, 200, 0)[0])


def test_the_index_exists_and_is_in_use():
    assert store_module.FTS_OK
    s = SeenStore(":memory:")
    assert s.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'seen_fts'").fetchone()
    assert s._fts_expr("road nashik") and s._fts_expr("-paver") is None and s._fts_expr("") is None


@pytest.mark.parametrize("q", QUERIES)
def test_the_index_answers_exactly_like_the_row_by_row_search(store, monkeypatch, q):
    with_index = ids(store, q)
    monkeypatch.setattr(store_module, "FTS_OK", False)
    assert ids(store, q) == with_index, q


def test_a_query_with_nothing_but_exclusions_still_works(store):
    got = ids(store, "-paver")
    assert got and not any("paver" in t.lower() for t in [r["title"] for r in store.search(NOW, {"q": "-paver", "status": "all"}, "closing", 200, 0)[0]])


def test_the_index_follows_inserts_updates_and_deletes():
    s = SeenStore(":memory:")
    t = tender(1, "Construction of bridge at Pune", "PWD||X")
    s.keep(t, NOW)
    assert ids(s, "bridge") == ["T1"]
    t.title = t.description = "Repair of school at Pune"                    # a collected tender is corrected on the next run
    s.keep(t, NOW)
    assert ids(s, "bridge") == [] and ids(s, "school") == ["T1"]
    s.db.execute("UPDATE seen SET fit = 4 WHERE key = ?", (t.key,))         # an update that does not touch the words must not disturb the index
    s.db.commit()
    assert ids(s, "school") == ["T1"]
    s.delete_tender(t.key)
    assert ids(s, "school") == []
    s.skip("maha:placeholder", NOW)                                          # a placeholder with no text at all
    assert ids(s, "placeholder") == [] and s.db.execute("SELECT COUNT(*) FROM seen_fts_docsize").fetchone()[0] == s.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def test_an_index_that_drifted_is_rebuilt_when_the_store_opens(tmp_path):
    path = str(tmp_path / "t.db")
    s = SeenStore(path)
    for i, title in enumerate(TITLES[:5]):
        s.keep(tender(i, title, ORGS[0]), NOW)
    s.db.execute("DROP TRIGGER seen_fts_ai")                                 # simulate rows added by something that did not maintain the index
    s.keep(tender(50, "Dredging of pond at Sinnar", ORGS[0]), NOW)
    assert ids(s, "dredging") == []
    s.db.close()
    again = SeenStore(path)
    assert ids(again, "dredging") == ["T50"]                                # opening it noticed and rebuilt


def test_a_database_made_before_the_index_gets_one(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    s = SeenStore(path)
    s.keep(tender(1, "Construction of road", ORGS[0]), NOW)
    s.db.executescript("DROP TRIGGER seen_fts_ai; DROP TRIGGER seen_fts_ad; DROP TRIGGER seen_fts_au; DROP TABLE seen_fts;")
    s.db.close()
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'seen_fts%'").fetchone()[0] == 0
    assert ids(SeenStore(path), "road") == ["T1"]


@pytest.mark.parametrize("q", ['"', '"""', "road AND", "road OR OR", "NEAR(road nashik, 2)", "title:road", "road*", "(road", "road)", "^road", "a b c d e f g h i j k l", "road NOT", "'; DROP TABLE seen; --", "\\", "*", "{road}", "road:", "-", "- -"])
def test_nothing_a_person_types_can_be_read_as_search_syntax(store, q):
    ids(store, q)                                                            # must not raise
    assert store.count() == 38 and store.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'seen'").fetchone()


def test_best_text_match_puts_the_title_first(store):
    s = SeenStore(":memory:")
    for i in range(20):                                                      # a word in every tender tells nothing: relevance needs tenders without it
        s.keep(tender(100 + i, f"Supply of furniture item {i}", "ZP||Stores"), NOW)
    s.keep(tender(1, "Annual maintenance of equipment", "Roads Department||Division"), NOW)   # "roads" only in the department
    s.keep(tender(2, "Construction of road at Nashik", "ZP||Works"), NOW)
    s.keep(tender(3, "Road widening and road resurfacing, road safety", "ZP||Works"), NOW)  # the word again and again, in the title
    order = [r["tender_id"] for r in s.search(NOW, {"q": "road", "status": "all"}, "relevance", 10, 0)[0]]
    assert order[0] in ("T3", "T2") and order[-1] == "T1"
    # no search text: "best match" falls back to the recommended order instead of failing
    assert len(s.search(NOW, {"status": "all"}, "relevance", 50, 0)[0]) == 23
    # only "leave this out" words cannot be ranked; it still answers
    assert len(s.search(NOW, {"q": "-widening", "status": "all"}, "relevance", 50, 0)[0]) == 22


def test_it_is_much_faster_than_reading_every_row():
    random.seed(7)
    words = ["road", "bridge", "drain", "building", "school", "hospital", "canal", "pipeline", "culvert", "wall", "hostel", "market", "repair", "paving"]
    places = ["Nashik", "Pune", "Satara", "Nagpur", "Thane", "Latur", "Akola", "Dhule", "Beed", "Jalna"]
    s = SeenStore(":memory:")
    with s.lock:
        for i in range(30000):
            title = f"{random.choice(['Construction of', 'Repair of', 'Improvement to', 'Providing'])} {random.choice(words)} {random.choice(words)} at {random.choice(places)} ward {i}"
            s.db.execute("INSERT INTO seen (key, first_seen, civil, closing, portal, tender_id, title, description, organisation, category, location) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (f"maha:{i}", NOW.isoformat(), 1, (NOW + timedelta(days=10)).isoformat(), "maha", f"T{i}", title, title, "PWD||X", "Works", random.choice(places)))
        s.db.commit()
    q = {"q": "bridge nashik", "status": "all"}
    t0 = time.perf_counter()
    fast = s.search(NOW, q, "closing", 25, 0)
    with_index = time.perf_counter() - t0
    store_module.FTS_OK = False
    try:
        t0 = time.perf_counter()
        slow = s.search(NOW, q, "closing", 25, 0)
        row_by_row = time.perf_counter() - t0
    finally:
        store_module.FTS_OK = True
    assert fast[1] == slow[1] and fast[1] > 0
    print(f"\n30,000 tenders, 'bridge nashik': index {with_index * 1000:.0f} ms, row by row {row_by_row * 1000:.0f} ms")
    assert with_index < row_by_row
