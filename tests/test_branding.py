import pytest
from fastapi.testclient import TestClient

from tender_digest import appsettings, web
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from test_portals import PASSWORD, env_settings


@pytest.fixture
def app_client():
    store, env = SeenStore(":memory:"), env_settings()
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, RunManager(env, store, background=False))
    return TestClient(app)


def test_logo_defaults_to_qm_and_is_readable_before_login(app_client):
    assert app_client.get("/api/branding").json() == {"logo_text": "QM", "accent": "blue", "logo_url": None}


def test_logo_is_set_from_settings_and_shown_on_the_login_screen(app_client):
    assert app_client.put("/api/settings", json={"logo_text": "Zx"}).status_code == 401  # changing it needs a login
    app_client.post("/api/login", json={"password": PASSWORD})
    r = app_client.put("/api/settings", json={"logo_text": " Ab1 "})
    assert r.status_code == 200 and r.json()["logo_text"] == "Ab1"
    app_client.post("/api/logout")
    assert app_client.get("/api/branding").json() == {"logo_text": "Ab1", "accent": "blue", "logo_url": None}


@pytest.mark.parametrize("bad", ["", "   ", "ABCD", "A B", "<b>", "A_", "&", None, 5.5e9])
def test_bad_logo_letters_are_rejected(bad):
    with pytest.raises(appsettings.SettingsError):
        appsettings.clean({"logo_text": bad}, [])


def test_accented_and_other_script_letters_are_allowed():
    assert appsettings.clean({"logo_text": "\u00c9q"}, []) == {"logo_text": "\u00c9q"}
    assert appsettings.clean({"logo_text": "\u0915"}, []) == {"logo_text": "\u0915"}  # a Devanagari letter


def test_the_company_colour_is_one_of_the_offered_ones_and_shows_on_the_sign_in_screen(app_client):
    app_client.post("/api/login", json={"password": "correct horse"})
    for bad in ("red", "", None, 5, "Blue", "#ff0000", "teal; background:url(x)"):
        assert app_client.put("/api/settings", json={"accent": bad}).status_code == 422, bad
    assert app_client.put("/api/settings", json={"accent": "teal"}).json()["accent"] == "teal"
    app_client.post("/api/logout")
    assert app_client.get("/api/branding").json() == {"logo_text": "QM", "accent": "teal", "logo_url": None}      # public, so the sign-in screen can use it
    assert appsettings.current(env_settings(), {"accent": "not-a-colour"})["accent"] == "blue"        # a damaged saved value falls back, never breaks the page
    assert appsettings.ACCENTS[0] == "blue" and len(set(appsettings.ACCENTS)) == len(appsettings.ACCENTS)


# ---- the company's logo picture ---------------------------------------------------------------------------------------

import base64
import struct
import zlib

from tender_digest import branding


def png(w=64, h=64, pad=0):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + b"\x20\x60\xc0" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"") + b"\0" * pad


def jpeg(w=80, h=40):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xd9"


def webp_lossless(w=70, h=30):
    bits = (w - 1) | ((h - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits) + b"\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def upload(client, data, wrapped=False):
    text = base64.b64encode(data).decode()
    return client.put("/api/settings/logo", json={"file_base64": ("data:image/png;base64," + text) if wrapped else text})


@pytest.fixture
def owner(app_client):
    assert app_client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return app_client


def test_each_accepted_kind_is_recognised_with_its_size():
    assert branding.check(png(64, 48)) == ("image/png", 64, 48)
    assert branding.check(jpeg(80, 40)) == ("image/jpeg", 80, 40)
    assert branding.check(webp_lossless(70, 30)) == ("image/webp", 70, 30)


@pytest.mark.parametrize("data,why", [
    (b"", "Choose a picture"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "PNG, JPEG or WebP"),
    (b"GIF89a" + b"\0" * 40, "PNG, JPEG or WebP"),
    (b"MZ" + b"\0" * 100, "PNG, JPEG or WebP"),
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 10, "damaged"),
    (png(8, 8), "at least"),
    (png(3000, 40), "no larger than"),
    (png(64, 64, pad=branding.MAX_BYTES), "under"),
])
def test_a_file_that_is_not_a_usable_logo_is_refused_with_the_reason(data, why):
    with pytest.raises(branding.LogoError, match=why):
        branding.check(data)


def test_an_administrator_uploads_a_logo_and_anyone_can_see_it_before_signing_in(owner):
    r = upload(owner, png(120, 60))
    assert r.status_code == 200 and (r.json()["width"], r.json()["height"]) == (120, 60)
    owner.post("/api/logout")
    url = owner.get("/api/branding").json()["logo_url"]
    assert url.startswith("/api/branding/logo?v=")
    pic = owner.get(url)
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/png" and pic.content == png(120, 60)
    assert "max-age" in pic.headers["cache-control"] and pic.headers["x-content-type-options"] == "nosniff"


def test_a_pasted_data_address_is_accepted_too(owner):
    assert upload(owner, png(), wrapped=True).status_code == 200


def test_the_file_is_judged_by_its_bytes_never_by_what_it_says_it_is(owner):
    assert upload(owner, b"<svg onload=alert(1)>" * 40).status_code == 422
    assert owner.put("/api/settings/logo", json={"file_base64": "not base64 !!"}).status_code == 422
    assert owner.get("/api/branding").json()["logo_url"] is None                          # nothing was stored by the refused ones


def test_replacing_the_logo_changes_its_address_so_browsers_fetch_the_new_one(owner):
    import time
    upload(owner, png(64, 64))
    first = owner.get("/api/branding").json()["logo_url"]
    time.sleep(1.1)
    upload(owner, jpeg(90, 90))
    second = owner.get("/api/branding").json()["logo_url"]
    assert first != second and owner.get(second).headers["content-type"] == "image/jpeg"


def test_removing_the_logo_goes_back_to_the_letters(owner):
    upload(owner, png())
    assert owner.delete("/api/settings/logo").json()["removed"] is True
    assert owner.get("/api/branding").json()["logo_url"] is None and owner.get("/api/branding/logo").status_code == 404
    assert owner.delete("/api/settings/logo").json()["removed"] is False


def test_only_a_signed_in_administrator_can_change_it(app_client):
    assert upload(app_client, png()).status_code == 401
    assert app_client.delete("/api/settings/logo").status_code == 401
    app_client.post("/api/login", json={"password": PASSWORD})
    made = app_client.post("/api/users", json={"username": "bina", "name": "Bina", "role": "bidder", "password": "bidder password 1"})
    assert made.status_code == 200, made.text
    app_client.post("/api/logout")
    assert app_client.post("/api/login", json={"username": "bina", "password": "bidder password 1"}).status_code == 200
    assert upload(app_client, png()).status_code == 403 and app_client.delete("/api/settings/logo").status_code == 403


def test_the_change_is_in_the_activity_log_by_name(owner):
    upload(owner, png())
    owner.delete("/api/settings/logo")
    entries = [e["action"] for e in owner.get("/api/audit").json()["entries"]]
    assert "Changed the company logo picture" in entries and "Removed the company logo picture" in entries


def test_the_logo_travels_with_a_backup(tmp_path):
    from datetime import datetime

    from tender_digest.backup import copy_database
    store = SeenStore(":memory:")
    store.put_logo("image/png", png(100, 100), datetime(2026, 11, 19, 9, 0))
    copy_database(store.db, store.lock, tmp_path / "copy.db")            # the same copy the daily backup and the downloaded zip use
    restored = SeenStore(str(tmp_path / "copy.db"))
    assert restored.get_logo() == {"mime": "image/png", "data": png(100, 100), "updated": "2026-11-19T09:00:00"}
    restored.close()
