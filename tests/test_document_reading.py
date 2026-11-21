"""Reading a tender document and its corrigenda: the AI's quotes are checked against the document, and the rules work without an API key."""
import base64
import json

import pytest

from tender_digest import ai, pdftext
from tests.test_bids import app_ctx  # noqa: F401
from tests.test_boq_api import start_bid
from tests.test_clauses import NIT


def pdf_with(text: str) -> bytes:
    """A real one-page text PDF built by hand (no library needed to write it)."""
    lines = [text[i:i + 90] for i in range(0, len(text), 90)] or [""]
    stream = "BT /F1 9 Tf 20 780 Td 11 TL " + " T* ".join("(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj" for ln in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 800] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


# ---- reading the text of a PDF -----------------------------------------------------------------------------------------

def test_the_text_of_a_pdf_is_read_locally():
    text = pdftext.extract(pdf_with("The bid shall remain valid for 120 days. Earnest money Rs. 50,000."))
    assert "remain valid for 120 days" in text and "Rs. 50,000" in text
    assert pdftext.extract(base64.b64encode(pdf_with("hello world")).decode()).strip() == "hello world"


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-1.4 but damaged", "!!!not base64!!!", None])
def test_anything_that_is_not_a_readable_pdf_is_no_text_never_an_error(data):
    assert pdftext.extract(data or b"") == ""


def test_a_pdf_with_no_text_layer_gives_no_text():
    assert pdftext.extract(pdf_with("")).strip() == ""


# ---- the AI reading, checked against the document ----------------------------------------------------------------------

def fake_doc(evidence):
    return {"summary": "Road widening.", "requirements": ["Similar work of 40% of the estimate"], "financial": [{"label": "EMD", "value": "Rs 4,25,000"}], "dates": [], "documents": [],
            "penalties": ["Liquidated damages 0.5% per week"], "red_flags": ["No price escalation"], "evidence": evidence}


GOOD = {"item": "Liquidated damages 0.5% per week", "quote": "Liquidated damages at 0.5% per week of delay subject to a maximum of 10% of the contract value."}
INVENTED = {"item": "No price escalation", "quote": "The contractor must also pay a fee of 2% to the department for site clearance."}


def test_the_ai_reading_gets_its_quotes_checked_and_the_rules_clauses_added(monkeypatch):
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(fake_doc([GOOD, INVENTED])))
    doc = ai.read_document({}, text=NIT)
    assert [e["verified"] for e in doc["evidence"]] == [True, False]
    assert doc["source_checked"] is True and {c["kind"] for c in doc["clauses"]} >= {"emd", "completion", "liquidated_damages", "price_variation"}


def test_a_pdf_is_checked_against_its_own_text_layer(monkeypatch):
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(fake_doc([GOOD])))
    pdf = base64.b64encode(pdf_with(NIT.replace("\n", " "))).decode()
    doc = ai.read_document({}, pdf_base64=pdf)
    assert doc["evidence"][0]["verified"] is True and doc["source_checked"] is True


def test_a_scanned_pdf_is_never_marked_verified(monkeypatch):
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(fake_doc([GOOD])))
    doc = ai.read_document({}, pdf_base64=base64.b64encode(pdf_with("")).decode())
    assert doc["evidence"][0]["verified"] is None and doc["source_checked"] is False and doc["clauses"] == []


def test_evidence_without_a_quote_is_dropped_and_the_list_is_bounded():
    raw = fake_doc([{"item": "x", "quote": ""}, {"item": "y"}, "junk", {"item": "ok", "quote": "some real words here"}] + [{"item": str(i), "quote": "q" * 20} for i in range(60)])
    ev = ai.clean_doc(raw)["evidence"]
    assert ev[0] == {"item": "ok", "quote": "some real words here"} and len(ev) == 40
    assert ai.clean_doc({"summary": "s"})["evidence"] == []                     # a reading with no evidence key (older prompt, shorter answer) still works


# ---- reading a corrigendum ---------------------------------------------------------------------------------------------

CORR = ("CORRIGENDUM No. 2. The earnest money deposit shall be Rs. 5,00,000/- instead of Rs. 4,25,000/-. "
        "The last date of submission of bids is extended to 30-12-2026 up to 17:00 hours. Item 12 of the BOQ stands deleted.")


def fake_corr(**over):
    base = {"summary": "EMD raised, closing extended.", "closing": "2026-12-30T17:00",
            "changes": [{"what": "EMD raised", "old": "Rs 4,25,000", "new": "Rs 5,00,000", "impact": "price", "action": "Arrange the extra EMD", "quote": "earnest money deposit shall be Rs. 5,00,000/-"},
                        {"what": "Closing extended", "old": "", "new": "30-12-2026", "impact": "dates", "action": "", "quote": "closing is extended to the first of January"}]}
    return {**base, **over}


def test_the_corrigendum_reading_is_cleaned_and_its_quotes_checked(monkeypatch):
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(fake_corr()))
    out = ai.read_corrigendum({}, {"requirements": []}, text=CORR)
    assert out["closing"] == "2026-12-30T17:00" and out["summary"].startswith("EMD raised")
    assert [(c["what"], c["verified"]) for c in out["changes"]] == [("EMD raised", True), ("Closing extended", False)]


def test_a_bad_date_impact_or_shape_is_repaired_not_trusted():
    out = ai.clean_corrigendum({"summary": "s", "closing": "next week", "changes": [{"what": "x", "impact": "catastrophic"}, {"what": ""}, "junk", {"what": "y", "impact": "eligibility"}]})
    assert out["closing"] == "" and [(c["what"], c["impact"]) for c in out["changes"]] == [("x", "other"), ("y", "eligibility")]
    assert ai.clean_corrigendum({"closing": "2026-12-30"})["closing"] == "2026-12-30T00:00"
    with pytest.raises(ai.AIError):
        ai.clean_corrigendum([])


# ---- the endpoints -----------------------------------------------------------------------------------------------------

def test_the_clause_finder_needs_no_api_key_and_is_kept_with_the_bid_apart_from_the_ai_reading(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    r = c.post(f"/api/bids/{bid}/clauses", json={"text": NIT})
    assert r.status_code == 200 and {x["kind"] for x in r.json()["clauses"]} >= {"emd", "completion"} and r.json()["note"] == ""
    assert c.post(f"/api/bids/{bid}/clauses", json={}).status_code == 422
    kept = c.get(f"/api/bids/{bid}").json()
    assert kept["clauses"]["source_checked"] is True and any(x["kind"] == "emd" for x in kept["clauses"]["clauses"]) and kept["doc"] is None
    # a person's "checked against the notice" mark on the AI reading must survive a clause search
    app_ctx.store.db.execute("UPDATE bids SET doc = ?, doc_checked = ? WHERE id = ?", (json.dumps(fake_doc([])), json.dumps({"by": "admin", "at": "2026-11-20T10:00:00"}), bid))
    c.post(f"/api/bids/{bid}/clauses", json={"text": NIT})
    after = c.get(f"/api/bids/{bid}").json()
    assert after["doc_checked"]["by"] == "admin" and after["doc"]["summary"] == "Road widening."


def test_a_scanned_pdf_says_so_instead_of_pretending(app_ctx):
    bid = start_bid(app_ctx)
    r = app_ctx.client.post(f"/api/bids/{bid}/clauses", json={"pdf_base64": base64.b64encode(pdf_with("")).decode()}).json()
    assert r["clauses"] == [] and "scan" in r["note"]


def test_a_corrigendum_is_compared_with_the_tenders_clauses_by_rules_and_by_the_ai(app_ctx, monkeypatch):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    first = c.post(f"/api/bids/{bid}/corrigenda/read", json={"text": CORR}).json()
    assert first["had_document"] is False and [x["old"] for x in first["rules"]] == [""]        # nothing to compare with yet: the rules only say what the corrigendum states
    c.post(f"/api/bids/{bid}/clauses", json={"text": NIT})
    out = c.post(f"/api/bids/{bid}/corrigenda/read", json={"text": CORR}).json()
    assert out["ai"] is None and out["had_document"] is True                      # no API key: rules only, compared with the clauses found earlier
    assert [(x["kind"], x["old"], x["new"]) for x in out["rules"]] == [("emd", "₹4,25,000", "₹5,00,000")]
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(ai, "_call", lambda *a, **k: json.dumps(fake_corr()))
    both = c.post(f"/api/bids/{bid}/corrigenda/read", json={"text": CORR}).json()
    assert both["ai"]["closing"] == "2026-12-30T17:00" and both["rules"] == out["rules"] and both["ai_error"] == ""


def test_if_the_ai_fails_the_rules_result_is_still_returned(app_ctx, monkeypatch):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    monkeypatch.setattr(ai, "configured", lambda: True)

    def boom(*a, **k):
        raise ai.AIError("Claude is busy")
    monkeypatch.setattr(ai, "_call", boom)
    out = c.post(f"/api/bids/{bid}/corrigenda/read", json={"text": CORR}).json()
    assert out["ai"] is None and "busy" in out["ai_error"] and out["clauses"] and out["had_document"] is False


def test_reading_a_corrigendum_needs_text_a_login_and_a_real_bid(app_ctx):
    c = app_ctx.client
    bid = start_bid(app_ctx)
    assert c.post(f"/api/bids/{bid}/corrigenda/read", json={}).status_code == 422
    assert c.post("/api/bids/9999/corrigenda/read", json={"text": CORR}).status_code == 404
    assert c.post("/api/bids/9999/clauses", json={"text": CORR}).status_code == 404
    c.post("/api/logout")
    assert c.post(f"/api/bids/{bid}/corrigenda/read", json={"text": CORR}).status_code == 401
    assert c.post(f"/api/bids/{bid}/clauses", json={"text": CORR}).status_code == 401
