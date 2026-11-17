"""The recommended order: its parts, what it learns from stars, hides, bids and hidden departments, and that every part can be read and switched off."""
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import learn, web
from tender_digest.bids import BidBook
from tender_digest.gepnic import Tender
from tender_digest.learn import Model, features, priority
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, settings

NOW = datetime(2026, 11, 16, 10, 0)


def row(**kw):
    base = dict(portal="maha", organisation="PWD||Nashik Division", category="Works", location="Nashik", work_type="Roads", title="Widening of bridge approach road", description="", fit=None, value=2e7,
                closing=(NOW + timedelta(days=7)).isoformat())
    base.update(kw)
    return base


def tender(n, dept="PWD", title="Widening of bridge approach", days=7, value=2e7, category="Works", location="Nashik"):
    return Tender(portal="maha", tender_id=f"T{n}", title=title, reference="", description=title, organisation=f"{dept}||Division", category=category, location=location,
                  value=value, emd=None, published=NOW, closing=NOW + timedelta(days=days), opening=None)


# ---- features and the model ---------------------------------------------------------------------------------------

def test_what_a_tender_has_that_can_be_liked():
    f = features(row())
    assert ("dept", "PWD") in f and ("category", "works") in f and ("work_type", "roads") in f and ("location", "nashik") in f and ("portal", "maha") in f
    words = [v for k, v in f if k == "word"]
    assert "widening" in words and "bridge" in words and "approach" in words and "road" not in words      # filler words in every title are left out
    assert features({}) == [] and len([1 for k, _ in features(row(title="alpha beta gamma delta epsilon zeta eta theta iota kappa lambda")) if k == "word"]) <= 8


def test_nothing_is_learned_from_a_handful_of_clicks():
    m = Model()
    for _ in range(learn.MIN_SIGNALS - 1):
        m.add(row(), +1)
    assert not m.ready and m.score(row()) == (0.0, [])
    m.add(row(), +1)
    assert m.ready and m.score(row())[0] > 0


def test_one_stray_choice_does_not_decide_much_but_a_pattern_does():
    m = Model()
    m.add(row(organisation="Water||X"), -1)
    assert abs(m.strength(("dept", "Water"))) == pytest.approx(0.25)                  # 1 / (1 + 3)
    for _ in range(12):
        m.add(row(organisation="Water||X"), -1)
    assert m.strength(("dept", "Water")) < -0.75 and m.strength(("dept", "Never seen")) == 0


def test_liked_and_avoided_things_push_a_tender_up_or_down():
    m = Model()
    for _ in range(8):
        m.add(row(organisation="PWD||A", title="Bridge repair works"), +1)
        m.add(row(organisation="Housing||B", title="Staff quarters painting", category="Buildings", work_type="Buildings"), -1)
    up, why_up = m.score(row(organisation="PWD||C", title="Bridge deck"))
    down, why_down = m.score(row(organisation="Housing||C", title="Painting of quarters", category="Buildings", work_type="Buildings"))
    assert up > 0.5 and down < -0.5 and why_up[0][0] == "dept" and why_down[0][0] == "dept"
    assert m.score(row(organisation="Other||C", title="Unrelated thing", category="X", work_type="Y", location="Z", portal="q"))[0] == 0     # nothing known about it


def test_a_hidden_department_counts_strongly():
    m = Model()
    m.add_department("Irrigation", -1)
    for _ in range(learn.MIN_SIGNALS):
        m.add(row(), +1)
    other = row(organisation="Irrigation||Q", category="Canals", work_type="Canals", location="Satara", portal="gem", title="Lining of distributary")
    assert m.strength(("dept", "Irrigation")) == pytest.approx(-0.5) and m.score(other)[0] < 0
    assert m.score(row(organisation="Irrigation||Q"))[0] > m.score(other)[0] - 1                  # the likes it shares with the liked tenders still count


def test_the_strongest_choices_are_listed_for_checking():
    m = Model()
    for _ in range(10):
        m.add(row(organisation="PWD||A"), +1)
        m.add(row(organisation="Housing||B", title="Painting"), -1)
    top = m.top()
    liked = {x["value"]: x for x in top["liked"]}
    avoided = {x["value"]: x for x in top["avoided"]}
    assert liked["PWD"]["chosen"] == 10 and liked["PWD"]["kind"] == "department" and avoided["Housing"]["avoided"] == 10      # equal strengths have no fixed order, so look them up
    assert all(x["kind"] != "portal" and x["kind"] != "place" for x in top["liked"] + top["avoided"]) and not set(liked) & set(avoided)


# ---- the priority ---------------------------------------------------------------------------------------------------

def test_urgency_prefers_time_to_prepare():
    p = lambda days: priority(row(closing=(NOW + timedelta(days=days)).isoformat()), NOW, None, None, None, False)[0]   # noqa: E731
    assert p(0.5) < p(2) < p(7) and p(7) > p(20) > p(60) and p(7) == p(14)
    no_date = priority(row(closing=None), NOW, None, None, None, False)[0]
    assert no_date < p(60) < p(7)                                                              # no closing date at all is scored a little under one that is far away
    assert "little time" in priority(row(closing=(NOW + timedelta(hours=5)).isoformat()), NOW, None, None, None, False)[1]


def test_fit_size_and_stars_add_up_and_are_explained():
    base = priority(row(), NOW, None, None, None, False)
    good = priority(row(fit=5), NOW, None, None, None, False)
    weak = priority(row(fit=1), NOW, None, None, None, False)
    assert good[0] > base[0] > weak[0] and "good fit (5/5)" in good[1] and "weak fit (1/5)" in weak[1]
    inside = priority(row(value=2e7), NOW, None, 1e7, 5e7, False)
    outside = priority(row(value=9e8), NOW, None, 1e7, 5e7, False)
    unknown = priority(row(value=None), NOW, None, 1e7, 5e7, False)
    assert inside[0] - outside[0] == 15 and "fits your size" in inside[1] and "outside your size range" in outside[1] and unknown[0] == base[0]
    assert priority(row(), NOW, None, None, None, True)[0] - base[0] == 10 and "starred" in priority(row(), NOW, None, None, None, True)[1]


def test_the_priority_stays_between_0_and_100():
    best = priority(row(fit=5, value=2e7), NOW, _model(liked=40), 1e7, 5e7, True)[0]
    worst = priority(row(fit=1, value=9e9, closing=(NOW + timedelta(hours=2)).isoformat(), organisation="Housing||B"), NOW, _model(avoided=40), 1e7, 5e7, False)[0]
    assert 90 <= best <= 100 and 0 <= worst <= 10


def _model(liked=0, avoided=0):
    m = Model()
    for _ in range(liked):
        m.add(row(), +1)
    for _ in range(avoided):
        m.add(row(organisation="Housing||B"), -1)
    return m


def test_the_note_names_what_was_learned():
    note = priority(row(), NOW, _model(liked=10), None, None, False)[1]
    assert "you often bid on or star PWD" in note
    note = priority(row(organisation="Housing||B"), NOW, _model(avoided=10), None, None, False)[1]
    assert "you often hide Housing" in note


# ---- in the store: what counts as a choice ------------------------------------------------------------------------------

@pytest.fixture
def store():
    s = SeenStore(":memory:")
    for i in range(1, 9):
        s.keep(tender(i, "PWD", f"Bridge works {i}"), NOW)
    for i in range(21, 29):
        s.keep(tender(i, "Housing", f"Quarters painting {i}", category="Buildings"), NOW)
    return s


def order(s, sort="smart"):
    return [r["tender_id"] for r in s.search(NOW, {"status": "all"}, sort, 100, 0)[0]]


def test_stars_bids_hides_and_dropped_bids_are_the_choices(store):
    book = BidBook(store)
    for i in (1, 2):
        store.set_starred(f"maha:T{i}", True, NOW)
    bid, _ = book.create_from_tender("maha:T3", NOW)
    book.update(bid["id"], {"stage": "submitted"}, NOW)
    dropped, _ = book.create_from_tender("maha:T21", NOW)
    book.update(dropped["id"], {"stage": "dropped"}, NOW)
    store.hide(["maha:T22", "maha:T23"], NOW)
    store.mute_department("Irrigation", NOW)
    m = store.learning_model()
    assert m.signals == {"positive": 3, "negative": 4} and m.ready
    assert m.liked[("dept", "PWD")] == 2 * 1 + 2.0 and m.avoided[("dept", "Housing")] == 3 and m.avoided[("dept", "Irrigation")] == 3


def test_recompute_scores_open_tenders_only_and_the_order_follows(store):
    store.keep(tender(40, "PWD", "Old bridge", days=-30), NOW - timedelta(days=60))             # closed
    assert store.priorities_missing(NOW)
    assert store.recompute_priorities(NOW) == 16 and not store.priorities_missing(NOW)
    old = store.db.execute("SELECT priority FROM seen WHERE key = 'maha:T40'").fetchone()[0]
    assert old is None                                                                          # a closed tender is left alone
    p = store.db.execute("SELECT priority, priority_note FROM seen WHERE key = 'maha:T1'").fetchone()
    assert 0 < p[0] <= 100 and "closes in 7 days" in p[1]


def test_choices_change_the_order_of_similar_tenders(store):
    store.recompute_priorities(NOW)
    assert "you often" not in store.db.execute("SELECT priority_note FROM seen WHERE tender_id = 'T7'").fetchone()[0]     # an even start: nothing learned yet
    for i in range(1, 7):
        store.set_starred(f"maha:T{i}", True, NOW)                                              # six PWD bridges starred
    store.hide([f"maha:T{i}" for i in range(21, 27)], NOW)                                       # six Housing tenders hidden
    store.recompute_priorities(NOW)
    after = order(store)
    pwd_left = [t for t in after if t in ("T7", "T8")]
    housing_left = [t for t in after if t in ("T27", "T28")]
    assert after.index(pwd_left[0]) < after.index(housing_left[0])                                 # the unstarred PWD bridges now outrank the unhidden Housing ones
    notes = dict(store.db.execute("SELECT tender_id, priority_note FROM seen WHERE tender_id IN ('T7','T27')").fetchall())
    assert "you often bid on or star PWD" in notes["T7"] and "you often hide Housing" in notes["T27"]


def test_switching_learning_off_gives_the_plain_order(store):
    for i in range(1, 7):
        store.set_starred(f"maha:T{i}", True, NOW)
    store.recompute_priorities(NOW, learning=True)
    on = store.db.execute("SELECT priority FROM seen WHERE key = 'maha:T7'").fetchone()[0]
    store.recompute_priorities(NOW, learning=False)
    off = store.db.execute("SELECT priority, priority_note FROM seen WHERE key = 'maha:T7'").fetchone()
    assert on > off[0] and "you often" not in off[1]


def test_unscored_tenders_come_last_and_never_break_the_list(store):
    assert len(order(store)) == 16                                                              # nothing scored yet: still answers
    store.recompute_priorities(NOW)
    store.keep(tender(99, "PWD", "Brand new"), NOW)                                              # arrived after the last scoring
    assert order(store)[-1] == "T99"


def test_scoring_thousands_of_tenders_is_fast():
    s = SeenStore(":memory:")
    with s.lock:
        for i in range(5000):
            s.db.execute("INSERT INTO seen (key, first_seen, civil, closing, portal, tender_id, title, organisation, category, location, value) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (f"maha:{i}", NOW.isoformat(), 1, (NOW + timedelta(days=3 + i % 40)).isoformat(), "maha", f"T{i}", f"Road bridge drain work {i}", f"Dept{i % 30}||X", "Works", "Nashik", 1e6 * (1 + i % 50)))
        s.db.commit()
    for i in range(30):
        s.set_starred(f"maha:{i}", True, NOW)
    t0 = time.perf_counter()
    assert s.recompute_priorities(NOW, 1e7, 4e7) == 5000
    took = time.perf_counter() - t0
    print(f"\nscoring 5,000 tenders: {took * 1000:.0f} ms")
    assert took < 3


# ---- over the web -----------------------------------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    st = SeenStore(":memory:")
    now = datetime.now()
    for i in range(1, 9):
        t = tender(i, "PWD", f"Bridge works {i}")
        t.closing, t.published = now + timedelta(days=7), now
        st.keep(t, now)
    for i in range(21, 29):
        t = tender(i, "Housing", f"Quarters painting {i}", category="Buildings")
        t.closing, t.published = now + timedelta(days=7), now
        st.keep(t, now)
    env = settings()
    a = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, st, RunManager(env, st, lambda p, s: None, FakeSender(), background=False))
    owner = TestClient(a)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})

    def person(name, role):
        owner.post("/api/users", json={"username": name, "role": role, "password": "temporary-pass-1"})
        c = TestClient(a)
        c.post("/api/login", json={"username": name, "password": "temporary-pass-1"})
        c.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
        return c

    return type("A", (), {"owner": owner, "person": person, "store": st})


def listing(c, **p):
    return c.get("/api/tenders", params={"status": "all", **p}).json()["items"]


def test_the_list_is_recommended_by_default_and_every_tender_says_why(app):
    items = listing(app.owner)
    assert len(items) == 16 and all(i["priority"] is not None and "closes in" in i["priority_note"] for i in items)    # scored at start-up
    assert [i["priority"] for i in items] == sorted([i["priority"] for i in items], reverse=True)
    assert app.owner.get("/api/tenders", params={"sort": "closing"}).status_code == 200 and app.owner.get("/api/tenders", params={"sort": "relevance", "q": "bridge"}).status_code == 200
    assert app.owner.get("/api/tenders", params={"sort": "nonsense"}).status_code == 422


def test_starring_and_hiding_over_the_api_reorders_the_list(app):
    c = app.owner
    key = lambda tid: next(i["key"] for i in listing(c) if i["tender_id"] == tid)   # noqa: E731
    for i in range(1, 7):
        c.post("/api/tenders/star", json={"key": key(f"T{i}"), "starred": True})
    c.post("/api/tenders/hide", json={"keys": [key(f"T{i}") for i in range(21, 27)]})
    ids = [i["tender_id"] for i in listing(c)]
    assert ids.index("T7") < ids.index("T27")
    learned = c.get("/api/learning").json()
    assert learned["enabled"] and learned["ready"] and learned["positive"] == 6 and learned["negative"] == 6
    assert "PWD" in [x["value"] for x in learned["liked"]] and "Housing" in [x["value"] for x in learned["avoided"]]


def test_a_new_bid_and_a_dropped_bid_are_choices_too(app):
    c = app.owner
    items = listing(c)
    pwd = [i["key"] for i in items if i["tender_id"] in ("T1", "T2", "T3")]
    for k in pwd:
        c.post("/api/bids", json={"key": k})
    assert c.get("/api/learning").json()["positive"] == 3
    bid = c.get("/api/bids").json()["bids"][0]["id"]
    c.patch(f"/api/bids/{bid}", json={"stage": "dropped"})
    L = c.get("/api/learning").json()
    assert L["positive"] == 2 and L["negative"] == 1


def test_learning_can_be_switched_off_and_only_by_an_administrator(app):
    c = app.owner
    for i in range(1, 7):
        c.post("/api/tenders/star", json={"key": next(x["key"] for x in listing(c) if x["tender_id"] == f"T{i}"), "starred": True})
    assert any("you often" in i["priority_note"] for i in listing(c))
    assert c.put("/api/settings", json={"learning": False}).json()["learning"] is False
    assert not any("you often" in i["priority_note"] for i in listing(c)) and c.get("/api/learning").json()["enabled"] is False
    assert c.put("/api/settings", json={"learning": "no"}).status_code == 422
    assert c.put("/api/settings", json={"learning": True}).json()["learning"] is True
    bidder = app.person("raj", "bidder")
    assert bidder.get("/api/learning").status_code == 200 and bidder.put("/api/settings", json={"learning": False}).status_code == 403


def test_the_size_range_moves_tenders_in_the_recommended_order(app):
    c = app.owner
    before = {i["tender_id"]: i["priority"] for i in listing(c)}
    c.put("/api/company-size", json={"min": 1e6, "max": 5e6})                                   # every sample tender is 2 crore: now outside
    after = {i["tender_id"]: i["priority"] for i in listing(c)}
    assert all(after[t] < before[t] for t in before) and "outside your size range" in listing(c)[0]["priority_note"]


def test_a_single_tender_carries_its_reason_too(app):
    key = listing(app.owner)[0]["key"]
    one = app.owner.get("/api/tenders/detail", params={"key": key}).json()
    assert one["priority"] is not None and "closes in" in one["priority_note"]


# ---- what nearly every tender has is no reason -------------------------------------------------------------------

def population(n_works=95, n_other=5, dept=lambda i: "PWD" if i % 10 < 3 else "Water"):
    rows = [row(category="Works", organisation=f"{dept(i)}||X", title=f"Job {i}") for i in range(n_works)]
    rows += [row(category="Buildings", organisation=f"{dept(i)}||X", title=f"Job {i}") for i in range(n_other)]
    return rows


def test_a_feature_on_most_tenders_is_common_and_a_selective_one_is_not():
    common = learn.common_features(population())
    assert ("category", "works") in common and ("portal", "maha") in common            # 95% of tenders, and every tender
    assert ("dept", "PWD") not in common and ("category", "buildings") not in common   # about 30% and 5%


def test_nothing_is_judged_common_until_there_are_enough_tenders_to_compare():
    assert learn.common_features(population(n_works=10, n_other=5)) == set()           # 15 tenders: too few to say
    assert learn.common_features([]) == set()


def test_the_threshold_is_a_share_not_a_count():
    rows = [row(category="Works" if i < 60 else "Other") for i in range(100)]          # exactly 60%
    assert ("category", "works") in learn.common_features(rows)
    rows = [row(category="Works" if i < 59 else "Other") for i in range(100)]          # 59%
    assert ("category", "works") not in learn.common_features(rows)


def test_a_common_feature_has_no_strength_no_part_in_the_score_and_no_place_in_the_list():
    m = Model()
    for _ in range(10):
        m.add(row(organisation="PWD||A", category="Works"), +1)
    assert m.strength(("category", "works")) > 0.5
    m.common = {("category", "works"), ("portal", "maha")}
    assert m.strength(("category", "works")) == 0 and m.strength(("dept", "PWD")) > 0.5
    score, why = m.score(row(organisation="PWD||C", category="Works", title="Unrelated thing"))
    kinds = {(k, v) for k, v, _ in why}
    assert ("category", "works") not in kinds and ("dept", "PWD") in kinds
    top = m.top()
    assert all(x["value"] != "works" for x in top["liked"]) and any(x["value"] == "PWD" for x in top["liked"])


def test_the_notes_name_what_sets_tenders_apart_not_what_all_of_them_share(tmp_path):
    s = SeenStore(":memory:")
    for i in range(30):                                            # three departments, every tender "Works", all on one portal
        s.keep(tender(i, ("PWD", "Housing", "Water")[i % 3], f"Bridge job {i}"), NOW)
    for i in (0, 3, 6, 9, 12, 15):
        s.set_starred(f"maha:T{i}", True, NOW)                     # six PWD tenders starred
    s.recompute_priorities(NOW)
    note = s.db.execute("SELECT priority_note FROM seen WHERE tender_id = 'T18'").fetchone()[0]
    assert "you often bid on or star PWD" in note                  # one department in three: a real preference
    assert "category" not in note and "portal" not in note and "place" not in note   # all thirty are Works, on one portal, in one place


def test_the_card_in_the_vault_lists_only_what_tells_tenders_apart(app):
    c = app.owner
    for i in range(30, 50):                                        # 16 tenders become 36: enough to judge
        t = tender(i, "Irrigation", f"Canal job {i}")
        t.closing, t.published = datetime.now() + timedelta(days=7), datetime.now()
        app.store.keep(t, datetime.now())
    for i in range(1, 7):
        c.post("/api/tenders/star", json={"key": next(x["key"] for x in listing(c) if x["tender_id"] == f"T{i}"), "starred": True})
    liked = [x["value"] for x in c.get("/api/learning").json()["liked"]]
    assert "PWD" in liked and "works" not in liked
