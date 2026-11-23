"""The WhatsApp assistant: only Meta can talk to it, only listed numbers get answers, and it only ever reads."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tender_digest import web, whatsapp
from tender_digest.bids import BidBook
from tender_digest.gepnic import Tender
from tender_digest.money import MoneyBook
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_portals import PASSWORD, env_settings

NOW = datetime(2026, 11, 18, 9, 0)
SECRET, TOKEN, PHONE = "app-secret-1", "verify-me", "1055551234"
ENV = {"WHATSAPP_VERIFY_TOKEN": TOKEN, "WHATSAPP_APP_SECRET": SECRET, "WHATSAPP_ACCESS_TOKEN": "tok", "WHATSAPP_PHONE_NUMBER_ID": PHONE,
       "WHATSAPP_ALLOWED": "919876543210:bidder, +91 98123 45678:viewer"}


def sign(body: bytes, secret=SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(text="closing", sender="919876543210", phone=PHONE, mid="wamid.1", kind="text"):
    msg = {"from": sender, "id": mid, "type": kind, "timestamp": "1"}
    if kind == "text":
        msg["text"] = {"body": text}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": phone}, "messages": [msg]}}]}]}


# ---- configuration -----------------------------------------------------------------------------------------------------

def test_the_settings_are_read_from_the_environment_and_what_is_missing_is_named():
    cfg = whatsapp.Config.from_env(ENV)
    assert cfg.enabled and cfg.allowed == {"919876543210": "bidder", "919812345678": "viewer"} and cfg.version == "v21.0" and cfg.phone_number_id == PHONE
    bare = whatsapp.Config.from_env({})
    assert not bare.enabled and set(bare.missing()) == {"WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ALLOWED"}
    assert whatsapp.Config.from_env({**ENV, "WHATSAPP_APP_SECRET": ""}).missing() == ["WHATSAPP_APP_SECRET"]


def test_a_number_with_an_unknown_role_or_no_digits_is_not_allowed_and_a_bare_number_is_a_viewer():
    cfg = whatsapp.Config.from_env({**ENV, "WHATSAPP_ALLOWED": "9111:owner, abc:bidder, 9122, 9133:admin"})
    assert cfg.allowed == {"9122": "viewer", "9133": "admin"}


def test_an_odd_api_version_falls_back_so_it_cannot_change_where_replies_go():
    assert whatsapp.Config.from_env({**ENV, "WHATSAPP_API_VERSION": "v19.0"}).version == "v19.0"
    assert whatsapp.Config.from_env({**ENV, "WHATSAPP_API_VERSION": "../evil.example/x"}).version == "v21.0"
    assert whatsapp.Config.from_env({**ENV, "WHATSAPP_PHONE_NUMBER_ID": "10/../x"}).phone_number_id == "10"


# ---- is it really Meta -------------------------------------------------------------------------------------------------

def test_only_a_body_signed_with_the_app_secret_is_accepted():
    body = json.dumps(payload()).encode()
    assert whatsapp.verify_signature(SECRET, body, sign(body))
    assert whatsapp.verify_signature(SECRET, body, sign(body).upper().replace("SHA256=", "sha256="))        # hex case does not matter
    assert not whatsapp.verify_signature(SECRET, body + b" ", sign(body))                                   # a changed body
    assert not whatsapp.verify_signature(SECRET, body, sign(body, "other"))                                  # a different secret
    for header in (None, "", "sha256=", "md5=abc", "sha256=" + "0" * 64, sign(body)[7:]):
        assert not whatsapp.verify_signature(SECRET, body, header)
    assert not whatsapp.verify_signature("", body, sign(body, ""))                                           # no secret configured: nothing is ever valid


# ---- reading what Meta sends -------------------------------------------------------------------------------------------

def test_a_text_message_sent_to_our_number_is_read():
    got = whatsapp.parse_incoming(payload("find drain", "+91 98765 43210"), PHONE)
    assert [(m.id, m.sender, m.text) for m in got] == [("wamid.1", "919876543210", "find drain")]


@pytest.mark.parametrize("bad", [None, [], "x", {}, {"object": "page"}, {"object": "whatsapp_business_account", "entry": [None, {"changes": [None, {"value": None}, {"value": {"messages": "x"}}]}]}])
def test_anything_that_is_not_a_message_is_ignored_without_error(bad):
    assert whatsapp.parse_incoming(bad, PHONE) == []


def test_messages_for_another_number_and_non_text_messages_are_ignored():
    assert whatsapp.parse_incoming(payload(phone="999"), PHONE) == []
    assert whatsapp.parse_incoming(payload(kind="image"), PHONE) == []
    status = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": PHONE}, "statuses": [{"id": "x", "status": "delivered"}]}}]}]}
    assert whatsapp.parse_incoming(status, PHONE) == []


# ---- repeats and floods ------------------------------------------------------------------------------------------------

def test_a_resent_message_is_answered_once_and_a_flood_is_dropped():
    t = [0.0]
    guard = whatsapp.Guard(limit=(3, 60), clock=lambda: t[0])
    assert guard.allow("a", "91") and not guard.allow("a", "91")                                             # Meta resends: only once
    assert guard.allow("b", "91") and guard.allow("c", "91") and not guard.allow("d", "91")                  # fourth in a minute from one number
    assert guard.allow("e", "92")                                                                              # another number is not affected
    t[0] = 120.0
    assert guard.allow("f", "91")                                                                              # the window moves on


# ---- sending -----------------------------------------------------------------------------------------------------------

class Post:
    def __init__(self, status=200, boom=None):
        self.calls, self.status, self.boom = [], status, boom

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.boom:
            raise self.boom
        return type("R", (), {"status_code": self.status})()


def test_a_reply_goes_to_metas_api_with_the_token_and_never_follows_redirects():
    post, cfg = Post(), whatsapp.Config.from_env(ENV)
    assert whatsapp.send_text(cfg, "919876543210", "hello", post=post) is True
    url, kw = post.calls[0]
    assert url == f"https://graph.facebook.com/v21.0/{PHONE}/messages" and kw["headers"] == {"Authorization": "Bearer tok"} and kw["allow_redirects"] is False
    assert kw["json"] == {"messaging_product": "whatsapp", "to": "919876543210", "type": "text", "text": {"body": "hello", "preview_url": False}}
    whatsapp.send_text(cfg, "91", "x" * 9000, post=post)
    assert len(post.calls[1][1]["json"]["text"]["body"]) == 3500


@pytest.mark.parametrize("post", [Post(status=401), Post(status=500), Post(boom=ConnectionError("down")), Post(boom=TimeoutError())])
def test_a_failed_reply_is_reported_not_raised(post):
    assert whatsapp.send_text(whatsapp.Config.from_env(ENV), "91", "hi", post=post) is False


# ---- the answers -------------------------------------------------------------------------------------------------------

def tender(i, title="Construction of drain at Nashik", org="PWD||Nashik", closing_days=2, value=2_500_000, base=NOW):
    return Tender(portal="maha", tender_id=f"2026_PWD_{i}", title=title, reference="", description=title, organisation=org, category="Works", location="Nashik", value=value, emd=25_000,
                  published=base, closing=base + timedelta(days=closing_days), opening=base + timedelta(days=closing_days + 2))


@pytest.fixture
def world():
    store = SeenStore(":memory:")
    bids = BidBook(store)
    money = MoneyBook(bids)
    asked = []

    def ai_filters(question):
        asked.append(question)
        return {"filters": {"q": "drain"}, "sort": "closing", "explanation": "Drain tenders"}

    a = whatsapp.Assistant(store, bids, money, lambda: NOW, ai_filters)
    return type("W", (), {"store": store, "bids": bids, "money": money, "a": a, "asked": asked})


def test_help_and_the_unknown_get_helpful_answers(world):
    for text in ("help", "Hi", "MENU", "", "  ?  ", "help me"):
        assert world.a.reply(text, "viewer") == whatsapp.HELP
    plain = whatsapp.Assistant(world.store, world.bids, world.money, lambda: NOW)
    assert "did not understand" in plain.reply("tell me a joke", "viewer")


def test_closing_lists_what_closes_in_three_days_soonest_first(world):
    world.store.keep(tender(1, "Late closing work", closing_days=3), NOW)
    world.store.keep(tender(2, "Soon closing work", closing_days=1), NOW)
    world.store.keep(tender(3, "Far away work", closing_days=20), NOW)
    out = world.a.reply("closing", "viewer")
    assert out.startswith("2 closing in 3 days:") and out.index("Soon closing") < out.index("Late closing") and "Far away" not in out
    assert "ID 2026_PWD_2" in out and "₹25.00 lakh" in out and "19 Nov" in out
    assert world.a.reply("what closes soon?", "viewer") == out
    assert SeenStore(":memory:") and whatsapp.Assistant(SeenStore(":memory:"), world.bids, world.money, lambda: NOW).reply("today", "viewer") == "Nothing is closing in the next 3 days."


def test_new_lists_what_was_found_since_yesterday(world):
    world.store.keep(tender(1, "Fresh drain work"), NOW)
    world.store.keep(tender(2, "Old work"), NOW - timedelta(days=5))
    out = world.a.reply("new", "viewer")
    assert "1 new since yesterday" in out and "Fresh drain work" in out and "Old work" not in out


def test_find_searches_open_tenders_and_says_so_when_nothing_matches(world):
    world.store.keep(tender(1, "Construction of drain at Nashik"), NOW)
    world.store.keep(tender(2, "Repair of school building"), NOW)
    out = world.a.reply("find drain", "viewer")
    assert "1 open tender match" in out and "Construction of drain" in out and "school" not in out
    assert world.a.reply("search tunnel", "viewer") == "No open tenders match “tunnel”."


def test_bids_deadlines_and_one_bid_are_answered_from_the_workspace(world):
    t = tender(1, "Drain works for the school", closing_days=2)
    world.store.keep(t, NOW)
    bid, _ = world.bids.create_from_tender(t.key, NOW)
    assert world.a.reply("bids", "viewer").startswith("1 bid in progress:") and "interested" in world.a.reply("my bids", "viewer")
    status = world.a.reply("bid 2026_PWD_1", "viewer")
    assert "Drain works" in status and "Stage: interested" in status and "Checklist:" in status and "Value: ₹25.00 lakh" in status
    assert "No bid on tender ID 2026_PWD_999" in world.a.reply("status 2026_PWD_999", "viewer")
    assert "Bid closes" in world.a.reply("deadlines", "viewer") or "Coming up" in world.a.reply("deadlines", "viewer")
    assert whatsapp.Assistant(SeenStore(":memory:"), BidBook(SeenStore(":memory:")), world.money, lambda: NOW).reply("bids", "viewer") == "You have no bids in progress."


def test_cash_is_for_bidders_and_admins_only(world):
    t = tender(1)
    world.store.keep(t, NOW)
    bid, _ = world.bids.create_from_tender(t.key, NOW)
    world.money.add(bid["id"], {"kind": "emd", "amount": 200000, "instrument": "online", "status": "paid", "paid_on": "2026-11-10", "refund_due": "2026-12-10"}, NOW)
    refused = world.a.reply("cash", "viewer")
    assert "bidders and administrators" in refused and "2,00,000" not in refused and "₹2" not in refused
    for role in ("bidder", "admin"):
        out = world.a.reply("cash", role)
        assert "Cash locked in EMDs and deposits: ₹2.00 lakh" in out and "Refunds due in 30 days: ₹2.00 lakh" in out
    assert world.a.reply("emd", "bidder") == world.a.reply("money", "bidder")


def test_free_text_goes_to_the_ai_for_filters_and_is_run_as_a_search(world):
    world.store.keep(tender(1, "Construction of drain at Nashik"), NOW)
    out = world.a.reply("any drain tenders near Nashik this week?", "viewer")
    assert out.startswith("Drain tenders: 1 found.") and "Construction of drain" in out and world.asked == ["any drain tenders near Nashik this week?"]


def test_an_ai_that_fails_or_understands_nothing_never_breaks_the_reply(world):
    def boom(q):
        raise RuntimeError("busy")
    assert "could not work that out" in whatsapp.Assistant(world.store, world.bids, world.money, lambda: NOW, boom).reply("anything", "viewer")
    empty = whatsapp.Assistant(world.store, world.bids, world.money, lambda: NOW, lambda q: {"filters": {}})
    assert "did not understand" in empty.reply("blah", "viewer")


def test_a_long_answer_is_cut_to_fit_a_phone_with_a_count_of_the_rest(world):
    for i in range(40):
        world.store.keep(tender(i, f"Construction of drain number {i} at a very long named place in Nashik district"), NOW)
    out = world.a.reply("closing", "viewer")
    assert len(out) <= whatsapp.MAX_REPLY and "…and " in out and "more. Open the app" in out and out.startswith("40 closing in 3 days:")


def test_the_assistant_only_reads(world):
    """Whatever is asked, nothing in the workspace changes."""
    t = tender(1)
    world.store.keep(t, NOW)
    world.bids.create_from_tender(t.key, NOW)
    before = [world.store.db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0] for tbl in ("seen", "bids", "bid_money", "bid_items", "settings")]
    for text in ("help", "closing", "new", "bids", "deadlines", "cash", "find drain", "bid 2026_PWD_1", "delete everything", "stage 2026_PWD_1 won", "bid 2026_PWD_1; DROP TABLE bids"):
        world.a.reply(text, "admin")
    assert [world.store.db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0] for tbl in ("seen", "bids", "bid_money", "bid_items", "settings")] == before


# ---- the webhook -------------------------------------------------------------------------------------------------------

@pytest.fixture
def hook(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    sent = Post()
    monkeypatch.setattr(whatsapp, "POST", sent)
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False, app_url="https://tend.example.in"), env, store, RunManager(env, store, background=False))
    real = datetime.now()                                                      # the webhook answers by the real clock
    store.keep(tender(1, "Closing very soon", closing_days=1, base=real), real)
    return type("H", (), {"client": TestClient(app), "sent": sent, "store": store, "app": app})


def post(hook, body, secret=SECRET, headers=None):
    raw = json.dumps(body).encode()
    return hook.client.post("/api/whatsapp/webhook", content=raw, headers={"content-type": "application/json", "x-hub-signature-256": sign(raw, secret), **(headers or {})})


def test_metas_registration_check_gets_its_challenge_back_only_with_the_right_token(hook):
    ok = hook.client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    for params in ({"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}, {"hub.mode": "unsubscribe", "hub.verify_token": TOKEN, "hub.challenge": "1"}, {"hub.challenge": "1"}):
        assert hook.client.get("/api/whatsapp/webhook", params=params).status_code == 403
    assert hook.client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "<script>1"}).text == "script1"


def test_a_signed_message_from_a_listed_number_is_answered_by_replying_to_that_number(hook):
    r = post(hook, payload("closing"))
    assert r.status_code == 200 and r.json() == {"ok": True, "handled": 1}
    assert len(hook.sent.calls) == 1
    url, kw = hook.sent.calls[0]
    assert url.endswith(f"/{PHONE}/messages") and kw["json"]["to"] == "919876543210" and "Closing very soon" in kw["json"]["text"]["body"]


def test_a_message_with_a_bad_or_missing_signature_is_refused_and_nothing_is_sent(hook):
    assert post(hook, payload(), secret="wrong").status_code == 403
    raw = json.dumps(payload()).encode()
    assert hook.client.post("/api/whatsapp/webhook", content=raw, headers={"content-type": "application/json"}).status_code == 403
    assert hook.sent.calls == []


def test_a_number_that_is_not_listed_gets_no_reply_at_all(hook):
    r = post(hook, payload("closing", sender="919000000000"))
    assert r.status_code == 200 and r.json()["handled"] == 0 and hook.sent.calls == []


def test_a_resent_message_is_answered_once(hook):
    post(hook, payload(mid="same"))
    post(hook, payload(mid="same"))
    assert len(hook.sent.calls) == 1


def test_the_viewer_number_cannot_see_cash_and_the_bidder_number_can(hook):
    post(hook, payload("cash", sender="919812345678", mid="a"))
    post(hook, payload("cash", sender="919876543210", mid="b"))
    bodies = {kw["json"]["to"]: kw["json"]["text"]["body"] for _, kw in hook.sent.calls}
    assert "bidders and administrators" in bodies["919812345678"] and "Cash locked" in bodies["919876543210"]


def test_unreadable_bodies_and_other_message_kinds_are_acknowledged_quietly(hook):
    raw = b"not json"
    assert hook.client.post("/api/whatsapp/webhook", content=raw, headers={"x-hub-signature-256": sign(raw)}).json() == {"ok": True}
    assert post(hook, payload(kind="image")).json()["handled"] == 0 and hook.sent.calls == []


def test_a_failing_reply_does_not_make_the_webhook_fail(hook):
    hook.sent.boom = ConnectionError("down")
    assert post(hook, payload()).status_code == 200


def test_an_installation_without_the_settings_refuses_the_webhook(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    c = TestClient(app)
    raw = json.dumps(payload()).encode()
    assert c.post("/api/whatsapp/webhook", content=raw, headers={"x-hub-signature-256": sign(raw)}).status_code == 503
    assert c.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}).status_code == 403


def test_the_status_page_shows_what_is_set_up_without_revealing_the_secrets_or_the_numbers(hook):
    assert hook.client.get("/api/whatsapp/status").status_code == 401
    hook.client.post("/api/login", json={"password": PASSWORD})
    s = hook.client.get("/api/whatsapp/status").json()
    assert s["enabled"] is True and s["missing"] == [] and s["webhook_url"] == "https://tend.example.in/api/whatsapp/webhook"
    assert s["numbers"] == [{"ending": "3210", "role": "bidder"}, {"ending": "5678", "role": "viewer"}]
    text = json.dumps(s)
    assert SECRET not in text and "tok" not in text.replace("token", "") and "919876543210" not in text


def test_the_preview_answers_like_the_assistant_without_any_whatsapp_setup_and_sends_nothing(hook, monkeypatch):
    c = hook.client
    assert c.post("/api/whatsapp/try", json={"text": "help"}).status_code == 401
    c.post("/api/login", json={"password": PASSWORD})
    got = c.post("/api/whatsapp/try", json={"text": "closing", "role": "viewer"}).json()
    assert got["role"] == "viewer" and "Closing very soon" in got["reply"]
    assert "TendAI assistant" in c.post("/api/whatsapp/try", json={"text": "help"}).json()["reply"]
    assert "for bidders and administrators" in c.post("/api/whatsapp/try", json={"text": "cash", "role": "viewer"}).json()["reply"]
    assert "Cash locked" in c.post("/api/whatsapp/try", json={"text": "cash", "role": "bidder"}).json()["reply"]
    assert c.post("/api/whatsapp/try", json={"text": "help", "role": "owner"}).json()["role"] == "viewer"      # an unknown role is treated as the least
    assert c.post("/api/whatsapp/try", json={"text": "x" * 501}).status_code == 422
    assert hook.sent.calls == []                                                                              # nothing went to Meta


def test_the_preview_is_for_administrators_only(hook):
    c = hook.client
    c.post("/api/login", json={"password": PASSWORD})
    c.post("/api/users", json={"username": "bob", "name": "Bob", "role": "bidder", "password": "a-long-password-1"})
    bob = TestClient(hook.app)
    r = bob.post("/api/login", json={"username": "bob", "password": "a-long-password-1"})
    assert r.status_code == 200
    assert bob.post("/api/whatsapp/try", json={"text": "help"}).status_code == 403
