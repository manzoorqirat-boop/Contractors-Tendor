"""Builds the test files from content.py: tender PDFs (+ .txt), vault papers (+ vault_index.csv), company profile/facts, edge cases.

    pip install reportlab
    python testdata/make_pdfs.py              # writes into testdata/output/
    python testdata/make_pdfs.py --out ./files

Every page carries a TEST DATA footer. You do not need this file to run the seed script.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError:
    sys.exit("This script needs reportlab: pip install reportlab")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content as C

FOOTER = "TEST DATA - fictional document created only for software testing. Not a real tender, certificate or legal paper."
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontSize=15, leading=19, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11.5, leading=14, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9.6, leading=13)
SMALL = ParagraphStyle("S", parent=BODY, fontSize=8.4, leading=11, textColor=colors.HexColor("#444444"))
CELL = ParagraphStyle("C", parent=BODY, fontSize=9, leading=11.5)
CELLB = ParagraphStyle("CB", parent=CELL, fontName="Helvetica-Bold")


def _page(watermark: bool):
    def draw(c, doc):
        c.saveState()
        if watermark:
            c.setFillColor(colors.Color(0.85, 0.85, 0.85, alpha=0.45))
            c.setFont("Helvetica-Bold", 78)
            c.translate(A4[0] / 2, A4[1] / 2)
            c.rotate(40)
            c.drawCentredString(0, 0, "TEST DATA")
            c.restoreState(); c.saveState()
        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor("#8a1c1c"))
        c.drawCentredString(A4[0] / 2, 10 * mm, FOOTER)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawRightString(A4[0] - 15 * mm, 15 * mm, f"Page {doc.page}")
        c.restoreState()
    return draw


def build(path, story, title, watermark=False):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=22 * mm, title=title, author="TendAI test data")
    doc.build(story, onFirstPage=_page(watermark), onLaterPages=_page(watermark))


_GRID = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")), ("VALIGN", (0, 0), (-1, -1), "TOP")]


def kv_table(rows, w=(62 * mm, 112 * mm)):
    t = Table([[Paragraph(str(k), CELLB), Paragraph(str(v), CELL)] for k, v in rows], colWidths=w)
    t.setStyle(TableStyle(_GRID + [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f1f1")), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def grid(header, rows, widths):
    t = Table([[Paragraph(h, CELLB) for h in header]] + [[Paragraph(str(c), CELL) for c in r] for r in rows], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(_GRID + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6"))]))
    return t


def bullets(items):
    return [Paragraph(f"{i}. {x}", BODY) for i, x in enumerate(items, 1)]


def tender_story(t):
    s = [Paragraph("NOTICE INVITING TENDER (NIT)", H1), Paragraph(t["org"], BODY), Spacer(1, 4 * mm)]
    s.append(kv_table([("Tender ID", t["id"]), ("Tender reference", t["ref"]), ("Portal", t["portal"]), ("Name of work", t["title"]), ("Location", t["place"]),
                       ("Estimated cost", t["value"]), ("EMD / bid security", t["emd"]), ("Fees", t["fee"]), ("Period of completion", t["period"]),
                       ("Defect liability period", t["dlp"]), ("Bid validity", t["validity"]), ("Contractor class required", t["cls"]), ("Number of covers", "2 (Technical and Financial)")]))
    s += [Paragraph("1. Scope of work", H2), Paragraph(t["scope"], BODY), Paragraph("2. Critical dates", H2), grid(["Event", "Date and time"], t["dates"], (80 * mm, 94 * mm))]
    s += [Paragraph("3. Eligibility and qualification criteria", H2)] + bullets(t["elig"])
    s += [Paragraph("4. Documents to be submitted online (Cover 1: Technical)", H2)] + bullets(t["docs"])
    s += [Paragraph("5. Contract conditions", H2)] + bullets(t["terms"])
    n = 6
    if t.get("annex"):
        s += [Paragraph(f"{n}. {t['annex'][0]}", H2), Paragraph(t["annex"][1], BODY)]
        n += 1
    s += [Paragraph(f"{n}. General", H2), Paragraph("Bids must be submitted online on the portal using a valid digital signature certificate. Bids received after the closing time will not be considered. "
                                                    "The Competent Authority reserves the right to accept or reject any bid. Bidders should verify all dates on the portal before bidding, since corrigenda may change them.", BODY),
          Spacer(1, 8 * mm), Paragraph("Executive Engineer (test signatory)<br/>" + t["org"], SMALL)]
    return s


def tender_text(t):
    L = ["NOTICE INVITING TENDER (NIT)", t["org"], "", f"Tender ID: {t['id']}", f"Tender reference: {t['ref']}", f"Portal: {t['portal']}", f"Name of work: {t['title']}", f"Location: {t['place']}",
         f"Estimated cost: {t['value']}", f"EMD / bid security: {t['emd']}", f"Fees: {t['fee']}", f"Period of completion: {t['period']}", f"Defect liability period: {t['dlp']}",
         f"Bid validity: {t['validity']}", f"Contractor class required: {t['cls']}", "", "1. Scope of work", t["scope"], "", "2. Critical dates"]
    L += [f"- {a}: {b}" for a, b in t["dates"]] + ["", "3. Eligibility and qualification criteria"] + [f"{i}. {x}" for i, x in enumerate(t["elig"], 1)]
    L += ["", "4. Documents to be submitted online (Cover 1: Technical)"] + [f"{i}. {x}" for i, x in enumerate(t["docs"], 1)]
    L += ["", "5. Contract conditions"] + [f"{i}. {x}" for i, x in enumerate(t["terms"], 1)]
    if t.get("annex"):
        L += ["", f"6. {t['annex'][0]}", t["annex"][1]]
    L += ["", "TEST DATA - fictional document created only for software testing."]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "output"), help="where to write the files")
    out = Path(ap.parse_args().out)
    d1, d2, d3, d4 = (out / n for n in ("1_tender_documents", "2_vault_documents", "3_company", "4_edge_cases"))
    for d in (d1, d2, d3, d4):
        d.mkdir(parents=True, exist_ok=True)

    for t in C.TENDERS:
        build(d1 / f"{t['file']}.pdf", tender_story(t), t["title"])
        (d1 / f"{t['file']}.txt").write_text(tender_text(t), encoding="utf-8")

    with open(d2 / "vault_index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["name", "kind", "issued", "expiry", "notes", "file"])
        for fn, name, kind, issued, expiry, notes, title, body in C.VAULT_DOCS:
            if isinstance(body, dict):
                widths = [x * mm for x in body["widths_mm"]]
                table = grid(body["header"], body["rows"], widths)
            else:
                table = kv_table(body)
            build(d2 / f"{fn}.pdf", [Paragraph(title, H1), Paragraph(C.CO, BODY), Spacer(1, 5 * mm), table, Spacer(1, 8 * mm),
                                     Paragraph("This is a specimen page generated for testing. All names, numbers and addresses are fictional.", SMALL)], title, watermark=True)
            w.writerow([name, kind, issued, expiry, notes, f"{fn}.pdf"])

    (d3 / "company_profile.txt").write_text(C.PROFILE, encoding="utf-8")
    (d3 / "company_facts.json").write_text(json.dumps({"facts": [{"label": a, "value": b} for a, b in C.FACTS]}, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(d3 / "company_facts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["What", "Value"]); w.writerows(C.FACTS)

    (d4 / "not_a_pdf.pdf").write_text(C.NOT_A_PDF, encoding="utf-8")
    (d4 / "T6_messy_pasted_text.txt").write_text(C.MESSY_TEXT, encoding="utf-8")
    print(f"Wrote {len(C.TENDERS)} tender PDFs, {len(C.VAULT_DOCS)} vault papers, company files and edge cases to {out}")


if __name__ == "__main__":
    main()
