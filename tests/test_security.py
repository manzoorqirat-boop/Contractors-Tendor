"""Devices, password reset by email, API tokens, outgoing webhooks, retention, export and erasure, encryption at rest."""
import time

import pytest
from fastapi.testclient import TestClient

from tender_digest import accounts, web
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_bids import kept
from tests.test_web import PASSWORD, FakeSender, settings

GOOD = "purple-tractor-window"
NEW = "orange-bicycle-harbour"


@pytest.fixture
def sec(monkeypatch, tmp_path):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    env = settings()
    sender = FakeSender()
    manager = RunManager(env, store, lambda p, s: None, sender, background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin", app_url="https://tend.example.com"), env, store, manager)
    owner = TestClient(app)
    assert owner.post("/api/login", json={"username": "admin", "password": PASSWORD}, headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"}).status_code == 200
    tender = kept(store)

    def person(username, role="bidder", email="", agent=""):
        r = owner.post("/api/users", json={"username": username, "name": username.title(), "role": role, "password": "temporary-pass-1", "email": email})
        assert r.status_code == 200, r.text
        c = TestClient(app)
        h = {"user-agent": agent} if agent else {}
        assert c.post("/api/login", json={"username": username, "password": "temporary-pass-1"}, headers=h).status_code == 200
        assert c.post("/api/account/password", json={"current": "temporary-pass-1", "new": GOOD}).status_code == 200
        return c

    def mails():
        return [(c[0][3], c[0][4], c[0][6]) for c in sender.calls]

    return type("S", (), {"app": app, "owner": owner, "person": person, "mails": mails, "sender": sender, "store": store, "tender": tender, "tmp": tmp_path, "manager": manager})


# ---- devices ------------------------------------------------------------------------------------------------------

def test_devices_are_listed_named_and_can_be_ended_one_by_one(sec):
    ua_pc = "Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    ua_phone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17) AppleWebKit/605.1.15 Version/17 Mobile Safari/604.1"
    meera = sec.person("meera", agent=ua_pc)
    phone = TestClient(sec.app)
    assert phone.post("/api/login", json={"username": "meera", "password": GOOD}, headers={"user-agent": ua_phone}).status_code == 200
    listed = meera.get("/api/account/sessions").json()["sessions"]
    assert {s["device"] for s in listed} == {"Chrome on Windows", "Safari on iPhone"}
    assert [s["current"] for s in listed].count(True) == 1 and all("sid" not in s and len(s["ref"]) == 16 for s in listed)
    other = next(s for s in listed if not s["current"])
    assert meera.delete(f"/api/account/sessions/{other['ref']}").status_code == 200
    assert phone.get("/api/me").json()["authenticated"] is False          # that device is signed out at once
    assert meera.get("/api/me").json()["authenticated"] is True
    assert meera.delete(f"/api/account/sessions/{other['ref']}").status_code == 404


def test_one_person_cannot_end_another_persons_sign_in(sec):
    a, b = sec.person("meera"), sec.person("raj")
    ref = a.get("/api/account/sessions").json()["sessions"][0]["ref"]
    assert b.delete(f"/api/account/sessions/{ref}").status_code == 404
    assert a.get("/api/me").json()["authenticated"] is True
    assert all(s["device"] for s in b.get("/api/account/sessions").json()["sessions"])


def test_user_agent_names():
    d = accounts.describe_agent
    assert d("Mozilla/5.0 (Windows NT 10.0) Edg/120") == "Edge on Windows" and d("Mozilla/5.0 (X11; Linux) Firefox/121.0") == "Firefox on Linux"
    assert d("curl/8.0") == "Unknown device" and d("") == "Unknown device" and d("x" * 5000) == "Unknown device"


# ---- password reset -----------------------------------------------------------------------------------------------

def link_token(sec):
    text = sec.mails()[-1][2]
    return text.split("/#/reset/")[1].split()[0]


def test_reset_by_email_end_to_end(sec):
    meera = sec.person("meera", email="meera@example.com")
    r = sec.owner.post("/api/password-reset/request", json={"who": "Meera@Example.com"})     # by email, any case
    assert r.status_code == 200 and "on its way" in r.json()["message"]
    to, subject, text = sec.mails()[-1]
    assert to == ["meera@example.com"] and "Reset your password" in subject and "https://tend.example.com/#/reset/" in text
    token = link_token(sec)
    anon = TestClient(sec.app)
    assert anon.post("/api/password-reset/confirm", json={"token": token, "password": "short"}).status_code == 422       # the link survives a weak password
    assert anon.post("/api/password-reset/confirm", json={"token": token, "password": NEW}).status_code == 200
    assert meera.get("/api/me").json()["authenticated"] is False                                                        # the old sign-ins ended
    assert anon.post("/api/login", json={"username": "meera", "password": GOOD}).status_code == 401
    ok = anon.post("/api/login", json={"username": "meera", "password": NEW})
    assert ok.status_code == 200 and anon.get("/api/me").json()["user"]["must_change"] is False                          # they chose it themselves
    assert anon.post("/api/password-reset/confirm", json={"token": token, "password": NEW + "x"}).status_code == 422     # one use only
    assert any("password" in e["action"].lower() for e in sec.owner.get("/api/audit").json()["entries"])


def test_reset_says_the_same_to_everyone_and_sends_nothing_for_strangers(sec):
    sec.person("noemail")                                                       # no address on file
    before = len(sec.mails())
    msgs = {sec.owner.post("/api/password-reset/request", json={"who": w}).json()["message"] for w in ("noemail", "nobody-here", "admin", "x@y.co")}
    assert len(msgs) == 1 and len(sec.mails()) == before


def test_reset_links_expire_and_are_limited(sec):
    sec.person("meera", email="meera@example.com")
    for _ in range(3):
        sec.owner.post("/api/password-reset/request", json={"who": "meera"})
    assert len(sec.mails()) == 3
    sec.owner.post("/api/password-reset/request", json={"who": "meera"})
    assert len(sec.mails()) == 3                                                # a fourth in an hour is quietly refused
    token = link_token(sec)
    with sec.store.lock:
        sec.store.db.execute("UPDATE password_resets SET expires = ?", (time.time() - 1,))
        sec.store.db.commit()
    r = TestClient(sec.app).post("/api/password-reset/confirm", json={"token": token, "password": NEW})
    assert r.status_code == 422 and "expired" in r.json()["detail"]
    assert TestClient(sec.app).post("/api/password-reset/confirm", json={"token": "made-up", "password": NEW}).status_code == 422


def test_reset_is_throttled_per_address(sec):
    anon = TestClient(sec.app)
    codes = [anon.post("/api/password-reset/request", json={"who": f"u{i}"}).status_code for i in range(8)]
    assert codes[:6] == [200] * 6 and codes[6:] == [429, 429]


def test_reset_needs_mail_set_up(sec, monkeypatch):
    monkeypatch.setattr(type(sec.manager), "mail_configured", property(lambda self: False))
    r = TestClient(sec.app).post("/api/password-reset/request", json={"who": "x"})
    assert r.status_code == 503 and "administrator" in r.json()["detail"]


def test_owner_and_deactivated_people_cannot_be_reset(sec):
    sec.person("meera", email="meera@example.com")
    uid = next(u["id"] for u in sec.owner.get("/api/users").json()["users"] if u["username"] == "meera")
    sec.owner.patch(f"/api/users/{uid}", json={"active": False})
    before = len(sec.mails())
    sec.owner.post("/api/password-reset/request", json={"who": "meera"})
    sec.owner.post("/api/password-reset/request", json={"who": "admin"})
    assert len(sec.mails()) == before


# ---- API tokens ---------------------------------------------------------------------------------------------------

def make_token(sec, name="Finance sheet", role="viewer", days=None):
    r = sec.owner.post("/api/tokens", json={"name": name, "role": role, "days": days})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(sec, secret):
    c = TestClient(sec.app)
    c.headers["authorization"] = f"Bearer {secret}"
    return c


def test_a_token_reads_with_its_role_and_is_shown_only_once(sec):
    made = make_token(sec)
    secret = made["secret"]
    assert secret.startswith("tnd_") and secret not in str(sec.owner.get("/api/tokens").json())      # never shown again
    with sec.store.lock:
        stored = [r[0] for r in sec.store.db.execute("SELECT hash FROM api_tokens")]
    assert secret not in stored and len(stored[0]) == 64                                                # only a hash is kept
    api = bearer(sec, secret)
    assert api.get("/api/bids").status_code == 200 and api.get("/api/me").json()["user"]["role"] == "viewer"
    assert api.post("/api/bids", json={"key": sec.tender.key}).status_code == 403                      # a viewer token cannot work
    assert api.get("/api/users").status_code == 403 and api.get("/api/tokens").status_code == 403    # and never reaches admin areas
    assert sec.owner.get("/api/tokens").json()["tokens"][0]["last_used"] is not None


def test_a_bidder_token_can_work_but_not_manage_accounts_or_itself(sec):
    api = bearer(sec, make_token(sec, role="bidder")["secret"])
    assert api.post("/api/bids", json={"key": sec.tender.key}).status_code == 200
    assert api.post("/api/account/password", json={"current": "x", "new": GOOD}).status_code == 403
    assert api.get("/api/account/sessions").status_code == 403
    assert api.post("/api/tokens", json={"name": "child", "role": "bidder"}).status_code == 403
    entries = sec.owner.get("/api/audit").json()["entries"]
    assert any(e["username"] == "token:Finance sheet" and e["action"] == "Started a bid" for e in entries)       # who did it is on record


def test_revoked_expired_and_made_up_tokens_are_refused(sec):
    made = make_token(sec)
    api = bearer(sec, made["secret"])
    assert api.get("/api/bids").status_code == 200
    assert sec.owner.delete(f"/api/tokens/{made['token']['id']}").status_code == 200
    assert api.get("/api/bids").status_code == 401 and sec.owner.delete(f"/api/tokens/{made['token']['id']}").status_code == 404
    soon = make_token(sec, "short", days=1)
    with sec.store.lock:
        sec.store.db.execute("UPDATE api_tokens SET expires = '2020-01-01T00:00:00' WHERE id = ?", (soon["token"]["id"],))
        sec.store.db.commit()
    assert bearer(sec, soon["secret"]).get("/api/bids").status_code == 401
    for junk in ("tnd_nonsense", "nonsense", "tnd_" + "a" * 300, ""):
        assert bearer(sec, junk).get("/api/bids").status_code == 401


def test_guessing_tokens_is_slowed_like_guessing_passwords(sec):
    guess = TestClient(sec.app)
    codes = []
    for i in range(12):
        guess.headers["authorization"] = f"Bearer tnd_wrong{i}"
        codes.append(guess.get("/api/bids").status_code)
    assert 401 in codes and codes[-1] == 429


def test_token_rules(sec):
    assert sec.owner.post("/api/tokens", json={"name": "x", "role": "admin"}).status_code == 422       # never admin
    assert sec.owner.post("/api/tokens", json={"name": "  ", "role": "viewer"}).status_code == 422
    assert sec.owner.post("/api/tokens", json={"name": "x", "role": "viewer", "days": 0}).status_code == 422
    meera = sec.person("meera", "bidder")
    assert meera.post("/api/tokens", json={"name": "x", "role": "viewer"}).status_code == 403
    for i in range(tokens_max()):
        make_token(sec, f"t{i}")
    assert sec.owner.post("/api/tokens", json={"name": "one more", "role": "viewer"}).status_code == 422


def tokens_max():
    from tender_digest import tokens
    return tokens.MAX_TOKENS


# ---- outgoing webhooks ------------------------------------------------------------------------------------------------

class Receiver:
    """Stands in for the other system: records what it was sent and answers with a chosen status."""
    def __init__(self, status=200):
        self.status, self.got = status, []

    def post(self, url, data=None, headers=None, **kw):
        self.got.append((url, data, headers, kw))
        return type("R", (), {"status_code": self.status})()


def hook(sec, events=("bid.stage_changed",), url="https://hooks.example.com/tendai"):
    r = sec.owner.post("/api/webhooks", json={"name": "ERP", "url": url, "events": list(events)})
    assert r.status_code == 200, r.text
    return r.json()


def book_of(sec):
    from tender_digest.events import Events
    return Events(sec.store)


def test_events_are_queued_signed_and_delivered(sec):
    from datetime import datetime
    from tender_digest import events as ev
    made = hook(sec, ("bid.stage_changed", "bid.created"))
    bid = sec.owner.post("/api/bids", json={"key": sec.tender.key}).json()["id"]
    sec.owner.patch(f"/api/bids/{bid}", json={"stage": "preparing"})
    sec.owner.patch(f"/api/bids/{bid}", json={"notes": "not an event"})
    rx = Receiver()
    book = book_of(sec)
    assert book.deliver(datetime.now(), session=rx) == 2
    (u1, body1, h1, k1), (u2, body2, h2, _) = rx.got
    assert u1 == "https://hooks.example.com/tendai" and k1["allow_redirects"] is False
    import json
    first, second = json.loads(body1), json.loads(body2)
    assert [first["event"], second["event"]] == ["bid.created", "bid.stage_changed"] and second["data"]["previous_stage"] == "interested" and second["data"]["tender_id"] == "T1"
    assert h2["X-TendAI-Signature"] == ev.sign(made["secret"], h2["X-TendAI-Timestamp"], body2.decode()) and h1["X-TendAI-Delivery"] != h2["X-TendAI-Delivery"]
    assert book.list()[0]["last_status"] == "ok" and book.list()[0]["waiting"] == 0
    assert "secret" not in str(sec.owner.get("/api/webhooks").json()["webhooks"][0]).replace("secret_hint", "")


def test_sending_stops_when_the_time_budget_is_used_and_the_rest_goes_out_next_time(sec):
    from datetime import datetime
    hook(sec, ("bid.stage_changed", "bid.created"))
    bid = sec.owner.post("/api/bids", json={"key": sec.tender.key}).json()["id"]
    sec.owner.patch(f"/api/bids/{bid}", json={"stage": "preparing"})
    book, rx = book_of(sec), Receiver()
    assert book.deliver(datetime.now(), session=rx, budget=-1) == 0 and rx.got == []           # a receiver that is slow cannot hold the scheduler: nothing new is started
    assert book.list()[0]["waiting"] == 2                                                        # and nothing is lost
    assert book.deliver(datetime.now(), session=rx) == 2 and book.list()[0]["waiting"] == 0


def test_a_webhook_only_gets_the_events_it_asked_for_and_not_when_off(sec):
    from datetime import datetime
    made = hook(sec, ("bid.result",))
    bid = sec.owner.post("/api/bids", json={"key": sec.tender.key}).json()["id"]
    sec.owner.patch(f"/api/bids/{bid}", json={"stage": "preparing"})
    assert book_of(sec).deliver(datetime.now(), session=Receiver()) == 0
    sec.owner.put(f"/api/bids/{bid}/bidders", json={"bidders": [{"name": "Rao Infra", "price": 100, "rank": 1}]})
    rx = Receiver()
    assert book_of(sec).deliver(datetime.now(), session=rx) == 1 and b"Rao Infra" in rx.got[0][1]
    sec.owner.patch(f"/api/webhooks/{made['webhook']['id']}", json={"active": False})
    sec.owner.patch(f"/api/bids/{bid}", json={"stage": "submitted"})
    assert book_of(sec).deliver(datetime.now(), session=Receiver()) == 0


def test_failed_deliveries_are_retried_then_dropped(sec):
    from datetime import datetime
    from tender_digest import events as ev
    hook(sec, ("bid.created",))
    sec.owner.post("/api/bids", json={"key": sec.tender.key})
    book, rx = book_of(sec), Receiver(500)
    assert book.deliver(datetime.now(), session=rx) == 0
    h = book.list()[0]
    assert h["last_status"] == "retrying" and "500" in h["last_error"] and h["waiting"] == 1
    assert book.deliver(datetime.now(), session=rx) == 0 and len(rx.got) == 1                 # not due yet: no hammering
    for _ in range(ev.MAX_ATTEMPTS - 1):
        with sec.store.lock:
            sec.store.db.execute("UPDATE webhook_queue SET next_try = 0")
            sec.store.db.commit()
        book.deliver(datetime.now(), session=rx)
    h = book.list()[0]
    assert h["last_status"] == "failed" and "Gave up" in h["last_error"] and h["waiting"] == 0 and len(rx.got) == ev.MAX_ATTEMPTS
    rx.status = 200
    sec.owner.post("/api/bids", json={"key": sec.tender.key})                                  # nothing new: the bid already exists
    assert book.deliver(datetime.now(), session=rx) == 0


def test_webhook_addresses_must_be_public_https(sec):
    for bad in ("http://hooks.example.com/x", "https://127.0.0.1/x", "https://user:pw@hooks.example.com/x", "ftp://x.example.com", "", "not a url"):
        assert sec.owner.post("/api/webhooks", json={"name": "x", "url": bad, "events": ["bid.created"]}).status_code == 422, bad
    assert sec.owner.post("/api/webhooks", json={"name": "x", "url": "https://hooks.example.com/x", "events": []}).status_code == 422
    assert sec.owner.post("/api/webhooks", json={"name": "x", "url": "https://hooks.example.com/x", "events": ["test"]}).status_code == 422
    assert sec.owner.post("/api/webhooks", json={"name": "x", "url": "https://hooks.example.com/x", "events": ["bid.nonsense"]}).status_code == 422


def test_private_network_targets_are_never_contacted(sec, monkeypatch):
    from datetime import datetime
    from tender_digest import events as ev
    hook(sec, ("bid.created",))
    sec.owner.post("/api/bids", json={"key": sec.tender.key})
    monkeypatch.setattr(ev, "is_public_host", lambda host: False)
    rx = Receiver()
    assert book_of(sec).deliver(datetime.now(), session=rx) == 0 and rx.got == []
    assert "private network" in book_of(sec).list()[0]["last_error"]


def test_webhook_management_and_test_button(sec, monkeypatch):
    from tender_digest import events as ev
    made = hook(sec)
    hid = made["webhook"]["id"]
    rx = Receiver()
    monkeypatch.setattr(ev, "public_session", lambda: rx)         # the test button sends through the public-only session; here the receiver stands in for it
    ok = sec.owner.post(f"/api/webhooks/{hid}/test")
    assert ok.status_code == 200 and rx.got[0][2]["X-TendAI-Event"] == "test"
    rx.status = 503
    assert sec.owner.post(f"/api/webhooks/{hid}/test").status_code == 502
    new = sec.owner.post(f"/api/webhooks/{hid}/secret").json()["secret"]
    assert new != made["secret"] and new.startswith("whsec_")
    assert sec.owner.patch(f"/api/webhooks/{hid}", json={"name": "ERP 2", "events": ["bid.created", "bid.changed"]}).json()["events"] == ["bid.created", "bid.changed"]
    assert sec.owner.patch(f"/api/webhooks/{hid}", json={}).status_code == 422
    assert sec.person("meera", "bidder").get("/api/webhooks").status_code == 403
    assert sec.owner.delete(f"/api/webhooks/{hid}").status_code == 200 and sec.owner.delete(f"/api/webhooks/{hid}").status_code == 404
    assert sec.owner.post("/api/webhooks/999/test").status_code == 404 and sec.owner.patch("/api/webhooks/999", json={"active": True}).status_code == 404


def test_corrigendum_and_changes_reach_webhooks(sec):
    from datetime import datetime
    import json
    hook(sec, ("bid.changed",))
    bid = sec.owner.post("/api/bids", json={"key": sec.tender.key}).json()["id"]
    sec.owner.post(f"/api/bids/{bid}/corrigenda", json={"note": "Corrigendum 1", "closing": "2026-12-20"})
    rx = Receiver()
    book_of(sec).deliver(datetime.now(), session=rx)
    kinds = sorted(json.loads(g[1])["data"]["change"] for g in rx.got)
    assert kinds == ["closing", "corrigendum"]


# ---- retention, export and erasure ------------------------------------------------------------------------------------

def uid(sec, username):
    return next(u["id"] for u in sec.owner.get("/api/users").json()["users"] if u["username"] == username)


def worked(sec, c, username="meera"):
    bid = sec.owner.post("/api/bids", json={"key": sec.tender.key}).json()["id"]
    c.post(f"/api/bids/{bid}/comments", json={"text": "Rates look low"})
    item = sec.owner.get(f"/api/bids/{bid}").json()["items"][0]["id"]
    sec.owner.patch(f"/api/bids/{bid}/items/{item}", json={"owner": username})
    sec.owner.patch(f"/api/bids/{bid}", json={"owner": username})
    c.post(f"/api/bids/{bid}/signoff", json={"action": "request", "note": "please"})
    return bid


def test_a_person_can_download_what_is_kept_about_them(sec):
    meera = sec.person("meera", "bidder", "meera@example.com")
    bid = worked(sec, meera)
    r = meera.get("/api/account/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["account"]["username"] == "meera" and data["account"]["email"] == "meera@example.com"
    assert "pw_hash" not in data["account"] and "totp_secret" not in data["account"] and "scrypt" not in r.text      # no secrets in the copy
    assert [c["text"] for c in data["comments"]] == ["Rates look low"] and data["bids_owned"][0]["bid_id"] == bid
    assert len(data["checklist_items_assigned"]) == 1 and data["approvals"][0]["requested_by"] == "meera" and any(a["action"] == "Signed in" for a in data["activity"])
    assert sec.owner.get("/api/account/export").status_code == 422                                      # the owner has no stored account
    assert sec.owner.get(f"/api/users/{uid(sec, 'meera')}/export").json()["account"]["username"] == "meera"
    assert sec.person("raj", "bidder").get(f"/api/users/{uid(sec, 'meera')}/export").status_code == 403   # only an administrator reads another person's
    assert sec.owner.get("/api/users/0/export").status_code == 422 and sec.owner.get("/api/users/999/export").status_code == 404


def test_erasing_a_person_removes_who_they_were_but_keeps_the_work(sec):
    meera = sec.person("meera", "bidder", "meera@example.com")
    bid = worked(sec, meera)
    meera_id = uid(sec, "meera")
    assert sec.owner.post(f"/api/users/{meera_id}/erase", json={"confirm": "meera"}).status_code == 422          # still active
    sec.owner.patch(f"/api/users/{meera_id}", json={"active": False})
    assert sec.owner.post(f"/api/users/{meera_id}/erase", json={"confirm": "wrong"}).status_code == 422
    r = sec.owner.post(f"/api/users/{meera_id}/erase", json={"confirm": "Meera"})
    assert r.status_code == 200 and r.json()["handle"] == f"erased-{meera_id}"
    assert r.json()["changed"]["comments"] == 1 and r.json()["changed"]["bids_owned"] == 1 and r.json()["changed"]["checklist_items"] == 1 and r.json()["changed"]["approvals"] == 1
    page = sec.owner.get(f"/api/bids/{bid}").json()
    assert page["owner"] is None and page["signoff"]["requested_by"] == f"erased-{meera_id}"
    assert page["items"][0]["owner"] in (None, "")
    comments = sec.owner.get(f"/api/bids/{bid}/comments").json()["comments"]
    assert comments[0]["text"] == "Rates look low" and comments[0]["username"] == f"erased-{meera_id}"           # the work stays, the name goes
    user = next(u for u in sec.owner.get("/api/users").json()["users"] if u["id"] == meera_id)
    assert user["username"] == f"erased-{meera_id}" and user["name"] == "Erased person" and not user["email"] and not user["active"]
    log = sec.owner.get("/api/audit", params={"limit": 200}).json()["entries"]
    assert not any(e["username"] == "meera" for e in log) and any(e["action"] == "Erased a person's data" for e in log)
    assert TestClient(sec.app).post("/api/login", json={"username": "meera", "password": GOOD}).status_code == 401
    assert TestClient(sec.app).post("/api/login", json={"username": f"erased-{meera_id}", "password": "!"}).status_code == 401
    assert sec.owner.post(f"/api/users/{meera_id}/erase", json={"confirm": f"erased-{meera_id}"}).status_code == 422   # nothing left to erase
    assert sec.owner.post("/api/users/0/erase", json={"confirm": "admin"}).status_code == 422 and sec.owner.post("/api/users/999/erase", json={"confirm": "x"}).status_code == 404


def test_erasing_needs_an_administrator_and_not_yourself(sec):
    raj = sec.person("raj", "bidder")
    assert raj.post(f"/api/users/{uid(sec, 'raj')}/erase", json={"confirm": "raj"}).status_code == 403
    sec.owner.post("/api/users", json={"username": "second", "role": "admin", "password": "temporary-pass-1"})
    second = TestClient(sec.app)
    second.post("/api/login", json={"username": "second", "password": "temporary-pass-1"})
    second.post("/api/account/password", json={"current": "temporary-pass-1", "new": GOOD})
    assert second.post(f"/api/users/{uid(sec, 'second')}/erase", json={"confirm": "second"}).status_code == 422


def test_retention_settings_are_checked_and_old_log_entries_go(sec):
    from datetime import datetime, timedelta
    assert sec.owner.get("/api/settings").json()["audit_days"] == 365 and sec.owner.get("/api/settings").json()["tender_days"] == 45
    for bad in (5, 99999, "90", True, 30.5):
        assert sec.owner.put("/api/settings", json={"audit_days": bad}).status_code == 422, bad
    assert sec.owner.put("/api/settings", json={"audit_days": 90, "tender_days": 60}).status_code == 200
    assert sec.owner.get("/api/settings").json()["audit_days"] == 90
    from tender_digest.accounts import Accounts
    people = Accounts(sec.store)
    now = datetime(2026, 11, 15)
    people.log(now - timedelta(days=200), "x", "viewer", "old thing")
    people.log(now - timedelta(days=10), "x", "viewer", "recent thing")
    assert people.prune_audit(now, 90) == 1
    assert [e["action"] for e in people.activity(10, "x")] == ["recent thing"]


def test_collected_tenders_follow_the_retention_setting(sec):
    from datetime import datetime, timedelta
    sec.owner.put("/api/settings", json={"tender_days": 30})
    assert sec.store.count() == 1
    sec.store.prune(datetime.now() + timedelta(days=60), 30)
    assert sec.store.count() == 0


# ---- encryption at rest -----------------------------------------------------------------------------------------------

import base64 as _b64

from tender_digest import sealing
from tender_digest.sealing import SealError, Sealer

KEY = "correct-horse-battery-staple"
KEY2 = "another-long-private-phrase!"


def test_the_sealer_round_trips_and_rejects_the_wrong_key():
    s = Sealer(KEY)
    assert s.seal_text("JBSWY3DPEHPK3PXP").startswith("enc1:") and s.open_text(s.seal_text("JBSWY3DPEHPK3PXP")) == "JBSWY3DPEHPK3PXP"
    assert s.seal_text(s.seal_text("x")) == s.seal_text("x") or s.open_text(s.seal_text(s.seal_text("x"))) == "x"   # never sealed twice
    assert s.open_text("plain secret") == "plain secret"                                        # data from before the key still reads
    blob = s.seal_bytes(b"%PDF-1.4 certificate")
    assert blob.startswith(sealing.FILE_MAGIC) and b"certificate" not in blob and s.open_bytes(blob) == b"%PDF-1.4 certificate"
    other = Sealer(KEY2)
    with pytest.raises(SealError, match="different"):
        other.open_text(s.seal_text("x"))
    with pytest.raises(SealError, match="different"):
        other.open_bytes(blob)
    off = Sealer()
    assert not off.enabled and off.seal_text("x") == "x" and off.seal_bytes(b"x") == b"x"
    with pytest.raises(SealError, match="not set"):
        off.open_text(s.seal_text("x"))
    with pytest.raises(SealError, match="not set"):
        off.open_bytes(blob)


def test_a_short_key_is_refused_and_a_key_can_be_changed():
    with pytest.raises(SealError, match="at least 16"):
        Sealer("short")
    old = Sealer(KEY)
    token, blob = old.seal_text("secret"), old.seal_bytes(b"file")
    moving = Sealer(KEY2, [KEY])                                                # new key, old key still readable
    assert moving.open_text(token) == "secret" and moving.open_bytes(blob) == b"file"
    fresh = moving.rotate_text(token)
    assert Sealer(KEY2).open_text(fresh) == "secret"                            # now readable with the new key alone
    with pytest.raises(SealError):
        Sealer(KEY2).open_text(token)
    assert Sealer.from_env({"DATA_ENCRYPTION_KEY": KEY2, "DATA_ENCRYPTION_KEY_OLD": f"{KEY}, "}).has_old


@pytest.fixture
def locked(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", KEY)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    env = settings()
    manager = RunManager(env, store, lambda p, s: None, FakeSender(), background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, manager)
    owner = TestClient(app)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    t = kept(store)
    return type("L", (), {"owner": owner, "store": store, "vault": tmp_path / "vault", "app": app, "tender": t})


def upload(c, name="GST certificate", kind="gst", data=b"%PDF-1.4 TOP SECRET CERTIFICATE"):
    r = c.owner.post("/api/vault", json={"name": name, "kind": kind, "filename": "gst.pdf", "file_base64": _b64.b64encode(data).decode()})
    assert r.status_code == 200, r.text
    return r.json()


def test_uploaded_files_are_encrypted_on_disk_but_download_normally(locked):
    doc = upload(locked)
    on_disk = [f for f in locked.vault.iterdir() if f.is_file()]
    assert len(on_disk) == 1 and b"TOP SECRET" not in on_disk[0].read_bytes() and on_disk[0].read_bytes().startswith(sealing.FILE_MAGIC)
    got = locked.owner.get(f"/api/vault/{doc['id']}/file")
    assert got.status_code == 200 and got.content == b"%PDF-1.4 TOP SECRET CERTIFICATE" and "gst.pdf" in got.headers["content-disposition"]


def test_the_bid_pack_and_saved_notices_work_with_encryption(locked):
    import io
    import zipfile
    upload(locked)
    bid = locked.owner.post("/api/bids", json={"key": locked.tender.key}).json()["id"]
    locked.owner.post(f"/api/bids/{bid}/items", json={"text": "GST registration certificate", "kind": "document"})
    z = zipfile.ZipFile(io.BytesIO(locked.owner.get(f"/api/bids/{bid}/pack").content))
    assert z.read([n for n in z.namelist() if n.endswith(".pdf")][0]) == b"%PDF-1.4 TOP SECRET CERTIFICATE"


def test_saved_tender_notices_are_encrypted_and_read_back(tmp_path):
    from tender_digest.bids import BidBook
    book = BidBook(SeenStore(":memory:"), tmp_path / "v", Sealer(KEY))
    book.save_source("maha:T1", "Notice text kept for later")
    book.save_source("maha:T2", b"%PDF-1.4 notice")
    files = list((tmp_path / "v" / "sources").glob("*"))
    assert len(files) == 2 and all(f.read_bytes().startswith(sealing.FILE_MAGIC) and b"notice" not in f.read_bytes().lower() for f in files)
    assert book.load_source("maha:T1") == ("Notice text kept for later", "")
    assert _b64.b64decode(book.load_source("maha:T2")[1]) == b"%PDF-1.4 notice"
    assert book.source_bytes("maha:T1") == (".txt", b"Notice text kept for later") and book.has_source("maha:T2")
    plain = BidBook(SeenStore(":memory:"), tmp_path / "v")                         # the key is gone: a clear error, not garbage
    with pytest.raises(SealError, match="not set"):
        plain.load_source("maha:T1")


def test_files_and_secrets_stored_before_the_key_are_encrypted_at_start(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    env = settings()

    def boot():
        manager = RunManager(env, store, lambda p, s: None, FakeSender(), background=False)
        app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, manager)
        c = TestClient(app)
        c.post("/api/login", json={"username": "admin", "password": PASSWORD})
        return c

    c = boot()
    doc = upload(type("X", (), {"owner": c}))
    c.post("/api/users", json={"username": "meera", "role": "bidder", "password": "temporary-pass-1"})
    m = TestClient(c.app)
    m.post("/api/login", json={"username": "meera", "password": "temporary-pass-1"})
    m.post("/api/account/password", json={"current": "temporary-pass-1", "new": GOOD})
    secret = m.post("/api/account/2fa/start").json()["secret"]
    hook = c.post("/api/webhooks", json={"name": "x", "url": "https://hooks.example.com/x", "events": ["bid.created"]}).json()
    assert any(b"%PDF" in f.read_bytes() for f in (tmp_path / "vault").iterdir() if f.is_file())  # plain at first
    assert c.get("/api/backup/status").json()["encryption"]["enabled"] is False
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", KEY)
    c2 = boot()
    status = c2.get("/api/backup/status").json()["encryption"]
    assert status["enabled"] is True and status["plain_files"] == 0 and status["plain_secrets"] == 0 and not status["problem"]
    assert all(f.read_bytes().startswith(sealing.FILE_MAGIC) for f in (tmp_path / "vault").iterdir() if f.is_file())
    assert c2.get(f"/api/vault/{doc['id']}/file").content.startswith(b"%PDF-1.4")
    with store.lock:
        stored = store.db.execute("SELECT totp_secret FROM users WHERE username = 'meera'").fetchone()[0]
        hook_secret = store.db.execute("SELECT secret FROM webhooks").fetchone()[0]
    assert stored.startswith("enc1:") and secret not in stored and hook_secret.startswith("enc1:") and hook["secret"] not in hook_secret
    # two-step still works: the code made from the secret is accepted by the restarted service
    assert Sealer(KEY).open_text(stored) == secret
    m2 = TestClient(c2.app)
    assert m2.post("/api/login", json={"username": "meera", "password": GOOD}).status_code == 200
    code = accounts.totp_code(secret, int(time.time() // 30))
    assert m2.post("/api/account/2fa/enable", json={"code": code}).status_code == 200


def test_a_wrong_key_is_reported_not_silently_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", KEY)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    store = SeenStore(":memory:")
    env = settings()
    mk = lambda: web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, RunManager(env, store, lambda p, s: None, FakeSender(), background=False))  # noqa: E731
    c = TestClient(mk())
    c.post("/api/login", json={"username": "admin", "password": PASSWORD})
    doc = upload(type("X", (), {"owner": c}))
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", KEY2)
    c2 = TestClient(mk())
    c2.post("/api/login", json={"username": "admin", "password": PASSWORD})
    r = c2.get(f"/api/vault/{doc['id']}/file")
    assert r.status_code == 500 and "different DATA_ENCRYPTION_KEY" in r.json()["detail"]
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "short")
    with pytest.raises(SealError):
        mk()


# ---- first run and sample data ----------------------------------------------------------------------------------------

def test_setup_list_reflects_what_exists(sec):
    c = sec.owner
    s = c.get("/api/setup").json()
    done = {x["key"]: x["done"] for x in s["steps"]}
    assert done["profile"] is False and done["vault"] is False and done["bid"] is False and s["total"] == 7 and s["sample"] == {"loaded": False, "tenders": 0}
    c.put("/api/company-profile", json={"company_profile": "Class A civil contractor doing roads and bridges in Maharashtra."})
    c.put("/api/company-facts", json={"facts": [{"label": f"F{i}", "value": "x"} for i in range(3)]})
    for n in range(3):
        upload(type("X", (), {"owner": c}), name=f"Paper {n}", kind="gst")
    c.post("/api/bids", json={"key": sec.tender.key})
    done = {x["key"]: x["done"] for x in c.get("/api/setup").json()["steps"]}
    assert done["profile"] and done["facts"] and done["vault"] and done["bid"] and done["run"]        # the kept tender counts as a collected one
    assert sec.person("vera", "viewer").get("/api/setup").status_code == 200


def test_sample_data_loads_once_is_never_emailed_and_removes_cleanly(sec):
    from datetime import datetime
    c = sec.owner
    assert c.post("/api/sample-data").json() == {"added": 8, "tenders": 8}
    assert c.post("/api/sample-data").json()["added"] == 0                                       # a second press adds nothing
    page = c.get("/api/tenders", params={"status": "all", "portal": "sample"}).json()
    assert page["total"] == 8 and page["items"][0]["portal_name"] == "Sample data" and page["items"][0]["tender_id"].startswith("SAMPLE-")
    assert not [t for t in sec.store.pending(datetime.now()) if t.portal == "sample"]            # not in the daily email
    assert not [r for r in sec.store.ai_pending(datetime.now(), "x", 100) if r["portal"] == "sample"]  # and not sent to the AI
    assert c.get("/api/setup").json()["sample"] == {"loaded": True, "tenders": 8}
    key = page["items"][0]["key"]
    bid = c.post("/api/bids", json={"key": key}).json()["id"]
    c.post(f"/api/bids/{bid}/comments", json={"text": "trying it out"})
    other = c.post("/api/bids", json={"key": sec.tender.key}).json()["id"]
    assert [x["done"] for x in c.get("/api/setup").json()["steps"] if x["key"] == "bid"] == [True]
    gone = c.delete("/api/sample-data").json()
    assert gone == {"tenders": 8, "bids": 1}
    assert c.get("/api/tenders", params={"status": "all", "portal": "sample"}).json()["total"] == 0
    assert c.get(f"/api/bids/{bid}").status_code == 404 and c.get(f"/api/bids/{other}").status_code == 200   # real work is untouched
    assert c.get("/api/tenders", params={"status": "all"}).json()["total"] == 1                   # the real tender is still there


def test_sample_data_is_for_administrators(sec):
    assert sec.person("raj", "bidder").post("/api/sample-data").status_code == 403
    assert sec.person("vera", "viewer").delete("/api/sample-data").status_code == 403
