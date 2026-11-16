"""Copies backups to a bucket outside Railway, so losing the volume does not lose everything.

Works with anything that speaks the S3 protocol: Amazon S3, Cloudflare R2, Backblaze B2, MinIO. Requests are signed with AWS Signature
Version 4, written here with the standard library so there is nothing extra to install.

What is sent:
  daily/   gzip copies of the two databases, once a day (the newest BACKUP_S3_KEEP_DAILY are kept)
  weekly/  the full zip (databases and every Vault file), once a week (the newest BACKUP_S3_KEEP_WEEKLY are kept)

Set these variables on the service (the key pair needs write, list and delete on that bucket only):
  BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY            required
  BACKUP_S3_ENDPOINT   e.g. https://<account>.r2.cloudflarestorage.com    (blank = Amazon S3 in BACKUP_S3_REGION)
  BACKUP_S3_REGION     default "auto" (R2); use e.g. "ap-south-1" for Amazon S3
  BACKUP_S3_PREFIX     default "tendai/"
  BACKUP_S3_KEEP_DAILY (14), BACKUP_S3_KEEP_WEEKLY (8)
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import logging
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger("tender_digest.offsite")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class OffsiteError(Exception):
    pass


@dataclass
class OffsiteConfig:
    bucket: str
    access_key: str
    secret_key: str
    endpoint: str = ""
    region: str = "auto"
    prefix: str = "tendai/"
    keep_daily: int = 14
    keep_weekly: int = 8
    allow_http: bool = False  # only for a local test server

    @property
    def base(self) -> str:
        return (self.endpoint or f"https://s3.{self.region}.amazonaws.com").rstrip("/")


def config_from_env(environ: dict[str, str] | None = None) -> OffsiteConfig | None:
    """None when off-site backup is not set up. Raises OffsiteError for a half-set-up or unsafe configuration."""
    env = os.environ if environ is None else environ
    bucket, access, secret = (env.get(k, "").strip() for k in ("BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY"))
    if not (bucket or access or secret):
        return None
    if not (bucket and access and secret):
        raise OffsiteError("Off-site backup needs BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY all set")

    def count(name: str, default: int) -> int:
        try:
            return max(1, int(env.get(name, "") or default))
        except ValueError:
            return default

    cfg = OffsiteConfig(bucket, access, secret, env.get("BACKUP_S3_ENDPOINT", "").strip(), env.get("BACKUP_S3_REGION", "").strip() or "auto",
                        (env.get("BACKUP_S3_PREFIX", "tendai/").strip().strip("/") + "/").lstrip("/"), count("BACKUP_S3_KEEP_DAILY", 14), count("BACKUP_S3_KEEP_WEEKLY", 8),
                        env.get("BACKUP_S3_ALLOW_HTTP", "").lower() in ("1", "true", "yes"))
    if urlparse(cfg.base).scheme != "https" and not cfg.allow_http:
        raise OffsiteError("BACKUP_S3_ENDPOINT must start with https:// (your keys travel with every request)")
    return cfg


# ---- AWS Signature Version 4 ----------------------------------------------------------------------------------------------

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(method: str, host: str, path: str, query: dict[str, str], headers: dict[str, str], payload_sha256: str, *, access_key: str, secret_key: str,
         region: str, when: datetime, service: str = "s3") -> dict[str, str]:
    """The headers to send (including Authorization) for one request. `headers` are the extra ones to sign, such as Range or Content-Type."""
    stamp, day = when.strftime("%Y%m%dT%H%M%SZ"), when.strftime("%Y%m%d")
    signed = {"host": host, "x-amz-content-sha256": payload_sha256, "x-amz-date": stamp, **{k.lower(): v.strip() for k, v in headers.items()}}
    names = ";".join(sorted(signed))
    canonical_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    canonical = "\n".join([method, quote(path, safe="/-_.~"), canonical_query, canonical_headers, names, payload_sha256])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k: v for k, v in signed.items() if k != "host"}
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},SignedHeaders={names},Signature={signature}"
    return out


class Bucket:
    def __init__(self, cfg: OffsiteConfig, session: requests.Session | None = None, clock=lambda: datetime.now(timezone.utc)):
        self.cfg, self.http, self.clock = cfg, session or requests.Session(), clock
        u = urlparse(cfg.base)
        self.host, self.root = u.netloc, u.path.rstrip("/")

    def _request(self, method: str, key: str = "", query: dict[str, str] | None = None, body: BinaryIO | bytes | None = None, payload_sha256: str = EMPTY_SHA256,
                 extra: dict[str, str] | None = None, timeout: int = 600) -> requests.Response:
        path = f"{self.root}/{self.cfg.bucket}" + (f"/{key}" if key else "")  # path-style: works for S3, R2, B2 and MinIO alike
        headers = sign(method, self.host, path, query or {}, extra or {}, payload_sha256, access_key=self.cfg.access_key, secret_key=self.cfg.secret_key,
                       region=self.cfg.region, when=self.clock())
        try:
            r = self.http.request(method, f"{urlparse(self.cfg.base).scheme}://{self.host}{quote(path, safe='/-_.~')}", params=query or None, headers=headers, data=body, timeout=timeout)
        except requests.RequestException as e:
            raise OffsiteError(f"Could not reach the backup bucket: {str(e)[:160]}") from None
        if r.status_code >= 300:
            raise OffsiteError(f"The bucket refused the request ({r.status_code}): {_reason(r.text)}")
        return r

    def put_file(self, key: str, path: Path) -> None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        with open(path, "rb") as f:
            self._request("PUT", key, body=f, payload_sha256=h.hexdigest(), extra={"content-type": "application/octet-stream"})

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = ""
        while True:
            q = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            root = ET.fromstring(self._request("GET", query=q).text)
            ns = {"s": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
            find = (lambda el, tag: el.findall(f"s:{tag}", ns)) if ns else (lambda el, tag: el.findall(tag))
            keys += [k.text or "" for c in find(root, "Contents") for k in find(c, "Key")]
            more = find(root, "IsTruncated")
            nxt = find(root, "NextContinuationToken")
            if more and (more[0].text or "").lower() == "true" and nxt:
                token = nxt[0].text or ""
            else:
                return keys

    def delete(self, key: str) -> None:
        self._request("DELETE", key)


def _reason(body: str) -> str:
    m = re.search(r"<Message>(.*?)</Message>", body or "", re.S)
    return (m.group(1) if m else (body or "no detail"))[:160]


# ---- what to send, and what to keep ----------------------------------------------------------------------------------------

def _gz(src: Path, dest: Path) -> None:
    with open(src, "rb") as i, gzip.open(dest, "wb", compresslevel=6) as o:
        shutil.copyfileobj(i, o)


def prune(bucket: Bucket, key_prefix: str, keep: int) -> list[str]:
    """Deletes all but the newest `keep` objects whose key starts with `key_prefix`. Names end in their date, so the newest sort last.
    Each kind of file is pruned on its own prefix, so one kind can never crowd out another."""
    names = sorted(k for k in bucket.list(key_prefix) if not k.endswith("/"))
    old = names[:-keep] if len(names) > keep else []
    for k in old:
        bucket.delete(k)
    return old


def send_daily(bucket: Bucket, snapshot_dir: Path, day: str) -> list[str]:
    """Uploads today's two database copies (made by the local daily backup), compressed."""
    sent = []
    with tempfile.TemporaryDirectory(prefix="tendai-offsite-") as tmp:
        for stem in ("tender_state", "boq"):
            src = snapshot_dir / f"{stem}-{day}.db"
            if not src.exists():
                raise OffsiteError(f"Today's copy {src.name} is not there yet")
            gz = Path(tmp) / f"{stem}-{day}.db.gz"
            _gz(src, gz)
            key = f"{bucket.cfg.prefix}daily/{stem}-{day}.db.gz"
            bucket.put_file(key, gz)
            sent.append(key)
    for stem in ("tender_state", "boq"):
        prune(bucket, f"{bucket.cfg.prefix}daily/{stem}-", bucket.cfg.keep_daily)
    return sent


def send_weekly(bucket: Bucket, zip_path: Path, day: str) -> str:
    key = f"{bucket.cfg.prefix}weekly/tendai-backup-{day}.zip"
    bucket.put_file(key, zip_path)
    prune(bucket, f"{bucket.cfg.prefix}weekly/tendai-backup-", bucket.cfg.keep_weekly)
    return key


def weekly_due(bucket: Bucket, today: datetime) -> bool:
    """True when the newest weekly upload is seven or more days old (or there is none)."""
    names = sorted(k for k in bucket.list(bucket.cfg.prefix + "weekly/") if k.endswith(".zip"))
    if not names:
        return True
    m = re.search(r"(\d{4}-\d{2}-\d{2})\.zip$", names[-1])
    return not m or (today.date() - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days >= 7


def describe(cfg: OffsiteConfig | None, state: dict[str, Any]) -> dict[str, Any]:
    """What the Settings page shows about off-site backups."""
    return {"configured": cfg is not None, "bucket": cfg.bucket if cfg else "", "endpoint": (urlparse(cfg.base).netloc if cfg else ""), "last_ok": state.get("last_ok"),
            "last_error": state.get("last_error"), "last_daily": state.get("last_daily"), "last_weekly": state.get("last_weekly"),
            "keep_daily": cfg.keep_daily if cfg else 0, "keep_weekly": cfg.keep_weekly if cfg else 0}
