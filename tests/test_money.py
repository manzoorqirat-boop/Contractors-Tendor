"""Money tied up in bids: what is locked, what needs chasing, who may see it, and that nothing breaks the email or deletes the wrong thing."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import digest, money, web
from tender_digest.bids import BidBook, BidError
from tender_digest.gepnic import Tender
from tender_digest.money import MoneyBook, clean, locked
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, settings

TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 10, 0)


def d(days):
    return (TODAY + timedelta(days=days)).isoformat()


def tender(n=1, emd=50000.0, days=10):
    return Tender(portal="maha", tender_id=f"T{n}", title="Road work", reference="", description="Widening of road", organisation="PWD||Nashik", category="Works", location="Nashik",
                  value=2e7, emd=emd, published=NOW, closing=NOW + timedelta(days=days), opening=None)


@pytest.fixture
def book():
    store = SeenStore(":memory:")
    store.keep(tender(1), NOW)
    bids = BidBook(store)
    bid, _ = bids.create_from_tender("maha:T1", NOW)
    return type("B", (), {"store": store, "bids": bids, "money": MoneyBook(bids), "bid": bid, "id": bid["id"]})


def pay(book, **kw):
    return book.money.add(book.id, {"kind": "emd", "amount": 50000, "instrument": "online", "status": "paid", "paid_on": d(-2), **kw}, NOW)


# ---- validating what is typed ------------------------------------------------------------------------------------

def test_a_payment_needs_a_kind_and_an_amount():
    with pytest.raises(BidError, match="what the payment is for"):
        clean({"amount": 5}, TODAY)
    with pytest.raises(BidError, match="Enter the amount"):
        clean({"kind": "emd"}, TODAY)
    got = clean({"kind": "emd", "amount": "50,000".replace(",", "")}, TODAY)
    assert got["amount"] == 50000 and got["instrument"] == "online" and got["status"] == "planned"


@pytest.mark.parametrize("raw,message", [
    ({"kind": "emd", "amount": 0}, "more than zero"), ({"kind": "emd", "amount": -5}, "more than zero"), ({"kind": "emd", "amount": "abc"}, "must be a number"),
    ({"kind": "emd", "amount": True}, "more than zero"), ({"kind": "emd", "amount": 1e14}, "more than zero"), ({"kind": "gold", "amount": 5}, "what the payment is for"),
    ({"kind": "emd", "amount": 5, "instrument": "bitcoin"}, "how it was paid"), ({"kind": "emd", "amount": 5, "status": "lost"}, "Unknown status"),
    ({"kind": "emd", "amount": 5, "paid_on": "yesterday"}, "real dates"), ({"kind": "emd", "amount": 5, "valid_until": "2026-02-31"}, "real dates"),
    ({"kind": "emd", "amount": 5, "owner": "me"}, "cannot be set"),
])
def test_bad_payments_are_refused_with_a_reason(raw, message):
    with pytest.raises(BidError, match=message):
        clean(raw, TODAY)
    with pytest.raises(BidError):
        clean("not a dict", TODAY)


def test_long_text_is_cut_not_refused():
    got = clean({"kind": "emd", "amount": 1, "reference": "x" * 500, "note": "y" * 900}, TODAY)
    assert len(got["reference"]) == 60 and len(got["note"]) == 300


# ---- what each status means --------------------------------------------------------------------------------------

def test_a_paid_payment_gets_todays_date_and_a_settled_one_gets_a_settled_date(book):
    r = book.money.add(book.id, {"kind": "emd", "amount": 50000, "status": "paid"}, NOW)
    assert r["paid_on"] == TODAY.isoformat() and r["settled_on"] is None
    r = book.money.update(book.id, r["id"], {"status": "refunded"}, NOW)
    assert r["settled_on"] == TODAY.isoformat()
    r = book.money.update(book.id, r["id"], {"status": "paid"}, NOW)                         # put back: it is no longer settled
    assert r["settled_on"] is None


def test_dates_must_make_sense(book):
    with pytest.raises(BidError, match="before it was paid"):
        book.money.add(book.id, {"kind": "emd", "amount": 5, "status": "refunded", "paid_on": d(-1), "settled_on": d(-5)}, NOW)
    with pytest.raises(BidError, match="due before the payment"):
        book.money.add(book.id, {"kind": "emd", "amount": 5, "status": "paid", "paid_on": d(-1), "refund_due": d(-5)}, NOW)


def test_a_fee_does_not_come_back_so_it_has_no_refund_date_and_is_never_locked(book):
    r = book.money.add(book.id, {"kind": "tender_fee", "amount": 1500, "status": "paid", "refund_due": d(10)}, NOW)
    assert r["refund_due"] is None and not locked(r) and not money.refundable("tender_fee") and money.refundable("emd")


def test_only_a_guarantee_deposit_or_draft_has_a_validity_date(book):
    r = pay(book, instrument="online", valid_until=d(30))
    assert r["valid_until"] is None
    r = pay(book, instrument="bg", valid_until=d(30))
    assert r["valid_until"] == d(30)


# ---- totals -------------------------------------------------------------------------------------------------------

def test_cash_and_guarantees_are_counted_apart_and_only_while_held(book):
    pay(book, amount=50000)                                                                   # online: cash out
    pay(book, amount=200000, instrument="bg", valid_until=d(200))                              # bank guarantee: a limit used, not cash
    pay(book, amount=30000, instrument="fdr", valid_until=d(200))                              # a deposit receipt: cash held at the bank
    book.money.add(book.id, {"kind": "tender_fee", "amount": 1500, "status": "paid"}, NOW)
    refunded = pay(book, amount=40000)
    book.money.update(book.id, refunded["id"], {"status": "refunded"}, NOW)
    forfeited = pay(book, amount=10000)
    book.money.update(book.id, forfeited["id"], {"status": "forfeited"}, NOW)
    book.money.add(book.id, {"kind": "performance_security", "amount": 999000, "status": "planned"}, NOW)
    t = book.money.bid_view(book.id, NOW)["totals"]
    assert t == {"cash_locked": 80000, "guarantees": 200000, "fees_paid": 1500, "refunded": 40000, "forfeited": 10000, "planned": 999000}


# ---- what needs a person ------------------------------------------------------------------------------------------

def codes(book, bid_stage=None):
    if bid_stage:
        book.bids.update(book.id, {"stage": bid_stage}, NOW)
    return {a["code"]: a for a in book.money.attention(NOW)}


def test_an_overdue_refund_is_urgent_and_says_how_late(book):
    pay(book, paid_on=d(-40), refund_due=d(-12))
    a = codes(book)["refund_overdue"]
    assert a["severity"] == "high" and "12 days ago" in a["text"] and a["amount"] == 50000


def test_a_refund_due_this_week_is_a_low_priority_note(book):
    pay(book, refund_due=d(3))
    assert codes(book)["refund_soon"]["severity"] == "low" and "in 3 days" in codes(book)["refund_soon"]["text"]
    assert book.money.digest_lines(NOW) == []                                               # not worth an email line yet


def test_a_lost_or_dropped_bid_with_the_emd_still_held_prompts_to_ask_for_it(book):
    pay(book)
    assert "refund_ask" not in codes(book)                                                  # the bid is still open
    for stage in ("lost", "dropped"):
        assert codes(book, stage)["refund_ask"]["severity"] == "medium"
    book.money.update(book.id, book.money.for_bid(book.id)[0]["id"], {"refund_due": d(20)}, NOW)
    assert "refund_ask" not in codes(book)                                                  # once a refund date is noted it stops nagging


def test_a_won_bid_with_the_emd_still_held_is_a_gentle_reminder(book):
    pay(book)
    assert codes(book, "won")["emd_won"]["severity"] == "low"


def test_a_guarantee_running_out_is_flagged_by_how_soon(book):
    pay(book, instrument="bg", valid_until=d(5))
    assert codes(book)["expiring"]["severity"] == "high" and "5 days" in codes(book)["expiring"]["text"]
    book.store.db.execute("UPDATE bid_money SET valid_until = ?", (d(25),))
    assert codes(book)["expiring"]["severity"] == "medium"
    book.store.db.execute("UPDATE bid_money SET valid_until = ?", (d(90),))
    assert "expiring" not in codes(book)
    book.store.db.execute("UPDATE bid_money SET valid_until = ?", (d(-3),))
    assert codes(book)["expired"]["severity"] == "high" and "3 days ago" in codes(book)["expired"]["text"]


def test_a_released_guarantee_is_not_flagged(book):
    r = pay(book, instrument="bg", valid_until=d(-3))
    book.money.update(book.id, r["id"], {"status": "refunded"}, NOW)
    assert book.money.attention(NOW) == []


def test_an_emd_not_paid_when_the_bid_is_about_to_close(book):
    book.money.add(book.id, {"kind": "emd", "amount": 50000, "status": "planned"}, NOW)
    assert "unpaid" not in codes(book)                                                      # closes in 10 days
    book.store.db.execute("UPDATE bids SET closing = ?", ((NOW + timedelta(days=1)).isoformat(),))
    a = codes(book)["unpaid"]
    assert a["severity"] == "high" and "not paid yet" in a["text"]
    book.bids.update(book.id, {"stage": "dropped"}, NOW)
    assert "unpaid" not in codes(book)                                                      # a dropped bid needs no payment


def test_the_emd_due_date_set_on_the_bid_is_used_when_there_is_one(book):
    book.money.add(book.id, {"kind": "emd", "amount": 50000, "status": "planned"}, NOW)
    book.bids.update(book.id, {"emd_due": d(-1)}, NOW)
    assert "overdue" in codes(book)["unpaid"]["text"] and "EMD" in codes(book)["unpaid"]["text"]


def test_the_most_urgent_comes_first_and_the_email_gets_only_medium_and_high(book):
    pay(book, refund_due=d(3))                                                              # low
    pay(book, instrument="bg", valid_until=d(25))                                           # medium
    pay(book, paid_on=d(-30), refund_due=d(-2))                                             # high
    order = [a["severity"] for a in book.money.attention(NOW)]
    assert order == sorted(order, key=["high", "medium", "low"].index)
    assert {m["severity"] for m in book.money.digest_lines(NOW)} <= {"high", "medium"} and len(book.money.digest_lines(NOW, limit=1)) == 1


def test_the_bid_page_suggests_recording_the_emd_the_tender_asks_for(book):
    v = book.money.bid_view(book.id, NOW)
    assert v["suggestion"]["amount"] == 50000 and v["items"] == []
    pay(book)
    assert book.money.bid_view(book.id, NOW)["suggestion"] is None                          # already recorded
    book.bids.update(book.id, {"stage": "lost"}, NOW)
    book.money.delete(book.id, book.money.for_bid(book.id)[0]["id"])
    assert book.money.bid_view(book.id, NOW)["suggestion"] is None                          # a lost bid needs no suggestion either


def test_the_summary_adds_up_across_bids(book):
    book.store.keep(tender(2, emd=0), NOW)
    second, _ = book.bids.create_from_tender("maha:T2", NOW)
    pay(book, amount=50000, refund_due=d(10))
    book.money.add(second["id"], {"kind": "emd", "amount": 25000, "status": "paid", "paid_on": d(-30), "refund_due": d(-5)}, NOW)
    s = book.money.summary(NOW)
    assert s["totals"]["cash_locked"] == 75000 and s["refunds_due_30_days"] == 50000 and s["refunds_overdue"] == 25000 and s["count"] == 2
    assert [b["cash"] for b in s["by_bid"]] == [50000, 25000]


def test_deleting_a_bid_deletes_its_payments_and_only_its_own(book):
    book.store.keep(tender(2), NOW)
    other, _ = book.bids.create_from_tender("maha:T2", NOW)
    pay(book)
    book.money.add(other["id"], {"kind": "emd", "amount": 1, "status": "paid"}, NOW)
    book.bids.delete(book.id)
    assert book.money.for_bid(book.id) == [] and len(book.money.for_bid(other["id"])) == 1


def test_one_bids_payment_cannot_be_changed_through_another_bid(book):
    book.store.keep(tender(2), NOW)
    other, _ = book.bids.create_from_tender("maha:T2", NOW)
    r = pay(book)
    with pytest.raises(BidError, match="No such payment"):
        book.money.update(other["id"], r["id"], {"amount": 1}, NOW)
    assert book.money.delete(other["id"], r["id"]) is False and len(book.money.for_bid(book.id)) == 1


def test_the_export_has_one_row_per_payment_with_readable_words(book):
    pay(book, reference="DD 123456", bank="SBI Nashik")
    rows = book.money.export_rows()
    assert len(rows) == 2 and rows[0][4] == "Payment" and rows[1][4] == "Earnest money (EMD)" and rows[1][6] == "Online (net banking, UPI, NEFT)" and rows[1][7] == "DD 123456"


# ---- the daily email -------------------------------------------------------------------------------------------------

def test_money_to_chase_appears_in_the_daily_email():
    m = [{"severity": "high", "code": "refund_overdue", "text": "Earnest money (EMD) should have come back 12 days ago: ask for the refund", "tender_id": "T1", "title": "Widening of road", "amount": 50000.0}]
    out = digest.build(NOW, [], [], [], bids={"alerts": [], "changes": [], "money": m})
    assert "Money to chase" in out.html and "12 days ago" in out.html and "MONEY:" in out.text and "T1" in out.text
    plain = digest.build(NOW, [], [], [], bids={"alerts": [], "changes": []})                 # an older caller that knows nothing of money still works
    assert "Money to chase" not in plain.html


# ---- over the web -------------------------------------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    st = SeenStore(":memory:")
    now = datetime.now()
    t = tender(1)
    t.closing, t.published = now + timedelta(days=10), now
    st.keep(t, now)
    env = settings()
    a = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, st, RunManager(env, st, lambda p, s: None, FakeSender(), background=False))
    owner = TestClient(a)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    bid = owner.post("/api/bids", json={"key": "maha:T1"}).json()

    def person(name, role):
        owner.post("/api/users", json={"username": name, "role": role, "password": "temporary-pass-1"})
        c = TestClient(a)
        c.post("/api/login", json={"username": name, "password": "temporary-pass-1"})
        c.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
        return c

    return type("A", (), {"owner": owner, "person": person, "bid_id": bid["id"], "anon": TestClient(a)})


def test_login_is_required(app):
    assert app.anon.get(f"/api/bids/{app.bid_id}/money").status_code == 401 and app.anon.get("/api/money").status_code == 401


def test_record_change_and_remove_a_payment(app):
    c, url = app.owner, f"/api/bids/{app.bid_id}/money"
    start = c.get(url).json()
    assert start["items"] == [] and start["suggestion"]["amount"] == 50000
    r = c.post(url, json={"kind": "emd", "amount": 50000, "instrument": "dd", "reference": "DD 778899", "bank": "SBI", "status": "paid"})
    assert r.status_code == 200 and r.json()["totals"]["cash_locked"] == 50000 and r.json()["suggestion"] is None
    item = r.json()["items"][0]
    assert item["kind_label"] == "Earnest money (EMD)" and item["locked"] and item["refundable"] and item["reference"] == "DD 778899"
    r = c.patch(f"{url}/{item['id']}", json={"status": "refunded"})
    assert r.json()["totals"] == {"cash_locked": 0, "guarantees": 0, "fees_paid": 0, "refunded": 50000, "forfeited": 0, "planned": 0}
    assert c.delete(f"{url}/{item['id']}").json()["items"] == [] and c.delete(f"{url}/{item['id']}").status_code == 404


def test_bad_input_and_missing_bids_give_clear_errors(app):
    c = app.owner
    assert c.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd", "amount": -1}).status_code == 422
    assert c.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd"}).status_code == 422
    assert c.get("/api/bids/999/money").status_code == 404 and c.post("/api/bids/999/money", json={"kind": "emd", "amount": 5}).status_code == 404
    assert c.patch(f"/api/bids/{app.bid_id}/money/999", json={"amount": 5}).status_code == 422


def test_view_only_accounts_cannot_see_or_change_money(app):
    app.owner.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd", "amount": 50000, "status": "paid"})
    viewer = app.person("vee", "viewer")
    for path in (f"/api/bids/{app.bid_id}/money", "/api/money", "/api/money/export"):
        assert viewer.get(path).status_code == 403, path
    assert viewer.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd", "amount": 5}).status_code == 403
    bidder = app.person("raj", "bidder")
    assert bidder.get("/api/money").status_code == 200 and bidder.post(f"/api/bids/{app.bid_id}/money", json={"kind": "tender_fee", "amount": 1500}).status_code == 200


def test_the_summary_and_the_spreadsheet(app):
    app.owner.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd", "amount": 50000, "status": "paid", "paid_on": "2026-01-01", "refund_due": "2026-02-01", "reference": "=HYPERLINK(1)"})
    s = app.owner.get("/api/money").json()
    assert s["totals"]["cash_locked"] == 50000 and s["refunds_overdue"] == 50000 and s["attention"][0]["code"] == "refund_overdue" and s["by_bid"][0]["cash"] == 50000
    csv_ = app.owner.get("/api/money/export")
    assert csv_.status_code == 200 and "attachment" in csv_.headers["content-disposition"] and csv_.content.startswith(b"\xef\xbb\xbf")
    text = csv_.content.decode("utf-8-sig")
    assert "Earnest money (EMD)" in text and "'=HYPERLINK(1)" in text and "\n=HYPERLINK" not in text            # typed text can never run as a spreadsheet formula


def test_changes_are_in_the_activity_log_by_name(app):
    app.owner.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd", "amount": 50000, "status": "paid"})
    log = app.owner.get("/api/audit").json()
    rows = log["entries"] if isinstance(log, dict) and "entries" in log else log
    assert any("Recorded a payment on a bid" in (r.get("action") or "") for r in rows)


def test_removing_a_bid_over_the_api_takes_its_payments_with_it(app):
    app.owner.post(f"/api/bids/{app.bid_id}/money", json={"kind": "emd", "amount": 50000, "status": "paid"})
    assert app.owner.get("/api/money").json()["count"] == 1
    app.owner.delete(f"/api/bids/{app.bid_id}")
    assert app.owner.get("/api/money").json()["count"] == 0
