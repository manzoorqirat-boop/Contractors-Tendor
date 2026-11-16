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
    assert app_client.get("/api/branding").json() == {"logo_text": "QM", "accent": "blue"}


def test_logo_is_set_from_settings_and_shown_on_the_login_screen(app_client):
    assert app_client.put("/api/settings", json={"logo_text": "Zx"}).status_code == 401  # changing it needs a login
    app_client.post("/api/login", json={"password": PASSWORD})
    r = app_client.put("/api/settings", json={"logo_text": " Ab1 "})
    assert r.status_code == 200 and r.json()["logo_text"] == "Ab1"
    app_client.post("/api/logout")
    assert app_client.get("/api/branding").json() == {"logo_text": "Ab1", "accent": "blue"}


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
    assert app_client.get("/api/branding").json() == {"logo_text": "QM", "accent": "teal"}      # public, so the sign-in screen can use it
    assert appsettings.current(env_settings(), {"accent": "not-a-colour"})["accent"] == "blue"        # a damaged saved value falls back, never breaks the page
    assert appsettings.ACCENTS[0] == "blue" and len(set(appsettings.ACCENTS)) == len(appsettings.ACCENTS)
