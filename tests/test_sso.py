"""Single sign-on (OpenID Connect): the whole flow against a fake provider, and every way the identity token can be wrong."""
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient

from tender_digest import sso, web
from tender_digest.runner import RunManager
from tender_digest.sso import SSOConfig, SSOError, b64u
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, settings

ISSUER = "https://idp.example.com"
CLIENT = "tendai-client"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def jwk_rsa(key, kid="k1"):
    n = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64u(n.n.to_bytes(256, "big")), "e": b64u(n.e.to_bytes(3, "big"))}


def jwk_ec(key, kid="e1"):
    n = key.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "kid": kid, "use": "sig", "x": b64u(n.x.to_bytes(32, "big")), "y": b64u(n.y.to_bytes(32, "big"))}


def mint(claims, key=KEY, alg="RS256", kid="k1", header_extra=None):
    head = {"alg": alg, "typ": "JWT", "kid": kid, **(header_extra or {})}
    signing = f"{b64u(json.dumps(head).encode())}.{b64u(json.dumps(claims).encode())}"
    if alg == "RS256":
        sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    elif alg == "ES256":
        r, s = decode_dss_signature(key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:
        sig = b"x"
    return f"{signing}.{b64u(sig)}"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class FakeIdP:
    """Stands in for the provider's web address: discovery, keys and the token exchange."""
    def __init__(self):
        self.keys = [jwk_rsa(KEY)]
        self.claims = {}
        self.token_status = 200
        self.posts = []
        self.sign_with = KEY

    def get(self, url, **kw):
        if url.endswith("/.well-known/openid-configuration"):
            return Resp(200, {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/keys"})
        if url == ISSUER + "/keys":
            return Resp(200, {"keys": self.keys})
        return Resp(404)

    def post(self, url, data=None, **kw):
        self.posts.append(data)
        if self.token_status != 200:
            return Resp(self.token_status)
        return Resp(200, {"id_token": mint(self.claims, self.sign_with), "access_token": "x"})


def good_claims(nonce_value, **over):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT, "sub": "u-100", "email": "Meera@Example.com", "email_verified": True, "name": "Meera Rao", "nonce": nonce_value, "iat": now, "exp": now + 600}
    claims.update(over)
    return claims


@pytest.fixture
def idp(monkeypatch):
    for k, v in {"SSO_ISSUER": ISSUER, "SSO_CLIENT_ID": CLIENT, "SSO_CLIENT_SECRET": "shh", "SSO_ALLOWED_DOMAINS": "example.com"}.items():
        monkeypatch.setenv(k, v)
    return FakeIdP()


@pytest.fixture
def app(idp, monkeypatch):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    store = SeenStore(":memory:")
    env = settings()
    manager = RunManager(env, store, lambda p, s: None, FakeSender(), background=False)
    a = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, manager, sso_session=idp)
    return type("A", (), {"app": a, "store": store})


def begin(app, client):
    r = client.get("/api/sso/login", follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    return r, {k: v[0] for k, v in q.items()}


def finish(client, q, code="abc", state=None):
    return client.get("/api/sso/callback", params={"code": code, "state": state or q["state"]}, follow_redirects=False)


def owner(app):
    c = TestClient(app.app)
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    return c


# ---- the flow -------------------------------------------------------------------------------------------------------

def test_signing_in_creates_an_account_with_the_default_role_and_a_session(app, idp):
    assert TestClient(app.app).get("/api/sso/status").json() == {"enabled": True, "button_text": "Sign in with SSO"}
    c = TestClient(app.app)
    r, q = begin(app, c)
    assert r.headers["location"].startswith(ISSUER + "/authorize?") and q["client_id"] == CLIENT and q["code_challenge_method"] == "S256" and q["scope"] == "openid email profile"
    assert q["redirect_uri"] == "http://testserver/api/sso/callback" and len(q["state"]) > 20 and len(q["nonce"]) > 20
    idp.claims = good_claims(q["nonce"])
    back = finish(c, q)
    assert back.status_code == 303 and back.headers["location"] == "/"
    me = c.get("/api/me").json()["user"]
    assert me["username"] == "meera" and me["email"] == "meera@example.com" and me["role"] == "viewer" and me["name"] == "Meera Rao" and not me["must_change"]
    sent = idp.posts[0]
    assert sent["code"] == "abc" and sent["client_secret"] == "shh" and sent["grant_type"] == "authorization_code" and sent["redirect_uri"] == q["redirect_uri"]
    import hashlib
    assert b64u(hashlib.sha256(sent["code_verifier"].encode()).digest()) == q["code_challenge"]       # PKCE: the verifier matches the challenge
    log = owner(app).get("/api/audit").json()["entries"]
    assert any(e["action"] == "Signed in with SSO" and e["username"] == "meera" for e in log)


def test_the_same_person_signing_in_again_is_the_same_account_and_an_admin_can_change_the_role(app, idp):
    for _ in range(2):
        c = TestClient(app.app)
        _, q = begin(app, c)
        idp.claims = good_claims(q["nonce"])
        finish(c, q)
    users = [u for u in owner(app).get("/api/users").json()["users"] if not u["owner"]]
    assert len(users) == 1 and users[0]["sso"] is True
    o = owner(app)
    o.patch(f"/api/users/{users[0]['id']}", json={"role": "bidder"})
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    finish(c, q)
    assert c.get("/api/me").json()["user"]["role"] == "bidder"                    # the role set here is kept, not reset by the provider


def test_an_existing_account_with_the_same_email_is_linked_not_duplicated(app, idp):
    o = owner(app)
    made = o.post("/api/users", json={"username": "meera.rao", "role": "bidder", "password": "temporary-pass-1", "email": "meera@example.com"}).json()
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    finish(c, q)
    me = c.get("/api/me").json()["user"]
    assert me["username"] == "meera.rao" and me["role"] == "bidder" and me["id"] == made["id"]
    assert len([u for u in o.get("/api/users").json()["users"] if not u["owner"]]) == 1


def test_a_switched_off_account_stays_out(app, idp):
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    finish(c, q)
    o = owner(app)
    uid = next(u["id"] for u in o.get("/api/users").json()["users"] if u["username"] == "meera")
    o.patch(f"/api/users/{uid}", json={"active": False})
    c2 = TestClient(app.app)
    _, q2 = begin(app, c2)
    idp.claims = good_claims(q2["nonce"])
    r = finish(c2, q2)
    assert "sso_error" in r.headers["location"] and "switched" in r.headers["location"] and c2.get("/api/me").json()["authenticated"] is False


def test_usernames_do_not_clash(app, idp):
    for sub, mail in (("a", "sam@example.com"), ("b", "sam@example.com"), ("c", "x@example.com")):
        c = TestClient(app.app)
        _, q = begin(app, c)
        idp.claims = good_claims(q["nonce"], sub=sub, email=mail)
        finish(c, q)
    names = sorted(u["username"] for u in owner(app).get("/api/users").json()["users"] if not u["owner"])
    assert len(names) == 3 and len(set(names)) == 3 and all(len(n) >= 3 for n in names)


# ---- everything that can be wrong -------------------------------------------------------------------------------------

def refused(app, idp, **claim_over):
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"], **claim_over)
    r = finish(c, q)
    assert c.get("/api/me").json()["authenticated"] is False, "nobody may be signed in"
    return r.headers["location"]


@pytest.mark.parametrize("over,why", [
    ({"iss": "https://evil.example.com"}, "different provider"),
    ({"aud": "someone-else"}, "not issued for this app"),
    ({"exp": int(time.time()) - 3600}, "expired"),
    ({"nonce": "not-the-one"}, "did not match"),
    ({"sub": ""}, "no person"),
    ({"email": "meera@evil.com"}, "may not sign in"),
    ({"email": "nobody"}, "email address"),
    ({"email_verified": False}, "not verified"),
    ({"iat": int(time.time()) + 3600}, "future"),
    ({"aud": [CLIENT, "other"], "azp": "other"}, "not issued for this app"),
])
def test_bad_claims_are_refused(app, idp, over, why):
    assert why in refused(app, idp, **over).replace("+", " ")


def test_a_token_signed_by_the_wrong_key_is_refused(app, idp):
    idp.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert "signature" in refused(app, idp).lower()


def test_algorithm_tricks_are_refused():
    cfg = SSOConfig(ISSUER, CLIENT, "s", ["example.com"])
    keys = [jwk_rsa(KEY)]
    for alg in ("none", "HS256", "RS512"):
        with pytest.raises(SSOError, match="refuses"):
            sso.verify_signature(mint({"sub": "x"}, alg=alg), keys)
    # a shared-secret token whose "secret" is the public key text must not pass either
    forged = f"{b64u(json.dumps({'alg': 'HS256', 'kid': 'k1'}).encode())}.{b64u(b'{}')}.{b64u(b'sig')}"
    with pytest.raises(SSOError):
        sso.verify_signature(forged, keys)
    with pytest.raises(SSOError, match="not a valid token"):
        sso.verify_signature("garbage", keys)
    assert cfg.issuer


def test_es256_tokens_work_and_unknown_keys_are_refused():
    claims = {"sub": "x"}
    _, got = sso.verify_signature(mint(claims, EC_KEY, "ES256", "e1"), [jwk_ec(EC_KEY)])
    assert got == claims
    with pytest.raises(SSOError, match="does not publish"):
        sso.verify_signature(mint(claims, EC_KEY, "ES256", "other"), [jwk_ec(EC_KEY)])
    with pytest.raises(SSOError, match="signature"):
        sso.verify_signature(mint(claims, EC_KEY, "ES256", "e1"), [jwk_ec(ec.generate_private_key(ec.SECP256R1()), "e1")])


def test_rotated_provider_keys_are_picked_up(app, idp):
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    finish(c, q)                                                                          # the provider's keys are now cached
    new = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp.keys, idp.sign_with = [jwk_rsa(new, "k2")], new                                   # the provider rotates its keys
    original_post = idp.post

    def post(url, data=None, **kw):
        idp.posts.append(data)
        return Resp(200, {"id_token": mint(idp.claims, new, kid="k2")})

    idp.post = post
    c2 = TestClient(app.app)
    _, q2 = begin(app, c2)
    idp.claims = good_claims(q2["nonce"])
    finish(c2, q2)
    assert c2.get("/api/me").json()["authenticated"] is True and original_post


def test_the_callback_needs_the_matching_state_and_cookie(app, idp):
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    assert "not valid" in finish(c, q, state="forged").headers["location"].replace("+", " ")
    assert TestClient(app.app).get("/api/sso/callback", params={"code": "x", "state": q["state"]}, follow_redirects=False).headers["location"].startswith("/?sso_error=")  # no cookie
    assert c.get("/api/me").json()["authenticated"] is False
    # the same cookie cannot be replayed once a flow has finished
    _, q2 = begin(app, c)
    idp.claims = good_claims(q2["nonce"])
    assert finish(c, q2).headers["location"] == "/"
    assert finish(c, q2).headers["location"].startswith("/?sso_error=")


def test_provider_refusals_and_errors_are_explained(app, idp):
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.token_status = 400
    assert "refused" in finish(c, q).headers["location"]
    _, q = begin(app, c)
    r = c.get("/api/sso/callback", params={"error": "access_denied", "state": q["state"]}, follow_redirects=False)
    assert "denied" in r.headers["location"]


def test_the_owner_password_login_still_works_and_sso_accounts_have_no_password(app, idp):
    assert owner(app).get("/api/me").json()["user"]["owner"] is True
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    finish(c, q)
    for pw in ("!", "", "temporary-pass-1"):
        assert TestClient(app.app).post("/api/login", json={"username": "meera", "password": pw}).status_code == 401


def test_admin_sees_how_it_is_set_up_others_do_not(app, idp):
    info = owner(app).get("/api/sso/info").json()
    assert info == {"enabled": True, "problem": "", "issuer": ISSUER, "client_id": CLIENT, "redirect_uri": "http://testserver/api/sso/callback", "allowed_domains": ["example.com"], "default_role": "viewer"}
    assert "shh" not in json.dumps(info)
    assert TestClient(app.app).get("/api/sso/info").status_code == 401
    c = TestClient(app.app)
    _, q = begin(app, c)
    idp.claims = good_claims(q["nonce"])
    finish(c, q)
    assert c.get("/api/sso/info").status_code == 403


# ---- configuration --------------------------------------------------------------------------------------------------

def test_sso_is_off_by_default_and_misconfiguration_is_explained(monkeypatch):
    for k in ("SSO_ISSUER", "SSO_CLIENT_ID", "SSO_CLIENT_SECRET", "SSO_ALLOWED_DOMAINS"):
        monkeypatch.delenv(k, raising=False)
    assert sso.config_from_env() is None
    store = SeenStore(":memory:")
    env = settings()
    a = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, lambda p, s: None, FakeSender(), background=False))
    c = TestClient(a)
    assert c.get("/api/sso/status").json() == {"enabled": False, "button_text": ""} and c.get("/api/sso/login", follow_redirects=False).status_code == 404
    with pytest.raises(SSOError, match="all set"):
        sso.config_from_env({"SSO_ISSUER": ISSUER})
    with pytest.raises(SSOError, match="https"):
        sso.config_from_env({"SSO_ISSUER": "http://idp.example.com", "SSO_CLIENT_ID": "a", "SSO_CLIENT_SECRET": "b"})
    with pytest.raises(SSOError, match="viewer or bidder"):
        sso.config_from_env({"SSO_ISSUER": ISSUER, "SSO_CLIENT_ID": "a", "SSO_CLIENT_SECRET": "b", "SSO_DEFAULT_ROLE": "admin"})   # never admin automatically
    cfg = sso.config_from_env({"SSO_ISSUER": ISSUER + "/", "SSO_CLIENT_ID": "a", "SSO_CLIENT_SECRET": "b", "SSO_ALLOWED_DOMAINS": "Example.com; @other.org", "SSO_BUTTON_TEXT": "Sign in with Google"})
    assert cfg.issuer == ISSUER and cfg.allowed_domains == ["example.com", "other.org"] and cfg.button_text == "Sign in with Google"


def test_a_bad_configuration_turns_sso_off_but_keeps_the_app_running(monkeypatch):
    monkeypatch.setenv("SSO_ISSUER", ISSUER)  # the other two are missing
    store = SeenStore(":memory:")
    env = settings()
    a = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, RunManager(env, store, lambda p, s: None, FakeSender(), background=False))
    c = TestClient(a)
    assert c.get("/api/sso/status").json()["enabled"] is False
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    assert "all set" in c.get("/api/sso/info").json()["problem"]


def test_nobody_is_let_in_without_an_allowed_domain(app, idp, monkeypatch):
    cfg = SSOConfig(ISSUER, CLIENT, "s", [])
    with pytest.raises(SSOError, match="no email domain"):
        sso.identity({"sub": "x", "email": "a@example.com"}, cfg)
