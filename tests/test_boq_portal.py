"""Fetching a tender's BOQ and risks from a portal: link choice, file sniffing, login walls, kept work. The portal and Claude are fakes."""
import base64
import csv
import io
import json
from pathlib import Path

import pytest

from tender_digest import ai
from tender_digest.boq import portal, risk
from tender_digest.boq.masters import MasterStore
from tender_digest.boq.service import BoqService
from tender_digest.gepnic import PortalError, boq_score, pick_boq, pick_nit

FIX = Path(__file__).parent / "fixtures"
KEY = "maha:T1"
CSV = (FIX / "boq_sample.csv").read_bytes()


def html_boq() -> bytes:
    rows = list(csv.reader(io.StringIO(CSV.decode())))
    cells = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<html><body><table>{cells}</table></body></html>".encode()


def xlsx_boq() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    for r in csv.reader(io.StringIO(CSV.decode())):
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Client:
    def __init__(self, files=None, error=None):
        self.files, self.error, self.asked = files or {}, error, []

    def download(self, href):
        self.asked.append(href)
        if self.error:
            raise self.error
        return self.files[href], "application/octet-stream"


class NoDownload:
    pass


def L(*names):
    return [{"name": n, "href": f"/d{i}"} for i, n in enumerate(names)]


@pytest.fixture()
def svc():
    s = BoqService(MasterStore(":memory:"))
    s.import_catalogue("items", (FIX / "erp_items.csv").read_text())
    return s


# ---- choosing links ------------------------------------------------------------------------------------------------------

def test_boq_links_are_chosen_by_name_and_spreadsheets_win():
    assert boq_score({"name": "BOQ_Road.xls"}) == 3 and boq_score({"name": "Annexure.xlsx"}) == 2 and boq_score({"name": "Price Bid.pdf"}) == 1
    assert boq_score({"name": "Tendernotice_1.pdf"}) == 0 and boq_score({"name": "Aboqx.pdf"}) == 0
    assert pick_boq(L("Tendernotice_1.pdf"))is None
    assert pick_boq(L("Tendernotice_1.pdf", "BOQ_Road.xls"))["name"] == "BOQ_Road.xls"
    assert pick_boq(L("BOQ.pdf", "Price Bid.xlsx"))["name"] == "Price Bid.xlsx"
    assert pick_boq(L("BOQ_Road.xls", "Tender Document.pdf"), exclude_href="/d0") is None


def test_a_lone_boq_is_never_mistaken_for_the_notice():
    assert pick_nit(L("BOQ_Road.xls")) is None
    assert pick_nit(L("BOQ_Road.xls", "Tendernotice_1.pdf"))["name"] == "Tendernotice_1.pdf"
    assert pick_nit(L("work.pdf"))["name"] == "work.pdf"
    assert pick_nit([]) is None


def test_files_are_recognised_by_content_not_name():
    assert portal.sniff(b"PK\x03\x04rest") == "xlsx" and portal.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == "xls" and portal.sniff(b"%PDF-1.4") == "pdf"
    assert portal.sniff(b"  <!DOCTYPE html><p>x") == "html" and portal.sniff(b"<?xml version='1.0'?><Workbook/>") == "html"
    assert portal.sniff(b"Sr,Description,Unit\n1,x,Cum") == "csv" and portal.sniff(b"\x00\x01\x02") == "other"
    assert portal._filename("BOQ: Road/Berasia.xls", "xlsx") == "BOQ_ Road_Berasia.xlsx" and portal._filename("", "html") == "BOQ.xls"


# ---- the BOQ step --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,content", [("BOQ_Road.csv", CSV), ("BOQ_Road.xls", html_boq()), ("Price Bid", CSV)])
def test_a_public_boq_is_downloaded_and_matched(svc, name, content):
    client = Client({"/d1": content})
    out = portal.fetch_boq(client, svc, KEY, L("Tendernotice_1.pdf", name), exclude_href="/d0")
    assert out["state"] == "read" and out["lines"] == 26 and client.asked == ["/d1"] and "26 items" in out["message"]
    assert svc.view(KEY)["boq"]["lines"][0]["description"].startswith("Clearing and grubbing")


def test_an_excel_boq_is_read(svc):
    out = portal.fetch_boq(Client({"/d0": xlsx_boq()}), svc, KEY, L("BOQ_Road.xlsx"))
    assert out["state"] == "read" and out["lines"] == 26


def test_a_boq_already_loaded_is_never_replaced(svc):
    portal.fetch_boq(Client({"/d0": CSV}), svc, KEY, L("BOQ.csv"))
    ln = svc.view(KEY)["boq"]["lines"][0]
    svc.confirm(KEY, next(x["line_no"] for x in svc.view(KEY)["boq"]["lines"] if x["description"].startswith("Excavation for roadway")), "EXC-01")
    client = Client({"/d0": CSV})
    out = portal.fetch_boq(client, svc, KEY, L("BOQ.csv"))
    assert out["state"] == "kept" and client.asked == []                   # nothing downloaded, nothing lost
    assert svc.view(KEY)["boq"]["counts"]["AUTO_MATCHED"] == 1 and ln["description"]


def test_every_way_the_boq_step_can_fail_is_explained_not_raised(svc):
    assert portal.fetch_boq(Client(), svc, KEY, L("Tendernotice_1.pdf"))["state"] == "no_boq"
    assert portal.fetch_boq(NoDownload(), svc, KEY, L("BOQ.xls"))["state"] == "unsupported"
    out = portal.fetch_boq(Client(error=PortalError("HTTP 403", "blocked")), svc, KEY, L("BOQ.xls"))
    assert out["state"] == "failed" and "HTTP 403" in out["message"]
    for page in (b"<html>Please enter the captcha</html>", b"<html><form>Login <input type=password></form></html>"):
        out = portal.fetch_boq(Client({"/d0": page}), svc, KEY, L("BOQ.xls"))
        assert out["state"] == "login" and "never gets past" in out["message"]
    assert portal.fetch_boq(Client({"/d0": b"%PDF-1.4 scanned"}), svc, KEY, L("BOQ.xls"))["state"] == "unreadable"
    out = portal.fetch_boq(Client({"/d0": b"<html><table><tr><td>nothing useful</td></tr></table></html>"}), svc, KEY, L("BOQ.xls"))
    assert out["state"] == "unreadable" and "No BOQ table" in out["message"]
    assert svc.view(KEY)["boq"] is None                                       # failures leave nothing half-loaded


# ---- the risk step -------------------------------------------------------------------------------------------------------

FINDINGS = {"risks": [{"tag": t, "present": "no_mention", "detail": "", "clause": ""} for t in risk.TAGS], "facts": {"retention_pct": 5}}


def test_risks_are_read_once_from_the_notice(svc, monkeypatch):
    assert portal.fetch_risks(svc, KEY, {}, None)["state"] == "no_notice"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert portal.fetch_risks(svc, KEY, {}, b"%PDF-1.4")["state"] == "ai_off"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = []
    monkeypatch.setattr(ai, "_call", lambda system, content, **k: seen.append(content) or json.dumps(FINDINGS))
    assert portal.fetch_risks(svc, KEY, {"title": "Road"}, b"%PDF-1.4 nit")["state"] == "read" and svc.risks(KEY)["findings"]["facts"]["retention_pct"] == 5.0
    assert portal.fetch_risks(svc, KEY, {}, b"%PDF-1.4 nit")["state"] == "kept" and len(seen) == 1       # no second Claude call
    other = BoqService(MasterStore(":memory:"))
    monkeypatch.setattr(ai, "_call", lambda *a, **k: (_ for _ in ()).throw(ai.AIError("busy")))
    out = portal.fetch_risks(other, KEY, {}, base64.b64decode(base64.b64encode(b"%PDF-1.4")))
    assert out == {"state": "ai_failed", "message": "busy"}
