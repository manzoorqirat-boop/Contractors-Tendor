"""One rule for the company's size limits and the learning switch, used by the web page, the daily email and the run."""
from datetime import datetime, timedelta

import pytest

from tender_digest import appsettings
from tender_digest.gepnic import Tender
from tender_digest.store import SeenStore
from tests.test_web import settings

NOW = datetime(2026, 11, 17, 10, 0)


def tender(n, value):
    return Tender(portal="maha", tender_id=f"T{n}", title=f"Road work {n}", reference="", description="Widening of road", organisation="PWD||Nashik", category="Works", location="Nashik",
                  value=value, emd=None, published=NOW, closing=NOW + timedelta(days=7), opening=None)


@pytest.mark.parametrize("saved,expected", [
    ({}, {}),
    ({"size_min": 1e6, "size_max": 5e7}, {"size_min": 1e6, "size_max": 5e7}),
    ({"size_min": 1000000}, {"size_min": 1e6}),                                  # whole numbers become floats
    ({"size_min": 0, "size_max": -5}, {}),                                        # zero or negative means "no limit"
    ({"size_min": None, "size_max": "5000000"}, {}),                              # not numbers
    ({"size_min": True, "size_max": False}, {}),                                  # True is not "1 rupee"
    ({"size_max": 2e7, "other": 1}, {"size_max": 2e7}),
])
def test_size_limits(saved, expected):
    assert appsettings.size_limits(saved) == expected
    assert all(isinstance(v, float) for v in appsettings.size_limits(saved).values())


@pytest.mark.parametrize("saved,expected", [({}, True), ({"learning": True}, True), ({"learning": False}, False), ({"learning": None}, True), ({"learning": 0}, True)])
def test_learning_is_on_unless_switched_off(saved, expected):
    assert appsettings.learning_on(saved) is expected


@pytest.mark.parametrize("saved", [{}, {"learning": True}, {"learning": False}])
def test_the_settings_page_reports_the_same_answer(saved):
    assert appsettings.current(settings(), saved)["learning"] is appsettings.learning_on(saved)


def test_rescore_uses_the_saved_limits_and_the_learning_switch():
    s = SeenStore(":memory:")
    s.keep(tender(1, 2e7), NOW)
    s.keep(tender(2, 9e8), NOW)
    s.put_settings({"size_min": 1e6, "size_max": 5e7})
    assert appsettings.rescore(s, NOW) == 2
    notes = dict(s.db.execute("SELECT tender_id, priority_note FROM seen").fetchall())
    assert "fits your size" in notes["T1"] and "outside your size range" in notes["T2"]
    s.put_settings({"size_min": 0, "size_max": 0})                                 # limits removed: no "size" notes at all
    appsettings.rescore(s, NOW)
    assert not any("size" in (n or "") for _, n in s.db.execute("SELECT tender_id, priority_note FROM seen"))


def test_a_stray_true_is_not_treated_as_a_one_rupee_minimum():
    s = SeenStore(":memory:")
    s.keep(tender(1, 5e6), NOW)
    s.put_settings({"size_min": True})
    appsettings.rescore(s, NOW)
    assert "size" not in s.db.execute("SELECT priority_note FROM seen").fetchone()[0]
