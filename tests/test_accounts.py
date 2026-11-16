"""People, roles and the activity log."""
import pytest
from fastapi.testclient import TestClient

from tender_digest import accounts, web
from tender_digest.accounts import required_role
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, settings

GOOD = "purple-tractor-window"  # 21 characters


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    store = SeenStore(":memory:")
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), settings(), store)
    owner = TestClient(app)
    assert owner.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200

    def person(username, role, password=GOOD, name=""):
        """Creates an account, signs it in, and (like a real person) replaces the temporary password."""
        r = owner.post("/api/users", json={"username": username, "name": name or username.title(), "role": role, "password": "temporary-pass-1"})
        assert r.status_code == 200, r.text
        c = TestClient(app)
        assert c.post("/api/login", json={"username": username, "password": "temporary-pass-1"}).status_code == 200
        assert c.post("/api/account/password", json={"current": "temporary-pass-1", "new": password}).status_code == 200
        return c, r.json()["id"]

    return type("W", (), {"app": app, "owner": owner, "person": person, "store": store})


# ---- the permission table ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,role", [
    ("GET", "/api/tenders", "viewer"), ("GET", "/api/bids/3", "viewer"), ("GET", "/api/me", "viewer"), ("GET", "/api/portals", "viewer"),
    ("POST", "/api/account/password", "viewer"),
    ("GET", "/api/vault/4/file", "bidder"), ("GET", "/api/vault", "viewer"),
    ("POST", "/api/tenders/star", "bidder"), ("POST", "/api/bids", "bidder"), ("PATCH", "/api/bids/2", "bidder"), ("DELETE", "/api/bids/2", "bidder"),
    ("POST", "/api/runs", "bidder"), ("POST", "/api/ai/score", "bidder"), ("PUT", "/api/company-facts", "bidder"), ("POST", "/api/vault", "bidder"),
    ("GET", "/api/settings", "admin"), ("PUT", "/api/settings", "admin"), ("POST", "/api/settings/test-email", "admin"),
    ("POST", "/api/portals", "admin"), ("DELETE", "/api/portals/x", "admin"), ("POST", "/api/portals/x/check", "admin"),
    ("GET", "/api/users", "admin"), ("POST", "/api/users", "admin"), ("PATCH", "/api/users/2", "admin"),
    ("GET", "/api/audit", "admin"), ("GET", "/api/backup", "admin"), ("GET", "/api/backup/status", "admin"),
])
def test_what_each_request_needs(method, path, role):
    assert required_role(method, path) == role


def test_every_route_has_a_deliberate_rule():
    """No route is left at the default by accident: read-only routes are GET, and anything else needs at least the bidder role."""
    world_app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), settings(), SeenStore(":memory:"))
    for route in world_app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in ("/api/login", "/api/logout", "/api/health", "/api/me", "/api/branding", "/api/account/password"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            role = required_role(method, path)
            assert role in accounts.ROLES
            if method != "GET":
                assert accounts.RANK[role] >= accounts.RANK["bidder"], (method, path)  # nothing that changes data is open to view-only accounts


# ---- passwords -----------------------------------------------------------------------------------------------------

def test_passwords_are_hashed_and_checked():
    h = accounts.hash_password(GOOD)
    assert GOOD not in h and h.startswith("scrypt$") and accounts.hash_password(GOOD) != h  # salted: the same password never hashes the same way twice
    assert accounts.verify_password(GOOD, h) and not accounts.verify_password(GOOD + "x", h)
    assert not accounts.verify_password(GOOD, "garbage") and not accounts.verify_password(GOOD, "md5$1$2$3$4$5")


@pytest.mark.parametrize("pw,why", [("short", "at least 12"), ("aaaaaaaaaaaaaaaa", "repeats"), ("administrator", "common"), ("meera-is-great-1", "username")])
def test_weak_passwords_are_refused_with_a_reason(pw, why):
    assert why in accounts.password_problem(pw, "meera")
    assert accounts.password_problem(GOOD, "meera") == ""


# ---- creating people --------------------------------------------------------------------------------------------------

def test_only_an_admin_manages_people(world):
    c, _ = world.person("viewer1", "viewer")
    assert c.get("/api/users").status_code == 403 and c.post("/api/users", json={"username": "x" * 5, "password": GOOD}).status_code == 403
    assert c.get("/api/audit").status_code == 403
    users = world.owner.get("/api/users").json()
    assert users["users"][0]["owner"] is True and users["users"][0]["username"] == "admin" and users["min_password"] == 12
    assert [r["key"] for r in users["roles"]] == ["viewer", "bidder", "admin"]


def test_new_accounts_are_checked(world):
    o = world.owner
    ok = {"username": "meera", "name": "Meera Rao", "role": "bidder", "password": GOOD}
    assert o.post("/api/users", json=ok).status_code == 200
    bad = {
        "dup": {**ok, "username": "MEERA"}, "short username": {**ok, "username": "ab"}, "bad chars": {**ok, "username": "me era!"},
        "reserved": {**ok, "username": "admin"}, "role": {**ok, "username": "m2", "role": "superuser"}, "weak": {**ok, "username": "m3", "password": "short"},
        "pw has name": {**ok, "username": "m4x", "password": "m4x-is-my-name-1"},
    }
    for name, body in bad.items():
        assert o.post("/api/users", json=body).status_code == 422, name
    listed = [u["username"] for u in o.get("/api/users").json()["users"]]
    assert listed == ["admin", "meera"]
    assert "pw_hash" not in str(o.get("/api/users").json())  # hashes never leave the server


def test_a_temporary_password_must_be_replaced_before_anything_else(world):
    world.owner.post("/api/users", json={"username": "newbie", "role": "bidder", "password": "temporary-pass-1"})
    c = TestClient(world.app)
    assert c.post("/api/login", json={"username": "newbie", "password": "temporary-pass-1"}).status_code == 200
    me = c.get("/api/me").json()["user"]
    assert me["must_change"] is True and me["role"] == "bidder"
    assert c.get("/api/stats").status_code == 403 and "new password" in c.get("/api/stats").json()["detail"]
    assert c.post("/api/account/password", json={"current": "wrong", "new": GOOD}).status_code == 422
    assert c.post("/api/account/password", json={"current": "temporary-pass-1", "new": "temporary-pass-1"}).status_code == 422  # must differ
    assert c.post("/api/account/password", json={"current": "temporary-pass-1", "new": "short"}).status_code == 422
    assert c.post("/api/account/password", json={"current": "temporary-pass-1", "new": GOOD}).status_code == 200
    assert c.get("/api/stats").status_code == 200 and c.get("/api/me").json()["user"]["must_change"] is False


def test_the_owner_login_is_managed_by_the_server_not_the_page(world):
    assert world.owner.post("/api/account/password", json={"current": PASSWORD, "new": GOOD}).status_code == 422
    assert world.owner.patch("/api/users/0", json={"role": "viewer"}).status_code == 422
    assert world.owner.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200  # and it still signs in


def test_wrong_names_and_passwords_look_the_same_and_count_against_the_limit(world):
    world.person("meera", "bidder")
    c = TestClient(world.app)
    a = c.post("/api/login", json={"username": "meera", "password": "wrong-password-1"})
    b = c.post("/api/login", json={"username": "nobody", "password": "wrong-password-1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    for _ in range(6):
        c.post("/api/login", json={"username": "meera", "password": "wrong-password-1"})
    assert c.post("/api/login", json={"username": "meera", "password": GOOD}).status_code == 429


# ---- what each role can really do ----------------------------------------------------------------------------------------

def test_a_viewer_can_look_but_not_change_anything(world):
    v, _ = world.person("vera", "viewer")
    for path in ("/api/stats", "/api/tenders", "/api/bids", "/api/vault", "/api/config", "/api/runs", "/api/portals"):
        assert v.get(path).status_code == 200, path
    for method, path, body in (("post", "/api/runs", {}), ("post", "/api/tenders/star", {"key": "x", "starred": True}), ("post", "/api/bids", {"key": "x"}),
                               ("put", "/api/views", {"views": []}), ("post", "/api/vault", {"name": "x"}), ("put", "/api/company-facts", {"facts": []}),
                               ("post", "/api/tenders/manual", {"tender_id": "T"}), ("post", "/api/ai/score", {})):
        r = getattr(v, method)(path, json=body)
        assert r.status_code == 403 and "view-only" in r.json()["detail"], path
    assert v.get("/api/vault/1/file").status_code == 403  # company papers are not for view-only accounts
    assert v.get("/api/settings").status_code == 403 and v.put("/api/settings", json={}).status_code == 403


def test_a_bidder_works_but_cannot_change_settings_people_or_backups(world):
    b, _ = world.person("bob", "bidder")
    assert b.put("/api/views", json={"views": []}).status_code == 200
    assert b.put("/api/company-facts", json={"facts": [{"label": "Turnover", "value": "5 crore"}]}).status_code == 200
    for method, path, body in (("get", "/api/settings", None), ("put", "/api/settings", {}), ("post", "/api/portals", {"name": "x"}), ("get", "/api/users", None),
                               ("get", "/api/backup", None), ("get", "/api/audit", None), ("post", "/api/settings/test-email", {}), ("delete", "/api/portals/x", None)):
        kw = {"json": body} if body is not None else {}
        r = getattr(b, method)(path, **kw)
        assert r.status_code == 403 and "administrator" in r.json()["detail"], path


def test_an_admin_account_can_do_admin_things(world):
    a, _ = world.person("alia", "admin")
    assert a.get("/api/settings").status_code == 200 and a.get("/api/users").status_code == 200 and a.get("/api/audit").status_code == 200
    assert a.get("/api/backup/status").status_code == 200


def test_role_changes_and_deactivation_take_effect_on_the_next_request(world):
    b, uid = world.person("bob", "bidder")
    assert b.put("/api/views", json={"views": []}).status_code == 200
    world.owner.patch(f"/api/users/{uid}", json={"role": "viewer"})
    assert b.put("/api/views", json={"views": []}).status_code == 403  # same session, new role
    world.owner.patch(f"/api/users/{uid}", json={"role": "bidder"})
    assert b.put("/api/views", json={"views": []}).status_code == 200
    world.owner.patch(f"/api/users/{uid}", json={"active": False})
    assert b.get("/api/stats").status_code == 401 and b.get("/api/me").json()["authenticated"] is False
    assert TestClient(world.app).post("/api/login", json={"username": "bob", "password": GOOD}).status_code == 401
    world.owner.patch(f"/api/users/{uid}", json={"active": True})
    assert TestClient(world.app).post("/api/login", json={"username": "bob", "password": GOOD}).status_code == 200


def test_resetting_a_password_signs_that_person_out_and_forces_a_new_one(world):
    b, uid = world.person("bob", "bidder")
    assert world.owner.patch(f"/api/users/{uid}", json={"password": "short"}).status_code == 422
    assert world.owner.patch(f"/api/users/{uid}", json={"password": "reset-by-admin-9"}).status_code == 200
    assert b.get("/api/stats").status_code == 401
    c = TestClient(world.app)
    assert c.post("/api/login", json={"username": "bob", "password": GOOD}).status_code == 401
    assert c.post("/api/login", json={"username": "bob", "password": "reset-by-admin-9"}).status_code == 200
    assert c.get("/api/me").json()["user"]["must_change"] is True


def test_changing_your_own_password_signs_out_your_other_devices_only(world):
    laptop, _ = world.person("bob", "bidder")
    phone = TestClient(world.app)
    assert phone.post("/api/login", json={"username": "bob", "password": GOOD}).status_code == 200
    assert laptop.post("/api/account/password", json={"current": GOOD, "new": "a-brand-new-password"}).status_code == 200
    assert laptop.get("/api/stats").status_code == 200 and phone.get("/api/stats").status_code == 401


def test_an_admin_cannot_lock_themselves_out(world):
    a, uid = world.person("alia", "admin")
    assert a.patch(f"/api/users/{uid}", json={"active": False}).status_code == 422
    assert a.patch(f"/api/users/{uid}", json={"role": "viewer"}).status_code == 422
    assert a.patch(f"/api/users/{uid}", json={"name": "Alia B"}).status_code == 200
    assert a.patch("/api/users/999", json={"name": "x"}).status_code == 404


# ---- the activity log ---------------------------------------------------------------------------------------------------

def test_the_activity_log_records_who_did_what(world):
    b, _ = world.person("bob", "bidder", name="Bob")
    b.put("/api/views", json={"views": []})
    b.put("/api/settings", json={})  # refused: not an admin
    b.get("/api/stats")  # ordinary reading is not logged
    world.owner.get("/api/backup")  # downloads of everything are
    entries = world.owner.get("/api/audit").json()["entries"]
    seen = [(e["username"], e["action"]) for e in entries]
    assert ("bob", "Signed in") in seen and ("bob", "Changed saved views") in seen
    assert ("bob", "Refused: Changed settings") in seen
    assert ("admin", "Downloaded a backup") in seen
    assert ("admin", "Created an account") in seen and ("bob", "Changed own password") in seen
    assert not any("stats" in e["action"] for e in entries)
    refused = next(e for e in entries if e["action"].startswith("Refused"))
    assert refused["role"] == "bidder" and refused["client"]


def test_failed_sign_ins_are_logged_without_the_password(world):
    TestClient(world.app).post("/api/login", json={"username": "mallory", "password": "my-secret-guess-1"})
    entries = world.owner.get("/api/audit").json()["entries"]
    failed = [e for e in entries if e["action"] == "Failed sign-in"]
    assert failed and failed[0]["username"] == "mallory"
    assert "my-secret-guess-1" not in str(entries)


def test_the_activity_log_can_be_filtered_and_paged(world):
    b, _ = world.person("bob", "bidder")
    for _ in range(5):
        b.put("/api/views", json={"views": []})
    mine = world.owner.get("/api/audit", params={"user": "bob", "limit": 3}).json()
    assert len(mine["entries"]) == 3 and all(e["username"] == "bob" for e in mine["entries"]) and mine["next"]
    older = world.owner.get("/api/audit", params={"user": "bob", "limit": 50, "before": mine["next"]}).json()
    assert older["entries"] and all(e["id"] < mine["next"] for e in older["entries"])


def test_sessions_that_predate_accounts_still_belong_to_the_owner(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, created REAL NOT NULL, last_seen REAL NOT NULL)")  # the shape released before accounts
    old.execute("INSERT INTO sessions VALUES ('old', 1000, 1000)")
    old.commit()
    old.close()
    store = SeenStore(str(path))  # opening it upgrades the table
    assert store.session_touch("old", 1010, 100) == 0


def test_bidders_keep_the_company_profile_without_access_to_settings(world):
    b, _ = world.person("bob", "bidder")
    assert b.put("/api/company-profile", json={"company_profile": "Class A civil contractor, roads and bridges."}).status_code == 200
    assert "roads and bridges" in b.get("/api/company-profile").json()["company_profile"]
    assert b.get("/api/settings").status_code == 403  # the rest of Settings stays with administrators
    v, _ = world.person("vera", "viewer")
    assert "roads and bridges" in v.get("/api/company-profile").json()["company_profile"]
    assert v.put("/api/company-profile", json={"company_profile": "x"}).status_code == 403
