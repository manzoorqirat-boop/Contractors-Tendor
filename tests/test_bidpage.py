"""The bid page: AI-read details are marked as checked by a named person, and that mark is cleared whenever the document is read again."""
from tests.test_bids import app_ctx  # noqa: F401


def test_a_person_can_mark_the_ai_read_details_as_checked_and_take_it_back(app_ctx):
    c = app_ctx.client
    bid = c.post("/api/bids", json={"key": app_ctx.key}).json()["id"]
    assert c.post(f"/api/bids/{bid}/doc-checked", json={"checked": True}).status_code == 422  # nothing was read yet
    from tender_digest.bids import BidBook
    from tests.test_bids import NOW
    book = BidBook(app_ctx.store)
    book.set_json(bid, "doc", {"requirements": ["Class B registration"]}, NOW)
    assert c.get(f"/api/bids/{bid}").json()["doc_checked"] is None
    marked = c.post(f"/api/bids/{bid}/doc-checked", json={"checked": True}).json()["doc_checked"]
    assert marked["by"] == "admin"  # the signed-in person, named
    assert c.get(f"/api/bids/{bid}").json()["doc_checked"]["at"] == marked["at"]
    assert c.post(f"/api/bids/{bid}/doc-checked", json={"checked": False}).json()["doc_checked"] is None


def test_reading_the_document_again_clears_the_checked_mark(app_ctx):
    from tender_digest.bids import BidBook
    from tests.test_bids import NOW
    c = app_ctx.client
    bid = c.post("/api/bids", json={"key": app_ctx.key}).json()["id"]
    book = BidBook(app_ctx.store)
    book.set_json(bid, "doc", {"requirements": ["A"]}, NOW)
    c.post(f"/api/bids/{bid}/doc-checked", json={"checked": True})
    assert c.get(f"/api/bids/{bid}").json()["doc_checked"]
    book.set_json(bid, "doc", {"requirements": ["A", "B"]}, NOW)  # a new reading: nobody has compared this one
    assert c.get(f"/api/bids/{bid}").json()["doc_checked"] is None
    book.set_json(bid, "doc_checked", {"by": "x", "at": "2026-11-20T10:00:00"}, NOW)  # and setting the mark itself does not clear itself
    assert c.get(f"/api/bids/{bid}").json()["doc_checked"]["by"] == "x"
