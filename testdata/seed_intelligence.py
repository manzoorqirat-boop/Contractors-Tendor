"""Loads fake HISTORY so the data-driven features can be tried now instead of after months of use:

  * 80 decided bids with winners and rivals          -> Win chance, Who usually wins here, the Results page
  * 120 imported award results with winners          -> Price history and its backtest, Who usually wins here
  * 14 months of daily collection counts and runs    -> What to expect (forecast, and "likely to come round again")
  * 5 open bids with EMDs, and an EMD already paid   -> Which bids to submit
  * portal reads that failed or found nothing        -> the portal health banner; a proven and a failed coverage test

Everything is fictional and made up from a fixed random seed, so it is the same every time. USE A THROWAWAY DATABASE: this adds a lot of data and never deletes.
It stops if it has already been run on the database unless you pass --force. Never point it at a real database.

    python testdata/seed_demo_data.py --db ./local.db        # optional: the tenders, vault and a few bids
    python testdata/seed_intelligence.py --db ./local.db
    APP_PASSWORD=choose-a-password COOKIE_SECURE=false STATE_PATH=./local.db python -m tender_digest.web
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # the repo root, for tender_digest

from tender_digest.bids import BidBook
from tender_digest.market import MarketBook
from tender_digest.money import MoneyBook
from tender_digest.store import SeenStore

US = "Sahyadri Infra Works"
RIVALS = [("M/s Rao Infra Pvt. Ltd.", 0.34), ("Shah & Sons", 0.22), ("Patil Builders", 0.16), ("Deshmukh Constructions", 0.12), ("Kulkarni Roadways", 0.09), ("Jadhav Engineers", 0.07)]
# department, win rate for us, usual L1 against the estimate (%)
DEPTS = [("Public Works Department||Nashik Division", 0.85, -9.0), ("Water Resources Department||Pune Division", 0.08, -2.0), ("Rural Development||Zilla Parishad Nashik", 0.40, 3.0)]
WORK = ["Roads & highways", "Buildings", "Drainage & sewerage", "Water supply", "Bridges & flyovers"]
TITLES = {"Roads & highways": "Widening and strengthening of road", "Buildings": "Construction of building", "Drainage & sewerage": "Construction of storm water drain",
          "Water supply": "Laying of water supply pipeline", "Bridges & flyovers": "Repair of culvert and bridge"}


def pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    r, run = rng.random() * sum(w for _, w in weighted), 0.0
    for name, w in weighted:
        run += w
        if r <= run:
            return name
    return weighted[-1][0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Load fake history into a throwaway TendAI database")
    p.add_argument("--db", required=True, help="the database file (created if missing)")
    p.add_argument("--force", action="store_true", help="run even if it was run before")
    a = p.parse_args(argv)
    rng = random.Random(2026)
    now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None, microsecond=0)
    today = now.date()
    store = SeenStore(a.db)
    book = BidBook(store)
    cash, market = MoneyBook(book), MarketBook(book)
    if store.get_settings().get("demo_intelligence") and not a.force:
        print("This database already has the history from this script. Use a fresh one, or pass --force to add it again.")
        return 1

    # 1. Eighty decided bids, oldest first, with who bid and who won
    n_bids = 80
    for i in range(n_bids):
        dept, p_win, usual = DEPTS[rng.randrange(len(DEPTS))]
        work = WORK[rng.randrange(len(WORK))]
        value = rng.choice([6e5, 2.5e6, 6e6, 1.4e7, 4.5e7])
        won = rng.random() < p_win
        when = (now - timedelta(days=5 * (n_bids - i))).replace(hour=11, minute=0)
        l1 = round(value * (1 + rng.gauss(usual, 3) / 100))
        mine = round(l1 if won else l1 * (1 + rng.uniform(0.01, 0.06)))
        key = f"demo:H{i}"
        store.db.execute(
            "INSERT INTO bids (key, tender_id, portal, title, description, organisation, work_type, value, emd, stage, my_value, l1_value, l1_bidder, created, updated, stage_changed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, f"H{i}", "maharashtra", f"{TITLES[work]} {i}", f"{TITLES[work]} at village {i}", dept, work, value, round(value * 0.02, -2), "won" if won else "lost", mine, l1,
             US if won else "", when.isoformat(), when.isoformat(), when.isoformat()))
        bid_id = store.db.execute("SELECT id FROM bids WHERE key = ?", (key,)).fetchone()[0]
        rival = pick(rng, RIVALS)
        others = [r for r, _ in RIVALS if r != rival][:2]
        rows = ([{"name": US, "price": mine, "rank": 1, "status": "qualified", "mine": True}, {"name": rival, "price": round(l1 * 1.03), "rank": 2, "status": "qualified", "mine": False}]
                if won else
                [{"name": rival, "price": l1, "rank": 1, "status": "qualified", "mine": False}, {"name": US, "price": mine, "rank": 2, "status": "qualified", "mine": True},
                 {"name": others[0], "price": round(mine * 1.02), "rank": 3, "status": "qualified", "mine": False}])
        book.set_bidders(bid_id, rows, when)
    store.db.commit()

    # 2. A pool of 120 award results from the last 18 months, naming the winner
    pool = []
    for i in range(120):
        dept, _, usual = DEPTS[rng.randrange(len(DEPTS))]
        est = rng.choice([8e5, 3e6, 8e6, 2e7, 5e7])
        day = (today - timedelta(days=rng.randrange(8, 540))).isoformat()
        pool.append({"department": dept.split("||")[0], "work_type": WORK[rng.randrange(len(WORK))], "location": "Nashik", "estimate": est, "price": round(est * (1 + rng.gauss(usual, 3.5) / 100)),
                     "bidders": rng.randint(2, 9), "awarded": day, "winner": pick(rng, RIVALS), "ref": f"id:M{i}"})
    imported = market.add_rows(pool, "import", now)["added"]

    # 3. Fourteen months of daily collection: a run each day, counts per department with a busy February to March, and a short memory of what was tendered
    for d in range(430, 0, -1):
        day = today - timedelta(days=d)
        store.db.execute("INSERT INTO runs (trigger, status, started) VALUES ('schedule', 'ok', ?)", (day.isoformat() + "T08:00:00",))
        busy = 1.6 if day.month in (2, 3) else 1.0
        for dept, base in (("Public Works Department", 4), ("Water Resources Department", 3), ("Rural Development", 2)):
            n = max(0, round(rng.gauss(base * busy, 1.2)))
            if n:
                store.db.execute("INSERT INTO flow (day, portal, dept, n) VALUES (?, 'maharashtra', ?, ?) ON CONFLICT(day, portal, dept) DO UPDATE SET n = n + excluded.n", (day.isoformat(), dept, n))
        if d in (352, 340, 330):   # tendered about this time last year, and nothing like it since
            store.db.execute("INSERT OR IGNORE INTO memory (key, day, portal, dept, title, value) VALUES (?, ?, 'maharashtra', 'Public Works Department', ?, ?)",
                             (f"demo:R{d}", day.isoformat(), ("Annual maintenance of rural roads, Nashik taluka", "Annual repair of government buildings, Nashik", "Desilting of nalas before monsoon, Nashik city")[(352, 340, 330).index(d)], 9e6))
    store.db.commit()

    # 4. Five open bids with EMDs (one with none on record), and some EMD cash already locked
    open_ids = []
    for i, (title, value, emd, days, stage) in enumerate([("Widening of road, Sinnar", 4.2e7, 425000, 9, "preparing"), ("Drainage works, Satpur", 1.2e7, 120000, 5, "interested"),
                                                          ("School building, Igatpuri", 2.8e7, 280000, 14, "preparing"), ("Bridge repair, Girna", 8e6, None, 20, "interested"),
                                                          ("Water tank, Niphad", 6e7, 900000, 11, "preparing")]):
        key = f"demo:O{i}"
        store.db.execute(
            "INSERT INTO bids (key, tender_id, portal, title, description, organisation, work_type, value, emd, closing, stage, created, updated, stage_changed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, f"O{i}", "maharashtra", title, title, DEPTS[i % 3][0], WORK[i % len(WORK)], value, emd, (now + timedelta(days=days)).isoformat(timespec="seconds"), stage, now.isoformat(), now.isoformat(), now.isoformat()))
        open_ids.append(store.db.execute("SELECT id FROM bids WHERE key = ?", (key,)).fetchone()[0])
    store.db.commit()
    cash.add(open_ids[0], {"kind": "emd", "amount": 200000, "instrument": "online", "status": "paid", "paid_on": (today - timedelta(days=3)).isoformat(), "refund_due": (today + timedelta(days=40)).isoformat()}, now)

    # 5. Portal trouble: one that keeps failing after working, one that answers but lists nothing, a healthy proven one, one that needs a captcha
    for d in range(6, 1, -1):
        for k in ("maharashtra", "delhi", "up"):
            store.put_portal_status(k, "ok", "", now - timedelta(days=d), 9, 90)
    store.put_portal_status("maharashtra", "unreachable", "timed out", now - timedelta(days=1), 0, 0)
    store.put_portal_status("maharashtra", "unreachable", "timed out", now, 0, 0)
    store.put_portal_status("delhi", "ok", "", now - timedelta(days=1), 9, 0)
    store.put_portal_status("delhi", "ok", "", now, 9, 0)
    store.put_portal_status("up", "ok", "", now, 9, 90)
    proven = {"verdict": "proven", "kind": "ok", "detail": "", "departments": 9, "advertised": 120, "department": "PWD", "listed": 40, "with_id_pct": 100, "with_closing_pct": 100,
              "with_value_pct": 66, "details_read": 3, "details_tried": 3, "seconds": 4.2}
    store.put_portal_coverage("up", proven, now)
    store.put_portal_coverage("central", {**proven, "verdict": "failed", "kind": "captcha", "detail": "the portal asks for a captcha", "listed": 0, "department": ""}, now)
    store.put_portal_status("central", "captcha", "the portal asks for a captcha", now, 0, 0, "check")

    store.put_settings({"demo_intelligence": True})
    store.close()
    print(f"Database: {a.db}\n  {n_bids} decided bids, {imported} imported award results, 430 days of collection history, {len(open_ids)} open bids, portal problems on Maharashtra and Delhi.\n"
          "  Look at: Bids > Results (win chance, price history), a bid's Decide tab (win chance, who usually wins here), Bids > Pipeline (which bids to submit),\n"
          "  Dashboard (what to expect, portal health banner), Settings > Portals (health column, coverage proof).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
