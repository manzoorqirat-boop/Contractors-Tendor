"""Loads fake tenders, bids, vault entries, company profile and facts into a TendAI database, so every page has data without a live run.

Run from anywhere (the script finds the repo by its own location, so keep it in <repo>/testdata/):
    python testdata/seed_demo_data.py --db ./local.db
then start the app with STATE_PATH=./local.db (see testdata/README.md).

USE A THROWAWAY DATABASE. It adds data and never deletes. It refuses to run on a database that already holds tenders that are not
from this script, unless you pass --force. Running it twice on the same test database adds nothing new.
Vault entries get their PDF only if you point --files at the folder make_pdfs.py wrote (or upload the PDFs in the app yourself).
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # the repo root, for tender_digest

from tender_digest import ai                                            # noqa: E402
from tender_digest.bids import BidBook, clean_patch, default_vault_dir  # noqa: E402
from tender_digest.money import MoneyBook  # noqa: E402
from tender_digest.gepnic import Tender                                 # noqa: E402
from tender_digest.letters import Letters                               # noqa: E402
from tender_digest.store import SeenStore                               # noqa: E402
from content import FACTS, PROFILE, TENDERS, VAULT_DOCS                 # noqa: E402

WORKS = [
    ("Construction of BT road from {a} to {b}", "Roads & highways", (4e6, 3e7)), ("Resurfacing of {a}-{b} road", "Roads & highways", (2e6, 2e7)),
    ("Construction of RCC culvert on {a} road", "Bridges & flyovers", (1e6, 9e6)), ("Construction of primary school building at {a}", "Buildings", (3e6, 2.5e7)),
    ("Construction of community hall at {a}", "Buildings", (2e6, 1.2e7)), ("Construction of housing units at {a}", "Housing", (2e7, 9e7)),
    ("Laying of water supply pipeline at {a}", "Water supply", (3e6, 4e7)), ("Construction of underground drainage at {a}", "Drainage & sewerage", (5e6, 6e7)),
    ("Lining of distributary canal at {a}", "Irrigation & canals", (4e6, 5e7)), ("Repairs and maintenance of government quarters at {a}", "Repairs & maintenance", (3e5, 4e6)),
    ("Development of garden and jogging track at {a}", "Landscaping & parks", (1e6, 8e6)), ("Construction of retaining wall at {a}", "Other civil", (1e6, 1.5e7)),
]
PORTALS = {
    "maharashtra": ("PWDM", [("Public Works Department", "Nashik Circle", "Nashik Division"), ("Zilla Parishad Ahmednagar", "Works Division"),
                              ("Water Resources Department", "Pune Irrigation Division"), ("Nashik Municipal Corporation (test)", "Engineering Department")],
                    ["Nashik", "Igatpuri", "Sinnar", "Pune", "Ahmednagar", "Satara"]),
    "delhi": ("DLPWD", [("Delhi PWD (test)", "Division 7"), ("Municipal Corporation (test)", "Zone 2 Engineering")], ["Rohini", "Dwarka", "Karol Bagh", "Shahdara"]),
    "up": ("UPPWD", [("UP PWD", "Provincial Division Lucknow"), ("Nagar Nigam (test)", "Engineering")], ["Lucknow", "Kanpur", "Agra", "Varanasi"]),
    "central": ("CPWD", [("CPWD (test)", "Delhi Central Circle"), ("Border Roads (test)", "Project Office")], ["New Delhi", "Jaipur", "Guwahati"]),
}
PORTAL_OF = {"Maharashtra (MahaTenders)": "maharashtra", "Delhi (Govt. e-Procurement)": "delhi", "Uttar Pradesh (UP eTender)": "up"}
WT = {"T1": "Roads & highways", "T2": "Buildings", "T3": "Drainage & sewerage", "T4": "Repairs & maintenance", "T5": "Bridges & flyovers"}
# Tender IDs this script makes: a six digit number starting with 7, 8 or 9 (real portal IDs seen so far have seven digits).
SEED_ID = re.compile(r"^\d{4}_[A-Z]+_[789]\d{5}_1$")


def parse_when(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.split(",")[0].strip(), "%d-%b-%Y %I:%M %p")
    except ValueError:
        return None


def money(text: str) -> float | None:
    m = re.search(r"Rs\s*([\d,]+)", text)
    return float(m.group(1).replace(",", "")) if m else None


def fit_for(portal: str, loc: str, value: float | None) -> tuple[int, str]:
    score, why = 3, []
    if portal == "maharashtra" and loc in ("Nashik", "Igatpuri", "Sinnar", "Pune", "Ahmednagar"):
        score += 1; why.append("in your districts")
    elif portal != "maharashtra":
        score -= 1; why.append("outside your region")
    if value is None:
        why.append("value not stated")
    elif 2e6 <= value <= 2e7:
        score += 1; why.append("within your size range")
    elif value > 3e7:
        score -= 2; why.append("too large for you")
    return max(1, min(5, score)), ", ".join(why).capitalize()[:80]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="./local.db", help="TendAI database file (the STATE_PATH you will run the app with)")
    ap.add_argument("--vault-dir", default="", help="vault folder (default: 'vault' next to the database, like the app)")
    ap.add_argument("--files", default=str(HERE / "output"), help="folder written by make_pdfs.py; its 2_vault_documents PDFs are attached to vault entries if found")
    ap.add_argument("--count", type=int, default=36, help="how many filler tenders to add")
    ap.add_argument("--force", action="store_true", help="run even if the database already holds tenders that are not from this script")
    a = ap.parse_args()

    store = SeenStore(a.db)
    others = [r[0] for r in store.db.execute("SELECT tender_id FROM seen WHERE tender_id IS NOT NULL").fetchall() if not SEED_ID.match(r[0] or "")]
    if others and not a.force:
        sys.exit(f"Stopped: {a.db} already holds {len(others)} tender(s) that are not test data (for example {others[0]}). "
                 "Use a fresh database file, or add --force if you really want fake tenders mixed in.")

    now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None, microsecond=0)
    book = BidBook(store, a.vault_dir or default_vault_dir(a.db))
    cash = MoneyBook(book)
    day = lambda n: str((now + timedelta(days=n)).date())  # noqa: E731
    rnd = random.Random(2026)
    kept: list[tuple[Tender, str]] = []

    # 1. The five tenders that have a PDF (same IDs and dates as the documents).
    for i, d in enumerate(TENDERS, 1):
        dates = dict(d["dates"])
        t = Tender(portal=PORTAL_OF[d["portal"]], tender_id=d["id"], title=d["title"], reference=d["ref"], organisation="||".join(x.strip() for x in d["org"].split(",")),
                   published=now - timedelta(days=1), closing=parse_when(dates["Document sale end / bid submission end"]), opening=parse_when(dates["Technical bid opening"]),
                   description=d["title"], category="Works", value=money(d["value"]), location=d["place"].split(",")[0], emd=money(d["emd"]))
        store.keep(t, now)
        kept.append((t, f"T{i}"))

    # 2. Filler tenders across the four portals, with a spread of values and dates, and a few without a value.
    for n in range(a.count):
        pkey = rnd.choice(list(PORTALS)); code, orgs, locs = PORTALS[pkey]
        title, wtype, (lo, hi) = rnd.choice(WORKS)
        loc, org = rnd.choice(locs), rnd.choice(orgs)
        value = None if n % 11 == 5 else round(rnd.uniform(lo, hi), -3)
        pub = now - timedelta(days=rnd.randint(0, 12), hours=rnd.randint(0, 20))
        close = now + timedelta(days=rnd.choice([-4, -1, 1, 2, 3, 5, 8, 12, 17, 24, 30]), hours=rnd.randint(-5, 5))
        t = Tender(portal=pkey, tender_id=f"2026_{code}_{800000 + n}_1", title=title.format(a=loc, b=rnd.choice(locs)), reference=f"REF/{code}/2026-27/{n + 1}",
                   organisation="||".join(org), published=pub, closing=close, opening=close + timedelta(days=2), description=title.format(a=loc, b=loc),
                   category="Works", value=value, location=loc, emd=round(value * 0.02, -2) if value else None)
        store.keep(t, now)
        kept.append((t, wtype))

    # 3. Company profile and facts, then fake AI labels (so Work type and Fit columns fill in without an API key).
    store.put_settings({"company_profile": PROFILE, "company_facts": [{"label": k, "value": v} for k, v in FACTS]})
    ph = ai.profile_hash(PROFILE)
    for t, wt in kept:
        fit, why = fit_for(t.portal, t.location, t.value)
        store.ai_save([(t.key, {"work_type": WT.get(wt, wt), "civil": True, "fit": fit, "reason": why})], ph, False)

    # 4. Two tenders that are probably not civil, so the "May not be civil" tag shows up.
    for n, title in enumerate(["Supply and installation of LED street lights", "Annual maintenance of air conditioners and UPS"]):
        t = Tender(portal="delhi", tender_id=f"2026_DLPWD_{899000 + n}_1", title=title, reference=f"REF/DL/NC/{n}", organisation="Delhi PWD (test)||Electrical Division",
                   published=now - timedelta(days=2), closing=now + timedelta(days=9), opening=now + timedelta(days=11), description=title, category="Works", value=2.4e6, location="Rohini", emd=48000.0)
        store.keep(t, now)
        store.ai_save([(t.key, {"work_type": "Other civil", "civil": False, "fit": 1, "reason": "Mainly electrical supply, not civil work"})], ph, False)

    # 5. Vault entries, with the PDF attached when --files has it. Names that already exist are skipped.
    have = {d["name"] for d in book.vault_list()}
    files_dir = Path(a.files) / "2_vault_documents"
    added = attached = 0
    for fn, name, kind, issued, expiry, notes, _title, _body in VAULT_DOCS:
        if name in have:
            continue
        pdf = files_dir / f"{fn}.pdf"
        data = pdf.read_bytes() if pdf.is_file() else None
        book.vault_add({"name": name, "kind": kind, "issued": issued, "expiry": expiry, "notes": notes}, data, pdf.name, now)
        added += 1
        attached += bool(data)

    # 6. Bids: T1 (document already "read", so the checklist and vault matching work offline), T4 preparing, and finished ones for Results.
    by = {tag: t for t, tag in kept if tag.startswith("T")}
    for tag, stage in (("T1", "interested"), ("T4", "preparing")):
        bid, _ = book.create_from_tender(by[tag].key, now)
        d = TENDERS[int(tag[1]) - 1]
        doc = {"summary": f"{d['title']}. Estimated {d['value'].split(' (')[0]}.", "requirements": d["elig"], "documents": d["docs"],
               "financial": [{"label": "Estimated cost", "value": d["value"].split(" (")[0]}, {"label": "EMD", "value": d["emd"].split(",")[0]}, {"label": "Bid validity", "value": d["validity"]}],
               "dates": [{"label": k, "value": v} for k, v in d["dates"]], "penalties": [x for x in d["terms"] if "damages" in x.lower()], "red_flags": []}
        book.set_json(bid["id"], "doc", doc, now)
        patch = {"stage": stage, "owner": "Test user", "emd_due": str(by[tag].closing.date() - timedelta(days=2))}
        if tag == "T1":
            patch.update(prebid_date="2026-11-19", query_deadline="2026-11-21")
        book.update(bid["id"], clean_patch(patch), now)
        book.generate_items(bid["id"], now)
        if tag == "T1":      # an EMD still to be paid, and a fee that is paid and gone
            cash.add(bid["id"], {"kind": "emd", "amount": by[tag].emd or 50000, "instrument": "online", "status": "planned"}, now)
            cash.add(bid["id"], {"kind": "tender_fee", "amount": 1180, "instrument": "online", "status": "paid", "paid_on": day(-1), "reference": "UTR 4021998877"}, now)
        else:                # an EMD held as a bank guarantee that runs out soon
            cash.add(bid["id"], {"kind": "emd", "amount": by[tag].emd or 50000, "instrument": "bg", "status": "paid", "paid_on": day(-6), "reference": "BG/2026/0412", "bank": "State Bank, Nashik", "valid_until": day(20)}, now)
    past = [("Road widening, Sinnar", "Roads & highways", 4.1e7, 3.7e7, 3.62e7, "Test Roadways (fictional)", "lost"), ("Drain at Ward 3, Satpur", "Drainage & sewerage", 1.2e7, 1.05e7, 1.05e7, "", "won"),
            ("School repairs, Igatpuri", "Repairs & maintenance", 4.5e6, 4.1e6, 3.9e6, "Test Builders (fictional)", "lost"), ("Culvert on Peth road", "Bridges & flyovers", 6e6, 5.4e6, 5.4e6, "", "won"),
            ("BT resurfacing, Dindori", "Roads & highways", 1.6e7, 1.46e7, 1.41e7, "Test Infra (fictional)", "lost"), ("Community hall, Trimbak", "Buildings", 8e6, 7.3e6, 7.3e6, "", "won")]
    for n, (title, wtype, est, mine, l1, who, stage) in enumerate(past):
        t = Tender(portal="maharashtra", tender_id=f"2026_PWDM_{700000 + n}_1", title=title, reference=f"OLD/{n}", organisation="Public Works Department||Nashik Circle||Nashik Division",
                   published=now - timedelta(days=95 + n * 9), closing=now - timedelta(days=70 + n * 9), opening=now - timedelta(days=68 + n * 9), description=title, category="Works", value=est,
                   location="Nashik", emd=round(est * 0.02, -2))
        store.keep(t, now)
        store.ai_save([(t.key, {"work_type": wtype, "civil": True, "fit": 4, "reason": "Past work"})], ph, False)
        bid, _ = book.create_from_tender(t.key, now)
        book.update(bid["id"], clean_patch({"stage": stage, "my_value": mine, "l1_value": l1, "l1_bidder": who or "Sahyadri Infra Works (us)", "result_note": "Seeded test result"}), now)
        if n == 0:           # a lost bid whose EMD was due back weeks ago
            cash.add(bid["id"], {"kind": "emd", "amount": round(est * 0.02, -2), "instrument": "dd", "status": "paid", "paid_on": day(-80), "refund_due": day(-25), "reference": "DD 556610", "bank": "Bank of Maharashtra"}, now)
        if n == 1:           # a won bid: EMD still held, and the performance security as a guarantee
            cash.add(bid["id"], {"kind": "emd", "amount": round(est * 0.02, -2), "instrument": "online", "status": "paid", "paid_on": day(-78)}, now)
            cash.add(bid["id"], {"kind": "performance_security", "amount": round(mine * 0.05, -2), "instrument": "bg", "status": "paid", "paid_on": day(-60), "valid_until": day(300), "bank": "State Bank, Nashik"}, now)

    letters = Letters(store)
    if not letters.list():
        letters.create("Covering letter", "To,\nThe {{organisation}}\n\nSubject: Bid for \"{{title}}\" (Tender ID {{tender_id}})\n\nDear Sir,\n\nWe submit our bid for the above work, which closes on {{closing}}.\n\nYours faithfully,\n{{today}}", "demo", now)
        letters.create("Request for extension", "To,\nThe {{organisation}}\n\nSubject: Request to extend the closing date of Tender ID {{tender_id}}\n\nDear Sir,\n\nWe request a short extension of the closing date ({{closing}}) so that we can complete our submission.\n\n{{today}}", "demo", now)

    print(f"Database: {a.db}\nTenders in list: {store.count()}  |  vault entries added: {added} ({attached} with a file)  |  bids: {len(book.list())}")
    if added and not attached:
        print("Vault entries have no files. Upload the PDFs from 2_vault_documents in the Vault page, or re-run with --files pointing at make_pdfs.py output.")


if __name__ == "__main__":
    main()
