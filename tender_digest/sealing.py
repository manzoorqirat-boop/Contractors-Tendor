"""Encryption at rest for the secrets the app keeps: two-step sign-in secrets, webhook secrets and every uploaded file (the Vault and saved tender notices).

Set DATA_ENCRYPTION_KEY on the server (a long random phrase, at least 16 characters) and new data is stored encrypted; anything already stored
is encrypted at the next start. Encryption is Fernet (AES-128-CBC with an HMAC) from the `cryptography` package, with the key derived from your phrase.

  * Keep the phrase safe, somewhere other than the server. Without it the encrypted data cannot be read, and neither can a restored backup.
  * To change it: set DATA_ENCRYPTION_KEY to the new phrase and DATA_ENCRYPTION_KEY_OLD to the old one, restart, and remove the old one after.
  * The databases themselves (tenders, bids, comments) are not encrypted by this: it protects the secrets and the uploaded papers, which are
    what a copy of the volume or a backup would otherwise expose most. Disk-level encryption is the platform's job.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger("tender_digest.sealing")
FILE_MAGIC = b"TNDENC1\n"
TEXT_PREFIX = "enc1:"
MIN_KEY = 16


class SealError(Exception):
    pass


def _fernet(phrase: str) -> Fernet:
    key = hashlib.scrypt(phrase.encode(), salt=b"tendai-data-key-v1", n=2 ** 14, r=8, p=1, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


class Sealer:
    """Encrypts and decrypts. With no key set it passes plain data through, and refuses (with a clear message) to read data that is encrypted."""

    def __init__(self, key: str = "", old_keys: list[str] | None = None):
        key = (key or "").strip()
        olds = [k.strip() for k in (old_keys or []) if k and k.strip()]
        for k in [key, *olds]:
            if k and len(k) < MIN_KEY:
                raise SealError(f"DATA_ENCRYPTION_KEY must be at least {MIN_KEY} characters. A few random words joined together works well")
        self.enabled = bool(key)
        self.has_old = bool(olds)
        self._fer = MultiFernet([_fernet(k) for k in [key, *olds]]) if key else None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Sealer:
        env = os.environ if environ is None else environ
        olds = [x for x in env.get("DATA_ENCRYPTION_KEY_OLD", "").split(",") if x.strip()]
        return cls(env.get("DATA_ENCRYPTION_KEY", ""), olds)

    # -- text (secrets held in the database) --

    def seal_text(self, text: str) -> str:
        if not self._fer or not text or text.startswith(TEXT_PREFIX):
            return text
        return TEXT_PREFIX + self._fer.encrypt(text.encode()).decode()

    def open_text(self, text: str) -> str:
        if not text or not text.startswith(TEXT_PREFIX):
            return text
        if not self._fer:
            raise SealError("This data is encrypted but DATA_ENCRYPTION_KEY is not set on the server")
        try:
            return self._fer.decrypt(text[len(TEXT_PREFIX):].encode()).decode()
        except InvalidToken:
            raise SealError("This data was encrypted with a different DATA_ENCRYPTION_KEY") from None

    def is_sealed_text(self, text: str | None) -> bool:
        return bool(text) and text.startswith(TEXT_PREFIX)  # type: ignore[union-attr]

    # -- files --

    def seal_bytes(self, data: bytes) -> bytes:
        if not self._fer or data.startswith(FILE_MAGIC):
            return data
        return FILE_MAGIC + self._fer.encrypt(data)

    def open_bytes(self, data: bytes) -> bytes:
        if not data.startswith(FILE_MAGIC):
            return data
        if not self._fer:
            raise SealError("This file is encrypted but DATA_ENCRYPTION_KEY is not set on the server")
        try:
            return self._fer.decrypt(data[len(FILE_MAGIC):])
        except InvalidToken:
            raise SealError("This file was encrypted with a different DATA_ENCRYPTION_KEY") from None

    def read_file(self, path: Path) -> bytes:
        return self.open_bytes(path.read_bytes())

    def write_file(self, path: Path, data: bytes) -> None:
        """Writes the file (encrypted when a key is set) by building it beside the target and moving it into place."""
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(self.seal_bytes(data))
        tmp.replace(path)

    def rotate_text(self, text: str) -> str:
        """The same secret, encrypted under the current key (after a key change)."""
        if not self._fer or not self.is_sealed_text(text):
            return self.seal_text(text)
        return TEXT_PREFIX + self._fer.rotate(text[len(TEXT_PREFIX):].encode()).decode()

    def rotate_file(self, path: Path) -> str:
        """Brings one file up to date: plain files are encrypted, files under an old key are re-encrypted. Returns what was done: 'sealed', 'rotated' or ''."""
        if not self._fer:
            return ""
        raw = path.read_bytes()
        if not raw.startswith(FILE_MAGIC):
            self.write_file(path, raw)
            return "sealed"
        if self.has_old:
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(FILE_MAGIC + self._fer.rotate(raw[len(FILE_MAGIC):]))
            tmp.replace(path)
            return "rotated"
        return ""


def describe(sealer: Sealer, problem: str = "", plain_files: int = 0, plain_secrets: int = 0) -> dict[str, Any]:
    return {"enabled": sealer.enabled, "problem": problem, "plain_files": plain_files, "plain_secrets": plain_secrets, "rotating": sealer.has_old}
