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
    ("GET", "/api/vault/4/file", "bidder"), ("GET", "/api/vault", "viewer"), ("GET", "/api/plan", "bidder"), ("GET", "/api/money", "bidder"),
    ("GET", "/api/market-results", "viewer"), ("POST", "/api/market-results/import", "bidder"), ("PUT", "/api/settings/logo", "admin"), ("GET", "/api/branding/logo", "viewer"),
    ("GET", "/api/win-model", "viewer"), ("GET", "/api/bids/3/rivals", "viewer"), ("POST", "/api/bids/3/clauses", "bidder"),
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
        if not path.startswith("/api/") or path in ("/api/login", "/api/logout", "/api/health", "/api/me", "/api/branding") or path.startswith("/api/account/"):
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


# ---- two-step sign-in -----------------------------------------------------------------------------------------------

def test_totp_matches_the_published_rfc_6238_vectors():
    secret = accounts.base64.b32encode(b"12345678901234567890").decode()  # the RFC's SHA-1 test key
    for when, expected in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037")):
        assert accounts.totp_code(secret, when // 30, digits=8) == expected, when
    assert accounts.totp_code(secret, 59 // 30) == "287082"  # the same codes as the 6-digit app shows


def test_a_code_is_accepted_once_within_the_clock_tolerance_and_never_replayed():
    secret = accounts.new_totp_secret()
    now = 1_700_000_000.0
    step = int(now // 30)
    good = accounts.totp_code(secret, step)
    assert accounts.totp_match(secret, good, now) == step
    assert accounts.totp_match(secret, accounts.totp_code(secret, step - 1), now) == step - 1  # a phone clock slightly behind
    assert accounts.totp_match(secret, accounts.totp_code(secret, step + 1), now) == step + 1
    assert accounts.totp_match(secret, accounts.totp_code(secret, step + 3), now) is None       # too far off
    assert accounts.totp_match(secret, good, now, after_step=step) is None                      # already used
    assert accounts.totp_match(secret, " " + good[:3] + " " + good[3:], now) == step            # spaces typed by people are fine
    for bad in ("", "12345", "abcdef", "1234567"):
        assert accounts.totp_match(secret, bad, now) is None


def two_step_for(world, username, role="bidder"):
    """Signs a person in, turns on two-step sign-in for them, and returns (client, secret)."""
    c, uid = world.person(username, role)
    secret = c.post("/api/account/2fa/start").json()["secret"]
    code = accounts.totp_code(secret, int(web.time.time() // 30))
    assert c.post("/api/account/2fa/enable", json={"code": code}).status_code == 200
    return c, uid, secret


def test_two_step_sign_in_end_to_end(world):
    c, uid, secret = two_step_for(world, "tina")
    me = c.get("/api/me").json()["user"]
    assert me["two_step"] is True
    fresh = TestClient(world.app)
    # right password, no code: asked for the code, and nothing is signed in
    r = fresh.post("/api/login", json={"username": "tina", "password": GOOD})
    assert r.status_code == 401 and r.json()["need_code"] is True and fresh.get("/api/me").json()["authenticated"] is False
    # wrong code
    assert fresh.post("/api/login", json={"username": "tina", "password": GOOD, "code": "000000"}).status_code == 401
    # right code works once
    step = int(web.time.time() // 30)
    code = accounts.totp_code(secret, step + 1)  # next step: the setup used the current one
    assert fresh.post("/api/login", json={"username": "tina", "password": GOOD, "code": code}).status_code == 200
    again = TestClient(world.app)
    assert again.post("/api/login", json={"username": "tina", "password": GOOD, "code": code}).status_code == 401  # replay refused
    # a wrong password never gets as far as asking for a code
    assert fresh.post("/api/login", json={"username": "tina", "password": "wrong-password-1"}).json().get("need_code") is None


def test_setting_up_two_step_needs_a_right_code_and_turning_it_off_needs_the_password(world):
    c, uid = world.person("tina", "bidder")
    start = c.post("/api/account/2fa/start").json()
    assert start["uri"].startswith("otpauth://totp/TendAI:tina?secret=" + start["secret"])
    assert c.post("/api/account/2fa/enable", json={"code": "123456"}).status_code == 422
    assert c.get("/api/me").json()["user"]["two_step"] is False  # not on until a code was proved
    code = accounts.totp_code(start["secret"], int(web.time.time() // 30))
    assert c.post("/api/account/2fa/enable", json={"code": code}).status_code == 200
    assert c.post("/api/account/2fa/disable", json={"password": "wrong-password-1"}).status_code == 422
    assert c.post("/api/account/2fa/disable", json={"password": GOOD}).status_code == 200
    assert c.get("/api/me").json()["user"]["two_step"] is False
    assert TestClient(world.app).post("/api/login", json={"username": "tina", "password": GOOD}).status_code == 200


def test_an_admin_can_reset_a_lost_phone(world):
    c, uid, secret = two_step_for(world, "tina")
    assert world.owner.get("/api/users").json()["users"][1]["two_step"] is True
    assert world.owner.patch(f"/api/users/{uid}", json={"reset_2fa": True}).status_code == 200
    assert world.owner.get("/api/users").json()["users"][1]["two_step"] is False
    assert TestClient(world.app).post("/api/login", json={"username": "tina", "password": GOOD}).status_code == 200


def test_ten_wrong_codes_pause_that_accounts_codes(world):
    c, uid, secret = two_step_for(world, "tina")
    for i in range(10):
        TestClient(world.app).post("/api/login", json={"username": "tina", "password": GOOD, "code": f"{i:06d}"}, headers={"X-Forwarded-For": f"9.9.9.{i}"})
    good = accounts.totp_code(secret, int(web.time.time() // 30) + 1)
    assert TestClient(world.app).post("/api/login", json={"username": "tina", "password": GOOD, "code": good}, headers={"X-Forwarded-For": "9.9.9.99"}).status_code == 401


def test_the_owner_can_require_a_code_through_a_server_variable(monkeypatch):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    secret = accounts.new_totp_secret()
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin", owner_totp=secret), settings(), SeenStore(":memory:"))
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["need_code"] is True
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD, "code": "000000"}).status_code == 401
    code = accounts.totp_code(secret, int(web.time.time() // 30))
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD, "code": code}).status_code == 200
    assert TestClient(app).post("/api/login", json={"username": "admin", "password": PASSWORD, "code": code}).status_code == 401  # replay


def test_signing_out_other_devices_keeps_this_one(world):
    laptop, _ = world.person("bob", "bidder")
    phone = TestClient(world.app)
    assert phone.post("/api/login", json={"username": "bob", "password": GOOD}).status_code == 200
    assert laptop.post("/api/account/sessions/end-others").status_code == 200
    assert laptop.get("/api/stats").status_code == 200 and phone.get("/api/stats").status_code == 401


def test_failed_sign_ins_survive_a_restart(tmp_path):
    path = str(tmp_path / "s.db")
    a = web.LoginLimiter(store=SeenStore(path))
    for _ in range(8):
        a.failed("203.0.113.7")
    assert a.blocked("203.0.113.7")
    b = web.LoginLimiter(store=SeenStore(path))  # the service restarted
    assert b.blocked("203.0.113.7") and not b.blocked("198.51.100.1")
    b.succeeded("203.0.113.7")
    assert not web.LoginLimiter(store=SeenStore(path)).blocked("203.0.113.7")
