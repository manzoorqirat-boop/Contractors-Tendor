"""The pool of award results: reading a spreadsheet, refusing typing errors, keeping each tender once, and what the advisor does with it."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tender_digest import market, web
from tender_digest.bids import BidBook
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_bids import app_ctx  # noqa: F401
from tests.test_boq_api import b64, load_catalogue, start_bid
from tests.test_portals import PASSWORD, env_settings

NOW = datetime(2026, 11, 20, 10, 0)


# ---- reading numbers and dates -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,rupees", [
    ("500000", 500000), ("5,00,000", 500000), ("₹ 5,00,000", 500000), ("Rs. 8,51,184.50", 851184.5), ("Rs 5,00,000/-", 500000), ("INR 1,25,000", 125000),
    ("2.5 crore", 25_000_000), ("2.5 Cr", 25_000_000), ("50 lakh", 5_000_000), ("12L", 1_200_000), ("75k", 75_000), (" 1,234,567 ", 1_234_567),
])
def test_amounts_are_read_the_way_indian_sheets_write_them(text, rupees):
    assert market.parse_amount(text) == pytest.approx(rupees)


@pytest.mark.parametrize("text", ["", None, "abc", "NA", "-", "1.2.3", "5 million", "lakh", "₹"])
def test_what_is_not_an_amount_is_none(text):
    assert market.parse_amount(text) is None


@pytest.mark.parametrize("text,iso", [
    ("2026-11-05", "2026-11-05"), ("05-11-2026", "2026-11-05"), ("05/11/2026", "2026-11-05"), ("5.11.2026", "2026-11-05"), ("5-Nov-2026", "2026-11-05"),
    ("5 November 2026", "2026-11-05"), ("05-11-2026 05:30 PM", "2026-11-05"), ("2026-11-05T10:00:00", "2026-11-05"), ("05/11/26", "2026-11-05"),
])
def test_dates_are_day_first_and_times_are_ignored(text, iso):
    assert market.parse_date(text) == iso


@pytest.mark.parametrize("text", ["", None, "soon", "13/13/2026", "31-02-2026", "05-11-1850", "05-11-2099"])
def test_an_unreadable_or_absurd_date_is_none(text):
    assert market.parse_date(text) is None


# ---- reading a table ---------------------------------------------------------------------------------------------------

SHEET = """Tender ID,Department,Category,Estimated Cost,Awarded Amount,No. of Bidders,Award Date
2026_PWD_1,PWD Nashik,Roads & highways,"50,00,000","46,00,000",6,05-11-2026
2026_PWD_2,PWD Nashik,Roads & highways,"₹ 1,00,00,000",Rs. 9500000,4,10-10-2026
2026_WR_3,Water Resources,Irrigation,2.5 crore,2.6 crore,3,
"""


def test_a_spreadsheet_is_read_with_loose_column_names_and_indian_numbers():
    good, bad, info = market.parse_table(SHEET)
    assert bad == [] and len(good) == 3
    first = good[0]
    assert first["department"] == "PWD Nashik" and first["work_type"] == "Roads & highways" and (first["estimate"], first["price"]) == (5_000_000, 4_600_000)
    assert first["bidders"] == 6 and first["awarded"] == "2026-11-05" and first["ref"] == "id:2026_pwd_1"
    assert good[2]["awarded"] is None and good[2]["price"] == 26_000_000
    assert info["columns"]["estimate"] == "Estimated Cost" and info["columns"]["price"] == "Awarded Amount" and info["missing"] == []


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_a_table_pasted_from_a_spreadsheet_works_whatever_separates_the_columns(delimiter):
    text = "\n".join(delimiter.join(c.replace(delimiter, " ") for c in row) for row in [
        ["Department", "Estimate", "Awarded Value", "Date"], ["PWD", "1000000", "900000", "01-10-2026"], ["PWD", "2000000", "1900000", "02-10-2026"]])
    good, bad, _ = market.parse_table(text)
    assert len(good) == 2 and bad == []


def test_a_row_with_a_typing_error_is_left_out_and_explained_and_the_rest_are_kept():
    text = "Department,Estimated Cost,Awarded Amount\nPWD,1000000,900000\nPWD,1000000,90000\nPWD,1000000,\nPWD,abc,900000\nPWD,1000000,9500000\n"
    good, bad, _ = market.parse_table(text)
    assert len(good) == 1
    reasons = {b["line"]: b["reason"] for b in bad}
    assert "typing error" in reasons[3] and "9% of the estimate" in reasons[3]
    assert "missing or not a number" in reasons[4] and "missing or not a number" in reasons[5] and "typing error" in reasons[6]


def test_a_table_without_the_needed_columns_says_which():
    good, bad, info = market.parse_table("Department,Date\nPWD,01-10-2026\nPWD,02-10-2026\n")
    assert good == [] and info["missing"] == ["estimate", "price"] and "estimate" in bad[0]["reason"] and "awarded" in bad[0]["reason"]
    good, bad, _ = market.parse_table("Department,Estimated Cost\nPWD,1000000\n")
    assert good == [] and "awarded or lowest price" in bad[0]["reason"] and "estimate" not in bad[0]["reason"].split("and")[0]


def test_exact_header_words_win_over_loose_matches():
    # "Estimated Cost" must be the estimate and "Awarded Amount" the price, even though both headers contain other tempting words
    good, _, info = market.parse_table("Awarded Amount,Estimated Cost,Category of Work\n900000,1000000,Roads\n")
    assert (good[0]["estimate"], good[0]["price"]) == (1_000_000, 900_000)


def test_empty_and_one_line_input_is_refused_not_crashed():
    for text in ("", "   ", "Department,Estimate,Awarded\n", None):
        good, bad, _ = market.parse_table(text)
        assert good == [] and bad


def test_a_huge_input_is_refused():
    good, bad, _ = market.parse_table("a,b\n" + "1,2\n" * 400_000)
    assert good == [] and "too large" in bad[0]["reason"]


def test_a_tender_without_an_id_is_recognised_by_what_it_says():
    a = market.parse_table("Department,Estimated Cost,Awarded Amount,Date\nPWD,1000000,900000,01-10-2026\n")[0][0]["ref"]
    b = market.parse_table("Department,Estimated Cost,Awarded Amount,Date\n pwd ,1000000,900000,01-10-2026\n")[0][0]["ref"]
    c = market.parse_table("Department,Estimated Cost,Awarded Amount,Date\nPWD,1000000,900001,01-10-2026\n")[0][0]["ref"]
    assert a == b != c and a.startswith("h:")


# ---- the pool ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def book():
    store = SeenStore(":memory:")
    return market.MarketBook(BidBook(store, None))


def test_the_same_tender_imported_twice_is_kept_once(book):
    good = market.parse_table(SHEET)[0]
    assert book.add_rows(good, "import", NOW) == {"added": 3, "duplicates": 0}
    assert book.add_rows(good, "import", NOW) == {"added": 0, "duplicates": 3}
    assert book.summary()["imported"] == 3


def test_the_summary_counts_by_work_type_and_department_and_dates(book):
    book.add_rows(market.parse_table(SHEET)[0], "import", NOW)
    s = book.summary()
    assert (s["imported"], s["own"], s["usable"], s["dated"]) == (3, 0, 3, 2) and (s["first"], s["last"]) == ("2026-10-10", "2026-11-05")
    assert {"label": "Roads & highways", "n": 2} in s["by_work_type"] and {"label": "PWD Nashik", "n": 2} in s["by_department"]


def test_results_can_be_removed_one_by_one_or_by_source(book):
    book.add_rows(market.parse_table(SHEET)[0], "import", NOW)
    book.add_rows([dict(market.parse_table(SHEET)[0][0], ref="id:other")], "manual", NOW)
    first = book.recent()[0]["id"]
    assert book.delete(first) is True and book.delete(first) is False
    assert book.clear("import") == 2 and book.summary()["imported"] == 1
    assert book.clear() == 1 and book.summary()["imported"] == 0


def test_a_decided_bid_and_its_imported_result_count_once(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    assert c.patch(f"/api/bids/{bid}", json={"stage": "lost", "l1_value": 18_000_000}).status_code == 200
    pool = market.MarketBook(BidBook(app_ctx.store))
    assert [r["source"] for r in pool.model_rows()] == ["own"]                       # the decided bid: L1 at 18,000,000 against its 2 crore estimate
    tender_id = app_ctx.key.split(":", 1)[1]
    pool.add_rows([{"department": "PWD", "work_type": "", "location": "", "estimate": 2e7, "price": 1.8e7, "bidders": None, "awarded": None, "ref": "id:" + tender_id.lower()},
                   {"department": "PWD", "work_type": "", "location": "", "estimate": 2e7, "price": 1.7e7, "bidders": None, "awarded": None, "ref": "id:another"}], "import", NOW)
    rows = pool.model_rows()
    assert sorted(r["source"] for r in rows) == ["market", "own"]                    # the imported copy of the same tender was not counted twice; the other one was
    assert pool.summary()["imported"] == 2 and pool.summary()["usable"] == 2


# ---- the endpoints and the advice --------------------------------------------------------------------------------------

@pytest.fixture
def client():
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_import_keeps_the_good_rows_and_lists_the_rest(client):
    r = client.post("/api/market-results/import", json={"text": SHEET + "2026_X_9,PWD,Roads,1000000,50000,2,01-11-2026\n"})
    assert r.status_code == 200
    body = r.json()
    assert (body["added"], body["duplicates"], body["rejected_total"]) == (3, 0, 1) and "typing error" in body["rejected"][0]["reason"]
    again = client.post("/api/market-results/import", json={"text": SHEET}).json()
    assert (again["added"], again["duplicates"]) == (0, 3)
    listing = client.get("/api/market-results").json()
    assert listing["imported"] == 3 and len(listing["rows"]) == 3 and listing["rows"][0]["department"]


def test_a_file_whose_commas_split_the_amounts_gets_a_hint_about_quotes(client):
    text = "Department,Estimated Cost,Awarded Amount\nPWD,5,00,000,4,60,000\nPWD,10,00,000,9,50,000\n"
    r = client.post("/api/market-results/import", json={"text": text}).json()
    assert r["added"] == 0 and "inside quotes" in r["hint"]
    assert client.post("/api/market-results/import", json={"text": SHEET}).json()["hint"] == ""


def test_an_import_that_cannot_be_read_says_why(client):
    r = client.post("/api/market-results/import", json={"text": "Department,Date\nPWD,01-10-2026\n"})
    assert r.status_code == 422 and "estimate" in r.json()["detail"]
    assert client.post("/api/market-results/import", json={"text": ""}).status_code == 422


def test_one_result_can_be_typed_in(client):
    r = client.post("/api/market-results", json={"department": "PWD", "work_type": "Roads", "estimate": 1_000_000, "price": 920_000, "awarded": "05-11-2026"})
    assert r.status_code == 200 and r.json()["added"] == 1
    assert client.get("/api/market-results").json()["rows"][0]["awarded"] == "2026-11-05"
    assert client.post("/api/market-results", json={"estimate": 1_000_000, "price": 50_000}).status_code == 422
    assert client.post("/api/market-results", json={"estimate": 1_000_000, "price": 900_000, "awarded": "tomorrow-ish"}).status_code == 422
    assert client.post("/api/market-results", json={"estimate": 0, "price": 900_000}).status_code == 422


def test_results_are_removed_by_id_or_in_bulk(client):
    client.post("/api/market-results/import", json={"text": SHEET})
    rid = client.get("/api/market-results").json()["rows"][0]["id"]
    assert client.delete(f"/api/market-results/{rid}").status_code == 200 and client.delete(f"/api/market-results/{rid}").status_code == 404
    assert client.delete("/api/market-results", params={"source": "nonsense"}).status_code == 422
    assert client.delete("/api/market-results", params={"source": "import"}).json() == {"removed": 2}


def test_the_backtest_says_when_there_is_not_enough_history_yet(client):
    assert client.get("/api/market-results/backtest").json() == {"tested": 0, "needed": 16, "have": 0}


def test_the_backtest_reports_calibration_once_there_is_history(client):
    import random
    rng = random.Random(3)
    lines = ["Department,Estimated Cost,Awarded Amount,Date"]
    for i in range(80):
        dept, mean = rng.choice([("PWD", -9), ("Water", -2)])
        lines.append(f"{dept},1000000,{round(1_000_000 * (1 + rng.gauss(mean, 3) / 100))},{1 + i % 28:02d}-{1 + i // 28:02d}-2026")
    client.post("/api/market-results/import", json={"text": "\n".join(lines)})
    out = client.get("/api/market-results/backtest").json()
    assert out["tested"] == 80 - 15 and out["better_than_plain_pct"] > 10 and 30 <= out["inside_middle_half_pct"] <= 70


def test_only_a_signed_in_bidder_or_above_can_add_results(client):
    fresh = TestClient(client.app)
    assert fresh.get("/api/market-results").status_code == 401
    assert fresh.post("/api/market-results/import", json={"text": SHEET}).status_code == 401
    client.post("/api/users", json={"username": "vik", "name": "Vik", "role": "viewer", "password": "temporary-pass-1"})
    v = TestClient(client.app)
    assert v.post("/api/login", json={"username": "vik", "password": "temporary-pass-1"}).status_code == 200
    assert v.post("/api/account/password", json={"current": "temporary-pass-1", "new": "a better password 42"}).status_code == 200
    assert v.get("/api/market-results").status_code == 200                              # a viewer may look
    assert v.post("/api/market-results/import", json={"text": SHEET}).status_code == 403
    assert v.delete("/api/market-results").status_code == 403


def test_the_price_advisor_learns_from_the_imported_results(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    load_catalogue(app_ctx)
    c.post(f"/api/bids/{bid}/boq", json={"filename": "boq.csv", "file_base64": b64("boq_sample.csv")})
    c.get(f"/api/bids/{bid}/boq", params={"include_review": True})
    before = c.get(f"/api/bids/{bid}/advice").json()
    assert before["samples"] == 0 and before["confidence"] == "none" and before["quantiles"] == {}
    rows = "\n".join(f"PWD,1000000,{950_000 - 1000 * (i % 7)},0{1 + i % 9}-10-2026" for i in range(30))
    assert c.post("/api/market-results/import", json={"text": "Department,Estimated Cost,Awarded Amount,Date\n" + rows}).json()["added"] == 30
    after = c.get(f"/api/bids/{bid}/advice").json()
    assert after["samples"] == 30 and after["confidence"] == "good" and after["market"] == 30 and after["own"] == 0
    assert "30 results" in after["basis"] and "30 imported" in after["basis"]
    assert -7 < after["quantiles"]["p50"] < -4 and after["quantiles"]["p10"] <= after["quantiles"]["p50"] <= after["quantiles"]["p90"]
    assert any(p["win"] is not None for p in after["points"])
