"""Who usually wins tenders like this one: from your bidder lists and from imported awards that name the winner."""

from tender_digest import market, rivals
from tender_digest.bids import BidBook
from tests.test_awards import bidders, open_bid
from tests.test_bids import app_ctx  # noqa: F401


def row(name, dept="pwd", wt="Roads", est=1_000_000, price=900_000, source="market", mine=False, when="2026-10-01"):
    return {"name": name, "dept": dept, "work_type": wt, "estimate": est, "price": price, "when": when, "source": source, "mine": mine}


KEY = BidBook.company_key


# ---- the ranking -------------------------------------------------------------------------------------------------------

def test_winners_are_counted_with_their_share_and_usual_price_and_spellings_are_merged():
    rows = [row("M/s Rao Infra Pvt. Ltd.", price=880_000), row("RAO INFRA PRIVATE LIMITED", price=920_000, when="2026-11-05"), row("rao infra", price=900_000),
            row("Shah & Sons", price=950_000), row("Patil Builders", price=1_020_000)]
    out = rivals.who_wins(rows, KEY, "Roads", "PWD")
    assert out["total"] == 5 and out["basis"] == "Roads awards in this department"
    by = {r["key"]: r for r in out["rows"]}
    rao = out["rows"][0]
    assert (rao["key"], rao["wins"], rao["share_pct"], rao["usual_vs_estimate"], rao["priced"], rao["last"]) == ("rao infra", 3, 60, -10.0, 3, "2026-11-05")
    assert by["shah sons"]["wins"] == 1 and by["shah sons"]["usual_vs_estimate"] == -5.0 and by["patil builders"]["usual_vs_estimate"] == 2.0
    assert [r["name"] for r in out["rows"][1:]] == ["Patil Builders", "Shah & Sons"]            # equal wins: alphabetical


def test_the_group_is_as_specific_as_the_history_allows():
    deep = [row(f"A{i}") for i in range(5)] + [row(f"B{i}", dept="water") for i in range(6)] + [row(f"C{i}", wt="Buildings") for i in range(7)]
    assert rivals.who_wins(deep, KEY, "Roads", "PWD")["basis"] == "Roads awards in this department" and rivals.who_wins(deep, KEY, "Roads", "PWD")["total"] == 5
    assert rivals.who_wins(deep, KEY, "Bridges", "water")["basis"] == "awards in this department" and rivals.who_wins(deep, KEY, "Bridges", "water")["total"] == 6
    assert rivals.who_wins(deep, KEY, "Buildings", "Nowhere")["basis"] == "Buildings awards" and rivals.who_wins(deep, KEY, "Buildings", "Nowhere")["total"] == 7
    assert rivals.who_wins(deep, KEY, "Bridges", "Nowhere")["basis"] == "all awards on record" and rivals.who_wins(deep, KEY, None, None)["total"] == 18


def test_too_few_results_show_nothing_rather_than_a_pattern():
    out = rivals.who_wins([row("A"), row("B")], KEY, "Roads", "PWD")
    assert out["rows"] == [] and out["total"] == 2 and rivals.who_wins([], KEY, "Roads", "PWD")["total"] == 0


def test_concentration_is_reported_only_with_enough_awards():
    few = [row("A"), row("A"), row("B"), row("C")]
    assert rivals.who_wins(few, KEY, "Roads", "PWD")["top3_share_pct"] is None
    many = [row("A")] * 4 + [row("B")] * 3 + [row("C")] * 2 + [row("D"), row("E"), row("F"), row("G")]
    out = rivals.who_wins(many, KEY, "Roads", "PWD")
    assert out["total"] == 13 and out["top3_share_pct"] == 69                      # 9 of 13
    assert rivals.who_wins([row("A")] * 5 + [row("B")] * 3 + [row("C")] * 2, KEY, "Roads", "PWD")["top3_share_pct"] is None     # only three winners: nothing to say about concentration


def test_a_winner_with_no_price_is_counted_without_a_made_up_discount():
    out = rivals.who_wins([row("A", price=None), row("A", price=None), row("A", price=None)], KEY, "Roads", "PWD")
    assert out["rows"][0]["wins"] == 3 and out["rows"][0]["usual_vs_estimate"] is None and out["rows"][0]["priced"] == 0
    odd = rivals.who_wins([row("A", price=50), row("A", price=10_000_000), row("A", price=900_000)], KEY, "Roads", "PWD")["rows"][0]
    assert odd["priced"] == 1 and odd["usual_vs_estimate"] == -10.0                 # a typing error in either direction does not bend the figure


def test_your_own_wins_are_marked_and_head_to_head_is_attached():
    rows = [row("Us Pvt Ltd", mine=True, source="own")] * 3 + [row("Rao")] * 3
    out = rivals.who_wins(rows, KEY, "Roads", "PWD", {"rao": {"met": 5, "ahead": 2}})
    mine = next(r for r in out["rows"] if r["mine"])
    rao = next(r for r in out["rows"] if r["key"] == "rao")
    assert mine["name"] == "Us Pvt Ltd" and (rao["met"], rao["ahead"]) == (5, 2) and mine["met"] == 0
    assert out["own"] == 3 and out["market"] == 3


# ---- importing the winner ----------------------------------------------------------------------------------------------

SHEET = """Tender ID,Department,Work type,Estimated cost,Awarded amount,Awarded to,Award date
2026_A_1,PWD Nashik,Roads,"50,00,000","46,00,000","M/s Rao Infra Pvt. Ltd.",05-11-2026
2026_A_2,PWD Nashik,Roads,"1,00,00,000","95,00,000",Shah & Sons,10-10-2026
2026_A_3,PWD Nashik,Roads,"20,00,000","19,00,000",,01-10-2026
"""


def test_a_winner_column_is_found_whatever_it_is_called():
    for header in ("Awarded to", "Winner", "Successful Bidder", "Name of Contractor", "L1 Bidder", "Awardee"):
        good, bad, info = market.parse_table(SHEET.replace("Awarded to", header))
        assert bad == [] and good[0]["winner"] == "M/s Rao Infra Pvt. Ltd." and info["columns"]["winner"] == header, header
    assert [g["winner"] for g in market.parse_table(SHEET)[0]] == ["M/s Rao Infra Pvt. Ltd.", "Shah & Sons", ""]


def test_a_department_column_called_agency_is_still_not_mistaken_for_the_winner():
    good, _, info = market.parse_table("Department,Estimated Cost,Awarded Amount\nPWD,1000000,900000\n")
    assert good[0]["winner"] == "" and "winner" not in info["columns"]


def test_only_imported_results_that_name_a_winner_count_and_a_tender_you_bid_on_counts_once(app_ctx):
    c = app_ctx.client
    bid, tender = open_bid(app_ctx)
    c.put(f"/api/bids/{bid}/bidders", json={"bidders": bidders(("Rao Infra", 18_000_000, 1, "qualified", False), ("Us", 19_000_000, 2, "qualified", True))})
    pool = market.MarketBook(BidBook(app_ctx.store))
    good = market.parse_table(SHEET)[0]
    good.append({"department": "PWD", "work_type": "Roads", "location": "", "estimate": 2e7, "price": 1.8e7, "bidders": None, "awarded": None, "winner": "Rao Infra", "ref": "id:" + tender.tender_id.lower()})
    pool.add_rows(good, "import", market.datetime.now())
    names = sorted(r["name"] for r in pool.winner_rows())
    assert names == ["M/s Rao Infra Pvt. Ltd.", "Rao Infra", "Shah & Sons"]       # the unnamed winner is skipped; the imported copy of the tender you bid on is not counted again


def test_an_old_database_gains_the_winner_column(tmp_path):
    import sqlite3

    from tender_digest.store import SeenStore
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE market_results (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ref TEXT NOT NULL UNIQUE, department TEXT NOT NULL DEFAULT '', work_type TEXT NOT NULL DEFAULT '', "
                "location TEXT NOT NULL DEFAULT '', estimate REAL NOT NULL, price REAL NOT NULL, bidders INTEGER, awarded TEXT, added TEXT NOT NULL)")
    old.execute("INSERT INTO market_results (source, ref, estimate, price, added) VALUES ('import', 'h:1', 100, 90, '2026-01-01')")
    old.commit()
    old.close()
    store = SeenStore(path)
    book = market.MarketBook(BidBook(store))
    assert book.summary()["imported"] == 1 and book.winner_rows() == []             # kept, with no winner named
    store.close()


# ---- head to head ------------------------------------------------------------------------------------------------------

def test_head_to_head_counts_the_tenders_you_both_bid_on_and_who_finished_ahead(app_ctx):
    c = app_ctx.client
    ids = [open_bid(app_ctx, n)[0] for n in (1, 2, 3, 4)]
    c.put(f"/api/bids/{ids[0]}/bidders", json={"bidders": bidders(("Rao Infra", 18e6, 1, "qualified", False), ("Us", 19e6, 2, "qualified", True))})
    c.put(f"/api/bids/{ids[1]}/bidders", json={"bidders": bidders(("RAO INFRA PRIVATE LIMITED", 19e6, 2, "qualified", False), ("Us", 18e6, 1, "qualified", True))})
    c.put(f"/api/bids/{ids[2]}/bidders", json={"bidders": bidders(("Rao Infra", 18e6, None, "qualified", False), ("Us", 17e6, None, "qualified", True))})   # no ranks: the lower price is ahead
    c.put(f"/api/bids/{ids[3]}/bidders", json={"bidders": bidders(("Rao Infra", None, None, "x", False), ("Us", None, None, "x", True))})                       # nothing to compare
    hh = BidBook(app_ctx.store).head_to_head()
    assert hh == {"rao infra": {"met": 3, "ahead": 2}}


# ---- the endpoint ------------------------------------------------------------------------------------------------------

def test_the_rivals_endpoint_ranks_winners_for_this_bids_department_and_work(app_ctx):
    c = app_ctx.client
    bid, _ = open_bid(app_ctx)
    lines = ["Department,Work type,Estimated cost,Awarded amount,Awarded to,Award date"]
    lines += [f"PWD,Construction,1000000,{900_000 + 1000 * i},Rao Infra,0{1 + i % 9}-10-2026" for i in range(4)]
    lines += [f"PWD,Construction,1000000,950000,Shah & Sons,0{1 + i}-09-2026" for i in range(3)]
    assert c.post("/api/market-results/import", json={"text": "\n".join(lines)}).json()["added"] == 7
    out = c.get(f"/api/bids/{bid}/rivals").json()
    assert out["total"] >= 7 and out["rows"][0]["key"] == "rao infra" and out["rows"][0]["wins"] == 4 and out["rows"][0]["usual_vs_estimate"] is not None
    assert c.get("/api/bids/9999/rivals").status_code == 404
    c.post("/api/logout")
    assert c.get(f"/api/bids/{bid}/rivals").status_code == 401
