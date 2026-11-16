"""Off-site backups: the request signing (checked against Amazon's published examples), and a full round trip against a local fake bucket."""
import gzip
import hashlib
import re
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi.testclient import TestClient

from tender_digest import offsite, web
from tender_digest.backup import Backups
from tender_digest.offsite import Bucket, OffsiteConfig, OffsiteError
from tests.test_backup import real, rows  # noqa: F401
from tests.test_web import PASSWORD, settings

AWS_KEY, AWS_SECRET = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
WHEN = datetime(2013, 5, 24, tzinfo=timezone.utc)


def aws_sign(method, path, query, headers, payload_hash):
    return offsite.sign(method, "examplebucket.s3.amazonaws.com", path, query, headers, payload_hash, access_key=AWS_KEY, secret_key=AWS_SECRET, region="us-east-1", when=WHEN)


def signature_of(h):
    return re.search(r"Signature=([0-9a-f]+)", h["Authorization"]).group(1)


def test_signing_matches_amazons_published_examples():
    """The three worked examples in Amazon's Signature V4 documentation for S3: GET with a Range header, PUT with a '$' in the name, and a listing."""
    get = aws_sign("GET", "/test.txt", {}, {"Range": "bytes=0-9"}, offsite.EMPTY_SHA256)
    assert signature_of(get) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in get["Authorization"]
    body = b"Welcome to Amazon S3."
    put = aws_sign("PUT", "/test$file.text", {}, {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"}, hashlib.sha256(body).hexdigest())
    assert signature_of(put) == "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"
    listing = aws_sign("GET", "/", {"max-keys": "2", "prefix": "J"}, {}, offsite.EMPTY_SHA256)
    assert signature_of(listing) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


# ---- configuration --------------------------------------------------------------------------------------------------

def test_configuration_is_off_complete_or_refused():
    assert offsite.config_from_env({}) is None
    with pytest.raises(OffsiteError, match="all set"):
        offsite.config_from_env({"BACKUP_S3_BUCKET": "b"})
    ok = offsite.config_from_env({"BACKUP_S3_BUCKET": "b", "BACKUP_S3_ACCESS_KEY": "a", "BACKUP_S3_SECRET_KEY": "s", "BACKUP_S3_REGION": "ap-south-1", "BACKUP_S3_PREFIX": "/mine"})
    assert ok.base == "https://s3.ap-south-1.amazonaws.com" and ok.prefix == "mine/" and ok.keep_daily == 14 and ok.keep_weekly == 8
    r2 = offsite.config_from_env({"BACKUP_S3_BUCKET": "b", "BACKUP_S3_ACCESS_KEY": "a", "BACKUP_S3_SECRET_KEY": "s", "BACKUP_S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com/", "BACKUP_S3_KEEP_DAILY": "3"})
    assert r2.base == "https://acct.r2.cloudflarestorage.com" and r2.region == "auto" and r2.keep_daily == 3
    with pytest.raises(OffsiteError, match="https"):
        offsite.config_from_env({"BACKUP_S3_BUCKET": "b", "BACKUP_S3_ACCESS_KEY": "a", "BACKUP_S3_SECRET_KEY": "s", "BACKUP_S3_ENDPOINT": "http://example.com"})


# ---- a fake bucket on localhost -------------------------------------------------------------------------------------------

class FakeBucket:
    """Keeps objects in memory and checks what real S3 checks: the signature, and that the body matches its declared hash."""

    def __init__(self, bucket="bk", fail_with=None):
        self.objects: dict[str, bytes] = {}
        self.bucket, self.fail_with, self.calls = bucket, fail_with, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _auth_ok(self, body: bytes) -> bool:
                m = re.match(r"AWS4-HMAC-SHA256 Credential=(\w+)/(\d{8})/([\w-]+)/s3/aws4_request,SignedHeaders=([\w;-]+),Signature=([0-9a-f]{64})", self.headers.get("Authorization", ""))
                if not m or m.group(1) != "ACCESS":
                    return False
                if self.headers.get("x-amz-content-sha256") != hashlib.sha256(body).hexdigest():
                    return False  # the payload that arrived is not the payload that was signed
                u = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                extra = {h: self.headers[h] for h in m.group(4).split(";") if h not in ("host", "x-amz-content-sha256", "x-amz-date")}
                when = datetime.strptime(self.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                want = offsite.sign(self.command, self.headers["Host"], unquote(u.path), query, extra, hashlib.sha256(body).hexdigest(), access_key="ACCESS", secret_key="SECRET",
                                    region=m.group(3), when=when)
                return want["Authorization"].endswith("Signature=" + m.group(5))

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                u = urlparse(self.path)
                fake.calls.append((self.command, unquote(u.path)))
                status, out = 200, b""
                if fake.fail_with:
                    status, out = fake.fail_with, b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
                elif not self._auth_ok(body):
                    status, out = 403, b"<Error><Message>The request signature we calculated does not match</Message></Error>"
                else:
                    key = unquote(u.path)[len(f"/{fake.bucket}/"):]
                    if self.command == "PUT":
                        fake.objects[key] = body
                    elif self.command == "DELETE":
                        fake.objects.pop(key, None)
                        status = 204
                    elif self.command == "GET":
                        prefix = parse_qs(u.query).get("prefix", [""])[0]
                        names = sorted(k for k in fake.objects if k.startswith(prefix))
                        out = ("<?xml version='1.0'?><ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'><IsTruncated>false</IsTruncated>"
                               + "".join(f"<Contents><Key>{k}</Key></Contents>" for k in names) + "</ListBucketResult>").encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_PUT = do_DELETE = _go

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def config(self, **kw):
        return OffsiteConfig(self.bucket, "ACCESS", "SECRET", self.endpoint, "auto", "tendai/", allow_http=True, **kw)

    def close(self):
        self.server.shutdown()


@pytest.fixture
def s3():
    b = FakeBucket()
    yield b
    b.close()


DAY = datetime(2026, 11, 20, 3, 0)


def test_the_daily_copies_arrive_compressed_and_intact(real, s3):
    real.backups.daily(DAY)
    sent = offsite.send_daily(Bucket(s3.config()), real.dir / "backups", "2026-11-20")
    assert sent == ["tendai/daily/tender_state-2026-11-20.db.gz", "tendai/daily/boq-2026-11-20.db.gz"]
    assert gzip.decompress(s3.objects[sent[0]]) == (real.dir / "backups" / "tender_state-2026-11-20.db").read_bytes()


def test_old_copies_are_removed_beyond_the_keep_count(real, s3):
    bucket = Bucket(s3.config(keep_daily=2))
    for d in range(20, 25):
        real.backups.daily(datetime(2026, 11, d, 3, 0))
        offsite.send_daily(bucket, real.dir / "backups", f"2026-11-{d}")
    days = sorted({re.search(r"(\d{4}-\d{2}-\d{2})", k).group(1) for k in s3.objects})
    assert days == ["2026-11-23", "2026-11-24"] and len(s3.objects) == 4


def test_a_bad_signature_or_a_refusal_is_reported_in_words(real):
    bad = FakeBucket()
    try:
        cfg = bad.config()
        cfg.secret_key = "WRONG"
        real.backups.daily(DAY)
        with pytest.raises(OffsiteError, match="403.*signature"):
            offsite.send_daily(Bucket(cfg), real.dir / "backups", "2026-11-20")
    finally:
        bad.close()
    denied = FakeBucket(fail_with=403)
    try:
        with pytest.raises(OffsiteError, match="Access Denied"):
            Bucket(denied.config()).list("tendai/")
    finally:
        denied.close()
    with pytest.raises(OffsiteError, match="Could not reach"):
        Bucket(OffsiteConfig("bk", "ACCESS", "SECRET", "http://127.0.0.1:9", allow_http=True)).list("x/")


def test_the_weekly_zip_goes_when_the_last_one_is_a_week_old(real, s3):
    bucket = Bucket(s3.config())
    assert offsite.weekly_due(bucket, DAY) is True  # nothing there yet
    zp = real.backups.build_zip(DAY)
    key = offsite.send_weekly(bucket, zp, "2026-11-20")
    zp.unlink()
    assert key == "tendai/weekly/tendai-backup-2026-11-20.zip"
    with zipfile.ZipFile(__import__("io").BytesIO(s3.objects[key])) as z:
        assert {"tender_state.db", "boq.db", "vault/gst.pdf"} <= set(z.namelist())
    assert offsite.weekly_due(bucket, DAY + timedelta(days=6)) is False
    assert offsite.weekly_due(bucket, DAY + timedelta(days=7)) is True


def remote_backups(real, s3, **kw):
    return Backups(real.store, real.boq.store, str(real.state), real.vault, remote=s3.config(**kw))


def test_the_scheduler_hook_uploads_once_a_day_and_records_the_outcome(real, s3):
    b = remote_backups(real, s3)
    st = b.upload_offsite(DAY)
    assert st["last_daily"] == "2026-11-20" and st["last_weekly"] == "2026-11-20" and st["last_error"] is None
    n = len(s3.calls)
    b.upload_offsite(DAY + timedelta(hours=1))  # same day: nothing more is sent
    assert len(s3.calls) == n
    assert b.status()["offsite"]["last_ok"] and b.status()["offsite"]["bucket"] == "bk"
    b.upload_offsite(DAY + timedelta(days=1))  # tomorrow: the daily copies go, the weekly zip does not
    assert not any("2026-11-21.zip" in k for k in s3.objects) and any("2026-11-21.db.gz" in k for k in s3.objects)


def test_a_failed_upload_is_recorded_and_retried_after_half_an_hour(real):
    down = FakeBucket(fail_with=500)
    try:
        b = Backups(real.store, real.boq.store, str(real.state), real.vault, remote=down.config())
        st = b.upload_offsite(DAY)
        assert "500" in st["last_error"] and st.get("last_daily") is None
        calls = len(down.calls)
        b.upload_offsite(DAY + timedelta(minutes=10))  # too soon to try again
        assert len(down.calls) == calls
        down.fail_with = None
        st = b.upload_offsite(DAY + timedelta(minutes=31))
        assert st["last_error"] is None and st["last_daily"] == "2026-11-20"
    finally:
        down.close()


def test_the_admin_can_send_a_backup_now(real, s3):
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), settings(state_path=str(real.state)), real.store)
    app.state.backups = None
    c = TestClient(app)
    c.post("/api/login", json={"password": PASSWORD})
    assert c.post("/api/backup/offsite-now").status_code == 422  # not set up on this server
    assert c.get("/api/backup/status").json()["offsite"]["configured"] is False


def test_off_site_status_and_send_now_through_the_api(real, s3, monkeypatch):
    for k, v in {"BACKUP_S3_BUCKET": "bk", "BACKUP_S3_ACCESS_KEY": "ACCESS", "BACKUP_S3_SECRET_KEY": "SECRET", "BACKUP_S3_ENDPOINT": s3.endpoint, "BACKUP_S3_ALLOW_HTTP": "true"}.items():
        monkeypatch.setenv(k, v)
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), settings(state_path=str(real.state)), real.store)
    c = TestClient(app)
    c.post("/api/login", json={"password": PASSWORD})
    assert c.get("/api/backup/status").json()["offsite"]["configured"] is True
    r = c.post("/api/backup/offsite-now")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["last_weekly"]
    assert any(k.startswith("tendai/weekly/") for k in s3.objects)


def test_a_half_set_up_configuration_does_not_stop_the_app_and_says_why(real, monkeypatch):
    monkeypatch.setenv("BACKUP_S3_BUCKET", "only-a-bucket")
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False), settings(state_path=str(real.state)), real.store)
    c = TestClient(app)
    c.post("/api/login", json={"password": PASSWORD})
    st = c.get("/api/backup/status").json()["offsite"]
    assert st["configured"] is False and "all set" in st["last_error"]


def test_only_admins_can_send_backups_off_site(real, s3):
    app = web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), settings(state_path=str(real.state)), real.store)
    owner = TestClient(app)
    owner.post("/api/login", json={"username": "admin", "password": PASSWORD})
    owner.post("/api/users", json={"username": "bob", "role": "bidder", "password": "temporary-pass-1"})
    bob = TestClient(app)
    bob.post("/api/login", json={"username": "bob", "password": "temporary-pass-1"})
    bob.post("/api/account/password", json={"current": "temporary-pass-1", "new": "purple-tractor-window"})
    assert bob.post("/api/backup/offsite-now").status_code == 403
