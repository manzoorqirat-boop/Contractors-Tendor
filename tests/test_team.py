"""Working on a bid as a team: who is who, assignments, comments, and approval before bidding."""
import pytest
from fastapi.testclient import TestClient

from tender_digest import web
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_bids import kept
from tests.test_web import PASSWORD, FakeSender, settings

GOOD = "purple-tractor-window"


@pytest.fixture
def team(monkeypatch):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    store = SeenStore(":memory:")
    env = settings()
    sender = FakeSender()
    manager = RunManager(env, store, lambda p, s: None, sender, background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin", app_url="https://tend.example.com"), env, store, manager)
    owner = TestClient(app)
    assert owner.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    tender = kept(store)

    def person(username, role, email=""):
        r = owner.post("/api/users", json={"username": username, "name": username.title(), "role": role, "password": "temporary-pass-1", "email": email})
        assert r.status_code == 200, r.text
        c = TestClient(app)
        c.post("/api/login", json={"username": username, "password": "temporary-pass-1"})
        c.post("/api/account/password", json={"current": "temporary-pass-1", "new": GOOD})
        return c

    def mails():
        return [(c[0][3], c[0][4], c[0][6]) for c in sender.calls]  # (recipients, subject, text)

    bid = owner.post("/api/bids", json={"key": tender.key}).json()["id"]
    return type("T", (), {"app": app, "owner": owner, "person": person, "bid": bid, "mails": mails, "sender": sender, "store": store, "key": tender.key})


def first_item(team):
    return team.owner.get(f"/api/bids/{team.bid}").json()["items"][0]


# ---- people and their email addresses ---------------------------------------------------------------------------------------

def test_emails_are_checked_and_never_shown_in_the_people_picker(team):
    assert team.owner.post("/api/users", json={"username": "bad", "role": "bidder", "password": "temporary-pass-1", "email": "not an email"}).status_code == 422
    meera = team.person("meera", "bidder", "Meera@Example.com")
    listed = team.owner.get("/api/users").json()["users"]
    assert next(u for u in listed if u["username"] == "meera")["email"] == "meera@example.com"  # lower-cased
    assert meera.post("/api/account/email", json={"email": "meera.rao@example.com"}).json() == {"email": "meera.rao@example.com"}
    assert meera.post("/api/account/email", json={"email": "nope"}).status_code == 422
    assert meera.get("/api/me").json()["user"]["email"] == "meera.rao@example.com"
    directory = meera.get("/api/people").json()["people"]
    assert {"username": "meera", "name": "Meera"} in directory and {"username": "admin", "name": "Owner"} in directory
    assert "@" not in str(directory)                                                         # no contact details in the picker
    assert team.owner.post("/api/account/email", json={"email": "a@b.co"}).status_code == 422  # the owner login stores no email
    assert team.owner.patch(f"/api/users/{[u for u in listed if u['username'] == 'meera'][0]['id']}", json={"email": ""}).status_code == 200


# ---- assigning checklist items ------------------------------------------------------------------------------------------------

def test_assigning_an_item_tells_the_person_and_it_appears_in_their_work(team):
    meera = team.person("meera", "bidder", "meera@example.com")
    bob = team.person("bob", "bidder", "bob@example.com")
    item = first_item(team)
    r = bob.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"owner": "meera"})
    assert r.status_code == 200
    sent = team.mails()[-1]
    assert sent[0] == ["meera@example.com"] and "Bob gave you a task" in sent[1] and item["text"] in sent[2] and "Closes:" in sent[2]
    assert [i["item_id"] for i in meera.get("/api/my-work").json()["items"]] == [item["id"]]
    assert bob.get("/api/my-work").json()["items"] == []
    n = len(team.mails())
    bob.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"owner": "meera"})            # no change: no second email
    bob.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"note": "call the CA"})        # a note is not an assignment
    meera.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"owner": "meera"})           # assigning yourself needs no email
    assert len(team.mails()) == n
    bob.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"owner": "Site engineer"})     # free text still works, and tells nobody
    assert len(team.mails()) == n
    bob.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"owner": "meera"})
    meera.patch(f"/api/bids/{team.bid}/items/{item['id']}", json={"done": True})
    assert meera.get("/api/my-work").json()["items"] == []                                    # done items leave the list


def test_my_work_is_sorted_by_closing_and_skips_finished_bids(team):
    meera = team.person("meera", "bidder", "meera@example.com")
    other = kept(team.store, 2, closing_days=3)
    b2 = team.owner.post("/api/bids", json={"key": other.key}).json()["id"]
    for bid in (team.bid, b2):
        it = team.owner.get(f"/api/bids/{bid}").json()["items"][0]
        team.owner.patch(f"/api/bids/{bid}/items/{it['id']}", json={"owner": "MEERA"})        # case does not matter
    got = meera.get("/api/my-work").json()["items"]
    assert [g["bid_id"] for g in got] == [b2, team.bid]                                       # the one closing in 3 days comes first
    team.owner.patch(f"/api/bids/{b2}", json={"stage": "dropped"})
    assert [g["bid_id"] for g in meera.get("/api/my-work").json()["items"]] == [team.bid]


# ---- comments --------------------------------------------------------------------------------------------------------------------

def test_comments_are_kept_listed_counted_and_removed_with_the_bid(team):
    bob = team.person("bob", "bidder", "bob@example.com")
    assert bob.post(f"/api/bids/{team.bid}/comments", json={"text": "   "}).status_code == 422
    assert bob.post(f"/api/bids/{team.bid}/comments", json={"text": "x" * 2001}).status_code == 422
    c = bob.post(f"/api/bids/{team.bid}/comments", json={"text": "GST certificate is renewed"}).json()
    assert c["username"] == "bob"
    got = team.owner.get(f"/api/bids/{team.bid}/comments").json()["comments"]
    assert [x["text"] for x in got] == ["GST certificate is renewed"] and team.owner.get(f"/api/bids/{team.bid}").json()["comment_count"] == 1
    assert team.owner.delete(f"/api/bids/{team.bid}").status_code == 200
    assert team.store.db.execute("SELECT COUNT(*) FROM bid_comments").fetchone()[0] == 0


def test_mentions_and_the_bid_owner_are_emailed_but_not_the_author(team):
    meera = team.person("meera", "bidder", "meera@example.com")
    bob = team.person("bob", "bidder", "bob@example.com")
    team.person("vera", "bidder", "")                                                        # no email: nobody to tell, no error
    team.owner.patch(f"/api/bids/{team.bid}", json={"owner": "meera"})
    n = len(team.mails())
    bob.post(f"/api/bids/{team.bid}/comments", json={"text": "@bob @vera can you check the EMD, @nobody?"})
    new = team.mails()[n:]
    assert [m[0] for m in new] == [["meera@example.com"]]                                     # the owner; bob wrote it, vera has no address
    meera.post(f"/api/bids/{team.bid}/comments", json={"text": "thanks @bob"})
    assert team.mails()[-1][0] == ["bob@example.com"] and "<" not in team.mails()[-1][2]
    assert "https://tend.example.com/#/bids/" in team.mails()[-1][2]                          # a link back, from APP_URL


def test_who_may_remove_a_comment(team):
    bob = team.person("bob", "bidder")
    meera = team.person("meera", "bidder")
    cid = bob.post(f"/api/bids/{team.bid}/comments", json={"text": "mine"}).json()["id"]
    assert meera.delete(f"/api/bids/{team.bid}/comments/{cid}").status_code == 404            # not hers to remove
    assert bob.delete(f"/api/bids/{team.bid}/comments/{cid}").status_code == 200
    cid = bob.post(f"/api/bids/{team.bid}/comments", json={"text": "again"}).json()["id"]
    assert team.owner.delete(f"/api/bids/{team.bid}/comments/{cid}").status_code == 200       # an administrator can


def test_view_only_accounts_read_but_do_not_comment(team):
    vera = team.person("vera", "viewer")
    team.owner.post(f"/api/bids/{team.bid}/comments", json={"text": "hello"})
    assert vera.get(f"/api/bids/{team.bid}/comments").status_code == 200
    assert vera.post(f"/api/bids/{team.bid}/comments", json={"text": "hi"}).status_code == 403
    assert vera.get("/api/my-work").status_code == 200


# ---- approval to bid --------------------------------------------------------------------------------------------------------------

def test_a_bidder_cannot_move_on_without_approval_when_it_is_required(team):
    admin = team.person("alia", "admin", "alia@example.com")
    bob = team.person("bob", "bidder", "bob@example.com")
    assert bob.patch(f"/api/bids/{team.bid}", json={"stage": "preparing"}).status_code == 200  # not required yet: free to move
    team.owner.patch(f"/api/bids/{team.bid}", json={"stage": "interested"})
    assert team.owner.put("/api/settings", json={"require_signoff": True}).status_code == 200
    r = bob.patch(f"/api/bids/{team.bid}", json={"stage": "preparing"})
    assert r.status_code == 422 and "approval" in r.json()["detail"]
    assert bob.patch(f"/api/bids/{team.bid}", json={"stage": "dropped"}).status_code == 200      # saying no never needs approval
    team.owner.patch(f"/api/bids/{team.bid}", json={"stage": "interested"})
    # ask
    n = len(team.mails())
    assert bob.post(f"/api/bids/{team.bid}/signoff", json={"action": "request", "note": "Good margin, we have the plant"}).json()["signoff"]["state"] == "requested"
    asked = team.mails()[n]
    assert asked[0] == ["alia@example.com"] and "Bob asks approval" in asked[1] and "Good margin" in asked[2]
    assert bob.post(f"/api/bids/{team.bid}/signoff", json={"action": "approve"}).status_code == 403   # a bidder cannot approve
    assert bob.patch(f"/api/bids/{team.bid}", json={"stage": "preparing"}).status_code == 422          # asking is not approval
    # approve
    ok = admin.post(f"/api/bids/{team.bid}/signoff", json={"action": "approve", "note": "Go ahead"}).json()["signoff"]
    assert ok["state"] == "approved" and ok["by"] == "alia" and ok["requested_by"] == "bob"
    done = team.mails()[-1]
    assert done[0] == ["bob@example.com"] and "approved" in done[1] and "Go ahead" in done[2]
    assert bob.patch(f"/api/bids/{team.bid}", json={"stage": "preparing"}).status_code == 200
    assert team.owner.get(f"/api/bids/{team.bid}").json()["signoff"]["state"] == "approved"


def test_declined_withdrawn_and_admin_shortcuts(team):
    admin = team.person("alia", "admin", "alia@example.com")
    bob = team.person("bob", "bidder", "bob@example.com")
    meera = team.person("meera", "bidder", "meera@example.com")
    team.owner.put("/api/settings", json={"require_signoff": True})
    bob.post(f"/api/bids/{team.bid}/signoff", json={"action": "request"})
    assert meera.post(f"/api/bids/{team.bid}/signoff", json={"action": "withdraw"}).status_code == 403   # not hers to withdraw
    assert admin.post(f"/api/bids/{team.bid}/signoff", json={"action": "decline", "note": "Too far away"}).json()["signoff"]["state"] == "declined"
    assert bob.patch(f"/api/bids/{team.bid}", json={"stage": "preparing"}).status_code == 422             # a decline blocks too
    assert bob.post(f"/api/bids/{team.bid}/signoff", json={"action": "withdraw"}).json() == {"signoff": None}
    assert bob.post(f"/api/bids/{team.bid}/signoff", json={"action": "nonsense"}).status_code == 422
    # an administrator needs no approval for themselves, and can approve without anyone asking
    assert admin.patch(f"/api/bids/{team.bid}", json={"stage": "preparing"}).status_code == 200
    team.owner.patch(f"/api/bids/{team.bid}", json={"stage": "submitted"})
    assert bob.post(f"/api/bids/{team.bid}/signoff", json={"action": "request"}).status_code == 422       # only while still at Interested


def test_only_administrators_change_the_approval_setting(team):
    bob = team.person("bob", "bidder")
    assert bob.put("/api/settings", json={"require_signoff": True}).status_code == 403
    assert team.owner.put("/api/settings", json={"require_signoff": "yes"}).status_code == 422
    assert team.owner.get("/api/settings").json()["require_signoff"] is False


def test_no_email_is_sent_when_mail_is_not_set_up(monkeypatch):
    store = SeenStore(":memory:")
    env = settings(brevo_api_key="", mail_from="")
    sender = FakeSender()
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, RunManager(env, store, lambda p, s: None, sender, background=False))
    c = TestClient(app)
    c.post("/api/login", json={"username": "admin", "password": PASSWORD})
    bid = c.post("/api/bids", json={"key": kept(store).key}).json()["id"]
    assert c.post(f"/api/bids/{bid}/comments", json={"text": "@someone hi"}).status_code == 200 and sender.calls == []
