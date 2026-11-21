"""Finding the conditions that decide a tender by fixed rules, with the sentence each came from; and checking quotes against the document."""
import pytest

from tender_digest import clauses

NIT = """
NOTICE INVITING TENDER
1. Name of work: Widening of road from km 10 to km 14.
2. Estimated cost: Rs. 4,25,00,000/-. Earnest Money Deposit (EMD) of Rs. 4,25,000/- shall be paid online.
3. The bid shall remain valid for 120 days from the date of opening of bids.
4. Time of completion: 18 months including monsoon. Defect liability period shall be 36 months from the date of completion.
5. The bidder should have completed at least one similar work of value not less than 40% of the estimated cost during the last 7 years.
6. Average annual turnover of the bidder during last 3 years should be not less than Rs. 5.5 crore.
7. Performance security @ 3% of the contract value shall be submitted within 15 days of the letter of acceptance.
8. Retention money @ 5% shall be deducted from running bills. Mobilisation advance 10% will be given against bank guarantee.
9. Liquidated damages at 0.5% per week of delay subject to a maximum of 10% of the contract value.
10. No price escalation shall be payable. The contractor shall not be entitled to any price variation.
"""


def by_kind(text):
    return {c["kind"]: c for c in clauses.find(text)}


def test_a_realistic_notice_gives_every_clause_with_the_sentence_it_came_from():
    got = by_kind(NIT)
    assert {k: c["value"] for k, c in got.items()} == {
        "emd": "₹4,25,000", "bid_validity": "120 days", "completion": "18 months", "defect_liability": "36 months", "performance_security": "3% of the contract value",
        "retention": "5%", "liquidated_damages": "0.5% per week, up to 10%", "similar_work": "a similar work worth 40% of the estimate", "turnover": "₹5.5 crore",
        "mobilisation_advance": "10%", "price_variation": "not allowed: the price is fixed for the whole contract"}
    assert got["emd"]["quote"] == "Earnest Money Deposit (EMD) of Rs. 4,25,000/- shall be paid online."
    assert got["liquidated_damages"]["quote"] == "Liquidated damages at 0.5% per week of delay subject to a maximum of 10% of the contract value."
    for c in got.values():                                   # every quote really is in the notice, and the value's own words are in the quote
        assert clauses.quote_in(NIT, c["quote"])


def test_nothing_the_tender_does_not_say_is_reported():
    assert clauses.find("") == [] and clauses.find(None) == []
    assert clauses.find("Name of work: construction of a school building at Pune. Bids are invited online.") == []
    only = by_kind("The EMD is Rs. 50,000. Nothing else is stated.")
    assert list(only) == ["emd"]


@pytest.mark.parametrize("text,value", [
    ("Earnest Money Deposit : ₹ 1,00,000", "₹1,00,000"), ("EMD amount INR 2,50,000/-", "₹2,50,000"), ("Bid security of Rs. 12.5 lakh to be furnished", "₹12.5 lakh"),
    ("Earnest money @ 2% of the estimated cost", "2% of the estimate"), ("EMD of 1.5 per cent of tendered value", "1.5% of the estimate"),
    ("E.M.D. of Rs 75000", "₹75,000"),
])
def test_earnest_money_is_read_however_it_is_written(text, value):
    assert by_kind(text)["emd"]["value"] == value


@pytest.mark.parametrize("text,value", [
    ("Time of completion: 180 days", "180 days"), ("The period of completion of the work is 24 months.", "24 months"), ("Completion period 1 year", "1 year"),
    ("Time allowed for execution of the work: 12 weeks", "12 weeks"), ("Contract period shall be 9 months", "9 months"),
])
def test_the_completion_period_in_days_weeks_months_or_years(text, value):
    assert by_kind(text)["completion"]["value"] == value


@pytest.mark.parametrize("text,value", [
    ("Liquidated damages @ 0.5% per week subject to maximum 10%", "0.5% per week, up to 10%"),
    ("Compensation for delay shall be 1% per week of delay, not exceeding 10% of contract value", "1% per week, up to 10%"),
    ("Liquidated damages of 0.1% per day", "0.1% per day"),
])
def test_liquidated_damages_with_and_without_a_cap(text, value):
    assert by_kind(text)["liquidated_damages"]["value"] == value


def test_a_fixed_price_contract_is_flagged_as_a_risk_and_a_price_variation_clause_is_only_mentioned():
    no = by_kind("This is a fixed price contract.")["price_variation"]
    assert "not allowed" in no["value"] and "Price this in" in no["watch"]
    maybe = by_kind("Price variation shall be as per clause 10CC of the conditions of contract.")["price_variation"]
    assert "check how it works" in maybe["value"] and maybe["watch"] == ""
    assert "Price this in" in by_kind("Price escalation is not applicable to this work")["price_variation"]["watch"]


def test_similar_work_stated_as_an_amount():
    assert by_kind("Completed similar works costing not less than Rs. 1.2 crore in the last five years")["similar_work"]["value"] == "a similar work worth ₹1.2 crore"


def test_a_very_long_sentence_is_cut_around_the_match():
    text = "General conditions " * 80 + "earnest money Rs. 5,000 " + "and so on " * 80
    q = by_kind(text)["emd"]["quote"]
    assert len(q) <= 260 and "Rs. 5,000" in q


def test_the_indian_way_of_grouping_digits():
    assert [clauses._indian(n) for n in (999, 1000, 425000, 1234567, 10_000_000)] == ["999", "1,000", "4,25,000", "12,34,567", "1,00,00,000"]


# ---- checking quotes ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("quote", [
    "bid shall remain valid for 120 days", "BID SHALL REMAIN VALID FOR 120 DAYS", "bid  shall\nremain valid, for 120 days!", "Performance security ... within 15 days of the letter",
])
def test_a_real_quote_is_found_whatever_the_spacing_case_or_punctuation(quote):
    assert clauses.quote_in(NIT, quote)


@pytest.mark.parametrize("quote", ["bid shall remain valid for 90 days", "", None, "short", "the contractor shall pay a bonus of 5% on early completion", "within 15 days ... Performance security"])
def test_an_invented_or_reordered_or_empty_quote_is_not(quote):
    assert not clauses.quote_in(NIT, quote)


def test_evidence_is_marked_verified_not_found_or_unchecked():
    ev = [{"item": "valid 120 days", "quote": "bid shall remain valid for 120 days"}, {"item": "bonus", "quote": "a bonus of 5% is payable for early completion of work"}]
    out = clauses.ground(ev, NIT)
    assert [e["verified"] for e in out] == [True, False] and out[0]["item"] == "valid 120 days"
    assert [e["verified"] for e in clauses.ground(ev, "")] == [None, None]                   # a scan: nothing can be checked, so nothing is called verified
    assert [e["verified"] for e in clauses.ground(ev, "too short")] == [None, None]


# ---- what a corrigendum changes ----------------------------------------------------------------------------------------

def test_a_changed_clause_and_a_new_one_are_listed_and_an_unchanged_one_is_not():
    before = clauses.find(NIT)
    corr = "The earnest money deposit shall be Rs. 5,00,000/-. Time of completion: 18 months. Mobilisation advance 15% will be given."
    got = {c["kind"]: c for c in clauses.changes(before, clauses.find(corr))}
    assert set(got) == {"emd", "mobilisation_advance"}
    assert (got["emd"]["old"], got["emd"]["new"]) == ("₹4,25,000", "₹5,00,000") and "5,00,000" in got["emd"]["quote"]
    assert (got["mobilisation_advance"]["old"], got["mobilisation_advance"]["new"]) == ("10%", "15%")
    assert clauses.changes(before, before) == [] and clauses.changes([], []) == []


def test_a_clause_the_tender_never_had_is_reported_as_new():
    got = clauses.changes([], clauses.find("Retention money @ 10% shall be deducted."))
    assert got == [{"kind": "retention", "label": "Security deposit / retention", "old": "", "new": "10%", "quote": "Retention money @ 10% shall be deducted."}]


def test_an_abbreviation_or_a_decimal_does_not_cut_the_quoted_sentence_short():
    q = by_kind("The earnest money deposit shall be Rs. 5,00,000/- instead of Rs. 4,25,000/-. Other terms unchanged.")["emd"]["quote"]
    assert q == "The earnest money deposit shall be Rs. 5,00,000/- instead of Rs. 4,25,000/-."
    assert by_kind("Liquidated damages at 0.5% per week. Next sentence.")["liquidated_damages"]["quote"] == "Liquidated damages at 0.5% per week."
