"""Single sign-on with OpenID Connect (Google Workspace, Microsoft Entra ID, Okta, Keycloak, Auth0: anything that publishes /.well-known/openid-configuration).

The authorization-code flow with PKCE, run on the server. The ID token's signature is checked against the provider's published keys (RS256 or
ES256 only: `none` and shared-secret algorithms are refused), and so are its issuer, audience, expiry and the one-time `nonce`. Which people may
come in is set by SSO_ALLOWED_DOMAINS; the provider's own sign-in (and its two-step check) replaces this app's password and code for them.

Set on the server:
  SSO_ISSUER           e.g. https://accounts.google.com  or  https://login.microsoftonline.com/<tenant-id>/v2.0
  SSO_CLIENT_ID, SSO_CLIENT_SECRET
  SSO_ALLOWED_DOMAINS  e.g. example.com,example.org   (required: without it nobody is let in)
  SSO_DEFAULT_ROLE     viewer (default) or bidder; administrators are made by an administrator in Settings > People
  SSO_BUTTON_TEXT      default "Sign in with SSO"
Register this redirect address with the provider:  <APP_URL>/api/sso/callback
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


class SSOError(Exception):
    """`code` is a short reason for the log; `message` is what the person sees."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class SSOConfig:
    issuer: str
    client_id: str
    client_secret: str
    allowed_domains: list[str]
    default_role: str = "viewer"
    button_text: str = "Sign in with SSO"
    allow_http: bool = False  # only for a provider on this machine, in tests


def config_from_env(environ: dict[str, str] | None = None) -> SSOConfig | None:
    """None when SSO is not set up. Raises SSOError for a half-set-up or unsafe configuration."""
    env = os.environ if environ is None else environ
    issuer, cid, secret = (env.get(k, "").strip() for k in ("SSO_ISSUER", "SSO_CLIENT_ID", "SSO_CLIENT_SECRET"))
    if not (issuer or cid or secret):
        return None
    if not (issuer and cid and secret):
        raise SSOError("config", "SSO needs SSO_ISSUER, SSO_CLIENT_ID and SSO_CLIENT_SECRET all set")
    allow_http = env.get("SSO_ALLOW_HTTP", "").lower() in ("1", "true", "yes")
    if urlparse(issuer).scheme != "https" and not allow_http:
        raise SSOError("config", "SSO_ISSUER must start with https://")
    domains = [d.strip().lower().lstrip("@") for d in env.get("SSO_ALLOWED_DOMAINS", "").replace(";", ",").split(",") if d.strip()]
    role = env.get("SSO_DEFAULT_ROLE", "viewer").strip().lower() or "viewer"
    if role not in ("viewer", "bidder"):
        raise SSOError("config", "SSO_DEFAULT_ROLE must be viewer or bidder")
    return SSOConfig(issuer.rstrip("/"), cid, secret, domains, role, env.get("SSO_BUTTON_TEXT", "").strip()[:40] or "Sign in with SSO", allow_http)


# ---- tokens ----------------------------------------------------------------------------------------------------------

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pkce_pair() -> tuple[str, str]:
    verifier = b64u(secrets.token_bytes(48))
    return verifier, b64u(hashlib.sha256(verifier.encode()).digest())


def _public_key(jwk: dict[str, Any]):
    if jwk.get("kty") == "RSA":
        return rsa.RSAPublicNumbers(int.from_bytes(b64u_decode(jwk["e"]), "big"), int.from_bytes(b64u_decode(jwk["n"]), "big")).public_key()
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        return ec.EllipticCurvePublicNumbers(int.from_bytes(b64u_decode(jwk["x"]), "big"), int.from_bytes(b64u_decode(jwk["y"]), "big"), ec.SECP256R1()).public_key()
    raise SSOError("key", "The provider's signing key is of a kind this app does not support")


def verify_signature(token: str, keys: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(header, claims) when the token is signed by one of `keys`; otherwise SSOError. Only RS256 and ES256 are accepted."""
    try:
        head_b, body_b, sig_b = token.split(".")
        header = json.loads(b64u_decode(head_b))
        claims = json.loads(b64u_decode(body_b))
        sig = b64u_decode(sig_b)
    except (ValueError, TypeError):
        raise SSOError("token", "The sign-in answer was not a valid token") from None
    alg = header.get("alg")
    if alg not in ("RS256", "ES256"):
        raise SSOError("alg", "The sign-in token uses a signature method this app refuses")
    kid = header.get("kid")
    candidates = [k for k in keys if (not kid or k.get("kid") == kid) and k.get("use", "sig") == "sig" and k.get("kty") == ("RSA" if alg == "RS256" else "EC")]
    if not candidates:
        raise SSOError("kid", "The sign-in token was signed with a key the provider does not publish")
    signed = f"{head_b}.{body_b}".encode()
    for jwk in candidates:
        try:
            pub = _public_key(jwk)
            if alg == "RS256":
                pub.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
            else:
                if len(sig) != 64:
                    continue
                pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")), signed, ec.ECDSA(hashes.SHA256()))
            return header, claims
        except InvalidSignature:
            continue
    raise SSOError("signature", "The sign-in token's signature is not valid")


def check_claims(claims: dict[str, Any], cfg: SSOConfig, nonce: str, now: float) -> None:
    if str(claims.get("iss", "")).rstrip("/") != cfg.issuer:
        raise SSOError("iss", "The sign-in token came from a different provider than the one set up")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if cfg.client_id not in auds or (len(auds) > 1 and claims.get("azp") != cfg.client_id):
        raise SSOError("aud", "The sign-in token was not issued for this app")
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, (int, float)) or exp < now - 60:
        raise SSOError("exp", "The sign-in has expired. Try again")
    if isinstance(iat, (int, float)) and iat > now + 300:
        raise SSOError("iat", "The sign-in token is dated in the future (check the server's clock)")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SSOError("nonce", "The sign-in did not match the one that was started. Try again")
    if not claims.get("sub"):
        raise SSOError("sub", "The sign-in token names no person")


def identity(claims: dict[str, Any], cfg: SSOConfig) -> dict[str, str]:
    """{sub, email, name} for a person the provider vouches for and this app allows in. Raises SSOError otherwise."""
    email = str(claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    if "@" not in email or len(email) > 120:
        raise SSOError("email", "The provider did not give an email address for you, so you cannot be recognised")
    if claims.get("email_verified") in (False, "false"):
        raise SSOError("unverified", "The provider says your email address is not verified")
    domain = email.rsplit("@", 1)[1]
    if not cfg.allowed_domains:
        raise SSOError("domains", "SSO is set up but no email domain is allowed yet (SSO_ALLOWED_DOMAINS). Ask your administrator")
    if domain not in cfg.allowed_domains:
        raise SSOError("domain", f"Accounts at {domain} may not sign in here")
    return {"sub": f"{cfg.issuer}|{claims['sub']}", "email": email, "name": str(claims.get("name") or "").strip()[:80]}


# ---- the provider ----------------------------------------------------------------------------------------------------

@dataclass
class Provider:
    cfg: SSOConfig
    session: Any = field(default_factory=requests.Session)
    _doc: tuple[float, dict[str, Any]] | None = None
    _keys: tuple[float, list[dict[str, Any]]] | None = None

    def _get_json(self, url: str) -> dict[str, Any]:
        if urlparse(url).scheme != "https" and not self.cfg.allow_http:
            raise SSOError("https", "The provider's address is not https")
        try:
            r = self.session.get(url, timeout=10, allow_redirects=False)
            if r.status_code != 200:
                raise SSOError("provider", f"The sign-in provider answered {r.status_code}")
            return r.json()
        except requests.RequestException:
            raise SSOError("provider", "The sign-in provider could not be reached. Try again in a minute") from None
        except ValueError:
            raise SSOError("provider", "The sign-in provider sent something unreadable") from None

    def document(self) -> dict[str, Any]:
        if self._doc and time.time() - self._doc[0] < 3600:
            return self._doc[1]
        doc = self._get_json(self.cfg.issuer + "/.well-known/openid-configuration")
        if str(doc.get("issuer", "")).rstrip("/") != self.cfg.issuer:
            raise SSOError("issuer", "The provider's address does not match its own name. Check SSO_ISSUER")
        for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not doc.get(k):
                raise SSOError("provider", f"The provider's configuration has no {k}")
        self._doc = (time.time(), doc)
        return doc

    def keys(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._keys and not refresh and time.time() - self._keys[0] < 3600:
            return self._keys[1]
        keys = self._get_json(self.document()["jwks_uri"]).get("keys", [])
        self._keys = (time.time(), keys)
        return keys

    def start(self, redirect_uri: str) -> tuple[str, dict[str, str]]:
        """(the address to send the browser to, what to remember until it comes back)."""
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        q = urlencode({"response_type": "code", "client_id": self.cfg.client_id, "redirect_uri": redirect_uri, "scope": "openid email profile", "state": state, "nonce": nonce,
                       "code_challenge": challenge, "code_challenge_method": "S256"})
        return self.document()["authorization_endpoint"] + ("&" if "?" in self.document()["authorization_endpoint"] else "?") + q, {"state": state, "nonce": nonce, "verifier": verifier}

    def finish(self, code: str, redirect_uri: str, remembered: dict[str, str], now: float | None = None) -> dict[str, str]:
        """Trades the code for a token, checks it, and returns {sub, email, name}."""
        doc = self.document()
        try:
            r = self.session.post(doc["token_endpoint"], data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": self.cfg.client_id,
                                                             "client_secret": self.cfg.client_secret, "code_verifier": remembered["verifier"]},
                                  headers={"Accept": "application/json"}, timeout=10, allow_redirects=False)
        except requests.RequestException:
            raise SSOError("provider", "The sign-in provider could not be reached. Try again in a minute") from None
        if r.status_code != 200:
            raise SSOError("exchange", "The provider refused the sign-in. Try again")
        try:
            id_token = r.json()["id_token"]
        except (ValueError, KeyError, TypeError):
            raise SSOError("exchange", "The provider did not return an identity token") from None
        try:
            _, claims = verify_signature(id_token, self.keys())
        except SSOError as e:
            if e.code not in ("kid", "signature"):
                raise
            _, claims = verify_signature(id_token, self.keys(refresh=True))  # the provider rotated its keys
        check_claims(claims, self.cfg, remembered["nonce"], now if now is not None else time.time())
        return identity(claims, self.cfg)
