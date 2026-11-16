"""Ticking several tenders: export (CSV or Excel), star, start bids; and saved searches that email their new matches."""
import io
from datetime import timedelta

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from tender_digest import alerts, digest, web
from tender_digest.gepnic import Tender
from tender_digest.store import SeenStore
from tests.test_bids import NOW, app_ctx, kept  # noqa: F401
from tests.test_web import PASSWORD, ctx, settings  # noqa: F401


def more(store, n, **kw):
    t = Tender(portal="maha", tender_id=f"T{n}", title=f"Road {n}", reference="", description=kw.pop("description", f"Construction of road {n}"), organisation="PWD||Division",
               category="Works", location=kw.pop("location", "Pune"), value=kw.pop("value", 2e7), emd=2e5, published=NOW, closing=NOW + timedelta(days=kw.pop("days", 10)), opening=None)
    store.keep(t, NOW)
    return t


# ---- export -----------------------------------------------------------------------------------------------------------

def test_the_tender_list_downloads_as_an_excel_workbook(app_ctx):
    c = app_ctx.client
    more(app_ctx.store, 2, value=1500000)
    r = c.get("/api/tenders/export", params={"format": "xlsx", "status": "all"})
    assert r.status_code == 200 and r.headers["content-type"].endswith("spreadsheetml.sheet") and ".xlsx" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content)).active
    assert [c.value for c in ws[1]][:4] == ["Status", "Portal", "Tender ID", "Work"]
    assert ws.max_row == 3 and ws.freeze_panes == "A2"
    values = {ws.cell(row=i, column=3).value: ws.cell(row=i, column=8) for i in (2, 3)}
    assert values["T2"].value == 1500000 and values["T2"].number_format == "#,##0"  # a real number, not text


def test_text_that_looks_like_a_formula_stays_text_in_both_formats(app_ctx):
    more(app_ctx.store, 3, description='=HYPERLINK("http://evil.example","click")')
    c = app_ctx.client
    ws = load_workbook(io.BytesIO(c.get("/api/tenders/export", params={"format": "xlsx", "status": "all"}).content)).active
    cell = next(ws.cell(row=i, column=4) for i in range(2, ws.max_row + 1) if "HYPERLINK" in str(ws.cell(row=i, column=4).value))
    assert cell.data_type == "s"
    csv_text = c.get("/api/tenders/export", params={"status": "all"}).text
    assert "'=HYPERLINK" in csv_text


def test_only_the_ticked_tenders_are_exported_even_if_closed(app_ctx):
    closed = more(app_ctx.store, 4, days=-5)
    other = more(app_ctx.store, 5)
    c = app_ctx.client
    r = c.get("/api/tenders/export", params=[("keys", closed.key), ("keys", other.key), ("format", "xlsx")])
    ws = load_workbook(io.BytesIO(r.content)).active
    ids = sorted(ws.cell(row=i, column=3).value for i in range(2, ws.max_row + 1))
    assert ids == ["T4", "T5"]
    assert c.get("/api/tenders/export", params={"format": "pdf"}).status_code == 422


# ---- star and bids in bulk ------------------------------------------------------------------------------------------------

def test_starring_several_at_once(app_ctx):
    a, b = more(app_ctx.store, 6), more(app_ctx.store, 7)
    c = app_ctx.client
    r = c.post("/api/tenders/star-bulk", json={"keys": [a.key, b.key, "nope", a.key], "starred": True})
    assert r.json() == {"changed": 2, "starred": True}  # the unknown one is skipped, the repeat counted once
    assert c.get("/api/tenders", params={"starred": True, "status": "all"}).json()["total"] == 2
    assert c.post("/api/tenders/star-bulk", json={"keys": [a.key], "starred": False}).json()["changed"] == 1
    assert c.post("/api/tenders/star-bulk", json={"keys": [f"k{i}" for i in range(201)], "starred": True}).status_code == 422


def test_starting_bids_on_several_tenders(app_ctx):
    a = more(app_ctx.store, 8)
    c = app_ctx.client
    first = c.post("/api/bids", json={"key": app_ctx.key}).json()["id"]
    r = c.post("/api/bids/bulk", json={"keys": [app_ctx.key, a.key, "gone"]}).json()
    assert r["existing"] == [first] and len(r["created"]) == 1 and r["missing"] == ["gone"]
    assert c.get(f"/api/bids/{r['created'][0]}").json()["items"]  # the standard checklist was made
    assert len(c.get("/api/bids").json()["bids"]) == 2


def test_view_only_accounts_cannot_star_or_start_bids_in_bulk(app_ctx):
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), settings(), app_ctx.store)
    owner = TestClient(app)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    owner.post("/api/users", json={"username": "vera", "role": "viewer", "password": "temporary-pass-1"})
    v = TestClient(app)
    v.post("/api/login", json={"username": "vera", "password": "temporary-pass-1"})
    v.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
    assert v.post("/api/tenders/star-bulk", json={"keys": [app_ctx.key], "starred": True}).status_code == 403
    assert v.post("/api/bids/bulk", json={"keys": [app_ctx.key]}).status_code == 403
    assert v.get("/api/tenders/export", params={"format": "xlsx"}).status_code == 200  # looking and downloading the list is allowed


# ---- saved searches that email ------------------------------------------------------------------------------------------------

def test_a_saved_views_filters_are_cleaned_before_use():
    f = alerts.view_filters({"portal": ["maha", 5, {"x": 1}], "q": "  road ", "status": "closed", "min_value": "5000000", "max_value": "abc", "closing_within": "400",
                             "min_fit": "3", "starred": True, "closing_from": "2026-13-45", "seen_from": "2026-11-01", "evil": "'; DROP TABLE seen;--", "sort": "value"})
    assert f["portal"] == ["maha", "5"] and f["q"] == "road" and f["status"] == "open"  # a view of closed tenders cannot alert
    assert f["min_value"] == 5000000.0 and "max_value" not in f and "closing_within" not in f and f["min_fit"] == 3 and f["starred"] is True
    assert "closing_from" not in f and f["seen_from"] == "2026-11-01" and "evil" not in f and "sort" not in f
    assert alerts.view_filters("not a dict")["status"] == "open"


def test_only_new_matching_tenders_are_listed_for_each_alert_view():
    store = SeenStore(":memory:")
    pune_big = more(store, 1, location="Pune", value=9e7)
    pune_small = more(store, 2, location="Pune", value=1e5)
    nagpur_big = more(store, 3, location="Nagpur", value=9e7)
    old_pune = more(store, 4, location="Pune", value=9e7)  # already emailed before: not among the new keys
    views = [
        {"id": "1", "name": "Big Pune works", "alert": True, "filters": {"location": ["Pune"], "min_value": "10000000"}},
        {"id": "2", "name": "Nothing matches", "alert": True, "filters": {"location": ["Goa"]}},
        {"id": "3", "name": "Not an alert", "alert": False, "filters": {"location": ["Pune"]}},
    ]
    new = [pune_big.key, pune_small.key, nagpur_big.key]
    got = alerts.matches(store, NOW, views, new)
    assert [g["name"] for g in got] == ["Big Pune works"]
    assert got[0]["count"] == 1 and got[0]["items"][0]["tender_id"] == "T1"
    assert alerts.matches(store, NOW, views, []) == []
    assert old_pune.key not in str(got)


def test_the_digest_carries_a_section_for_saved_searches():
    searches = [{"name": "Big Pune works", "count": 12, "items": [{"tender_id": "T1", "title": "Road <b>one</b>", "organisation": "PWD > Div", "value": 9e7, "closing": "2026-12-01T10:00:00"}]}]
    d = digest.build(NOW, [], [], [], searches=searches)
    assert "Your saved searches" in d.html and "Big Pune works (12 new)" in d.html and "and 11 more in the app" in d.html
    assert "Road &lt;b&gt;one&lt;/b&gt;" in d.html and "<b>one</b>" not in d.html  # tender text is escaped
    assert "SAVED SEARCH: Big Pune works (12 new)" in d.text
    assert "saved searches" not in digest.build(NOW, [], [], []).html


def test_saved_views_remember_the_alert_flag_and_the_limit(app_ctx):
    c = app_ctx.client
    views = [{"id": "a", "name": "Pune", "filters": {"location": ["Pune"]}, "alert": True}, {"id": "b", "name": "Plain", "filters": {}}]
    got = c.put("/api/views", json={"views": views}).json()["views"]
    assert got[0]["alert"] is True and got[1]["alert"] is False
    many = [{"id": str(i), "name": f"v{i}", "filters": {}, "alert": True} for i in range(11)]
    r = c.put("/api/views", json={"views": many})
    assert r.status_code == 422 and "At most 10" in r.json()["detail"]


def test_a_real_digest_run_includes_the_saved_search_section(ctx):
    from tests.test_web import login
    ctx.store.put_settings({"views": [{"id": "1", "name": "Pune roads", "alert": True, "filters": {"location": ["Pune"]}}]})
    login(ctx)
    r = ctx.client.post("/api/runs", json={"send_email": True})
    assert r.status_code == 202
    html = ctx.sender.calls[-1][0][5]  # (key, from, name, recipients, subject, html, text)
    assert "Your saved searches" in html and "Pune roads" in html
