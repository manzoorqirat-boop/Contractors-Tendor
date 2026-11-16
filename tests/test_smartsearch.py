"""The tender list's search: every word, synonyms, phrases, exclusions, amounts and spelling help. No AI involved."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import smartsearch as ss, web
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, login, settings

NOW = datetime(2026, 11, 15, 10, 0)


def tender(n, title, org="PWD||Division", location="Nashik", value=2e7, category="Works"):
    return Tender(portal="maha", tender_id=f"T{n}", title=title, reference="", description=title, organisation=org, category=category, location=location, value=value,
                  emd=value / 50, published=NOW, closing=NOW + timedelta(days=10), opening=NOW + timedelta(days=12))


@pytest.fixture
def store():
    s = SeenStore(":memory:")
    for i, args in enumerate([
        ("Construction of roads at Nashik", "PWD||Nashik", "Nashik", 1.2e7),
        ("Improvement of nala and storm water drain at Satpur", "Municipal||Nashik", "Nashik", 4.5e7),
        ("Construction of minor bridge on river Godavari", "PWD||Pune", "Pune", 3.2e8),
        ("Repairs to school building at Igatpuri", "ZP||Nashik", "Igatpuri", 6e6),
        ("Standard drawings printing", "PWD||Nashik", "Nashik", 5e5),
        ("Renovation of hospital at Pune", "Health||Pune", "Pune", 2.5e7),
        ("RCC road with paver blocks, Kothrud", "PMC||Pune", "Pune", 9e6),
    ]):
        s.keep(tender(i, *args[:1], org=args[1], location=args[2], value=args[3]), NOW)
    return s


def find(store, q, **extra):
    rows, total = store.search(NOW, {"q": q, "status": "all", **extra}, "closing", 50, 0)
    return sorted(r["title"].split()[0] + " " + r["title"].split()[-1] for r in rows)


def titles(store, q, **extra):
    return {r["title"] for r in store.search(NOW, {"q": q, "status": "all", **extra}, "closing", 50, 0)[0]}


def test_every_word_must_be_found_in_any_field(store):
    assert len(titles(store, "road nashik")) == 1                                   # the road, in Nashik
    assert len(titles(store, "bridge pune")) == 1 and not titles(store, "bridge nashik")
    assert len(titles(store, "PWD pune")) == 1                                      # department and place count too
    assert len(titles(store, "t3")) == 1                                            # so does the tender id


def test_words_match_from_the_start_and_plurals_do_not_matter(store):
    assert len(titles(store, "roads")) == len(titles(store, "road")) == 2
    assert titles(store, "repair") == titles(store, "repairs") and len(titles(store, "repair")) == 2     # repair also means renovation
    assert "Standard drawings printing" not in titles(store, "rd")                  # "rd" is a word of its own, not part of "standard"


def test_words_that_mean_the_same_are_found_together(store):
    assert titles(store, "drain") == {"Improvement of nala and storm water drain at Satpur"}
    assert titles(store, "sewer") == titles(store, "nala") == titles(store, "drainage")
    assert len(titles(store, "culvert")) == 1 and "Construction of minor bridge on river Godavari" in titles(store, "flyover")
    assert len(titles(store, "renovation")) == 2                                    # the school repairs and the hospital renovation


def test_phrases_and_exclusions(store):
    assert titles(store, '"storm water"') == {"Improvement of nala and storm water drain at Satpur"}
    assert not titles(store, '"water storm"')
    assert titles(store, "road -paver") == {"Construction of roads at Nashik"}
    assert len(titles(store, "-nashik")) == 3


def test_amounts_become_value_limits(store):
    assert titles(store, "construction above 1 crore") == {"Construction of minor bridge on river Godavari", "Construction of roads at Nashik"}
    assert titles(store, "construction above 2 crore") == {"Construction of minor bridge on river Godavari"}
    assert {t.split()[0] for t in titles(store, "under 70 lakh")} == {"Repairs", "Standard"}
    assert titles(store, "bridge over 5 crore") == {"Construction of minor bridge on river Godavari"}
    assert titles(store, "above 3 crore below 5 crore") == {"Improvement of nala and storm water drain at Satpur"}
    assert titles(store, "above Rs 5,00,00,000") == {"Construction of minor bridge on river Godavari"}
    assert len(titles(store, "under 500000")) == 1                                  # a plain number is rupees


def test_a_search_of_filler_or_symbols_is_not_everything_and_not_an_error(store):
    for q in ("tenders for the", "%", "_", "' OR 1=1 --", "\\", "(((", '"'):
        assert len(titles(store, q)) <= 7
    assert titles(store, "%") == set() and titles(store, "_") == set()


def test_parse_describes_the_search_in_words():
    p = ss.parse('road "storm water" -paver above 2 crore under 50 lakh')
    assert p.min_value == 2e7 and p.max_value == 5e6 and p.phrases == ["storm water"] and p.excluded == ["paver"]
    text = p.describe()
    assert "road (or" in text and "“storm water”" in text and "leaving out paver" in text and "over ₹2 crore" in text and "under ₹50 lakh" in text
    assert ss.parse("the tenders for").has_text is False and ss.parse("").describe() == "Searching"


def test_spelling_help_offers_a_close_word_from_the_collected_tenders(store):
    vocab = store.vocabulary()
    assert "drain" in vocab and "godavari" in vocab
    assert ss.suggest("godavri bridge", vocab) == "godavari bridge"
    assert ss.suggest("bridge", vocab) is None and ss.suggest("xyzxyz", vocab) is None and ss.suggest("the", vocab) is None


def test_the_api_explains_and_suggests(monkeypatch):
    store = SeenStore(":memory:")
    for i in range(3):
        store.keep(tender(i, "Drainage works at Satpur"), NOW)
    env = settings()
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, lambda p, s: None, FakeSender(), background=False))
    c = TestClient(app)
    login(type("X", (), {"client": c}))
    ok = c.get("/api/tenders", params={"q": "sewer", "status": "all"}).json()
    assert ok["total"] == 3 and "sewer (or" in ok["understood"] and "suggest" not in ok
    miss = c.get("/api/tenders", params={"q": "drainge", "status": "all"}).json()
    assert miss["total"] == 0 and miss["suggest"] == "drainage"
    assert "understood" not in c.get("/api/tenders", params={"status": "all"}).json()
    assert c.get("/api/tenders", params={"q": "x" * 101}).status_code == 422
