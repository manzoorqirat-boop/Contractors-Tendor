"""One deployment per customer: the generated setup is strong and private, and the go-live check says what is still wrong."""
import os
import stat
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tender_digest import coverage, provision, web
from tender_digest.config import Portal, Settings
from tender_digest.gepnic import Tender
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore

# ---- generated secrets -------------------------------------------------------------------------------------------

def test_passwords_are_long_readable_and_never_repeat():
    seen = {provision.new_password() for _ in range(200)}
    assert len(seen) == 200
    for p in list(seen)[:20]:
        assert len(p) == 24 and p.count("-") == 4
        assert not set(p) & set("0O1lI")                    # nothing that is mistaken for something else when read out


def test_every_customer_gets_their_own_keys():
    a, b = provision.make_secrets(), provision.make_secrets()
    assert all(a[k] != b[k] for k in a)
    assert len(a["SECRET_KEY"]) == 64 and len(a["DATA_ENCRYPTION_KEY"]) >= 32


@pytest.mark.parametrize("raw,ok", [("Acme Infra", "acme-infra"), ("  Shree  Ganesh & Sons (P) Ltd. ", "shree-ganesh-sons-p-ltd"), ("../../etc", "etc")])
def test_a_customer_name_becomes_a_safe_folder_name(raw, ok):
    assert provision.slug_of(raw) == ok


def test_a_name_with_nothing_usable_is_refused():
    with pytest.raises(ValueError):
        provision.slug_of("!!!")


def test_addresses_are_checked():
    assert provision.clean_emails("a@x.in, b@y.in; a@x.in") == ["a@x.in", "b@y.in"]
    with pytest.raises(ValueError):
        provision.clean_emails("a@x.in, nope")
    assert provision.clean_url("https://tend.acme.in/") == "https://tend.acme.in"
    for bad in ("http://tend.acme.in", "tend.acme.in", "https://tend.acme.in/app", "https://"):
        with pytest.raises(ValueError):
            provision.clean_url(bad)


# ---- the kit -----------------------------------------------------------------------------------------------------

def kit(tmp_path, **kw):
    args = dict(out_root=tmp_path / "customers", name="Acme Infra", url="https://tend.acme.in", recipients=["a@acme.in", "b@acme.in"], mail_from="tenders@acme.in")
    return provision.write_kit(**{**args, **kw})


def test_the_kit_has_the_variables_and_a_checklist_for_that_customer(tmp_path):
    paths = kit(tmp_path)
    env = dict(line.split("=", 1) for line in paths["env"].read_text().splitlines() if line and not line.startswith("#"))
    assert env["APP_URL"] == "https://tend.acme.in" and env["RECIPIENTS"] == "a@acme.in,b@acme.in" and env["MAIL_FROM"] == "tenders@acme.in"
    assert env["STATE_PATH"] == "/data/tender_state.db" and env["COOKIE_SECURE"] == "true" and env["APP_USERNAME"] == "admin"
    assert len(env["APP_PASSWORD"]) == 24 and len(env["SECRET_KEY"]) == 64 and env["DATA_ENCRYPTION_KEY"]
    text = paths["checklist"].read_text()
    assert "Acme Infra" in text and "https://tend.acme.in" in text and "acme-infra.env" in text and "Volume" in text and "provision verify --url https://tend.acme.in" in text
    assert "BREVO_API_KEY" in text                                        # not in the file, so the checklist says to add it
    assert env["APP_PASSWORD"] not in text                                # the checklist can be shared; the password is only in the .env file


def test_the_settings_file_is_readable_by_its_owner_only(tmp_path):
    paths = kit(tmp_path)
    assert stat.S_IMODE(os.stat(paths["env"]).st_mode) == 0o600


def test_customer_folders_are_kept_out_of_git(tmp_path):
    kit(tmp_path)
    assert (tmp_path / "customers" / ".gitignore").read_text().splitlines()[-1] == "*"


def test_a_second_run_never_replaces_the_passwords_by_accident(tmp_path):
    first = kit(tmp_path)
    before = first["env"].read_text()
    with pytest.raises(FileExistsError):
        kit(tmp_path)
    assert first["env"].read_text() == before
    kit(tmp_path, force=True)
    assert first["env"].read_text() != before


def test_two_customers_share_nothing(tmp_path):
    a, b = kit(tmp_path), kit(tmp_path, name="Beta Builders", url="https://tend.beta.in")
    ea, eb = (dict(x.split("=", 1) for x in p["env"].read_text().splitlines() if x and not x.startswith("#")) for p in (a, b))
    assert ea["APP_PASSWORD"] != eb["APP_PASSWORD"] and ea["SECRET_KEY"] != eb["SECRET_KEY"] and ea["DATA_ENCRYPTION_KEY"] != eb["DATA_ENCRYPTION_KEY"]
    assert a["folder"] != b["folder"]


def test_a_brevo_key_from_the_environment_goes_into_the_file_and_the_checklist_stops_asking(tmp_path):
    paths = kit(tmp_path, brevo_key="xkeysib-123")
    assert "BREVO_API_KEY=xkeysib-123" in paths["env"].read_text()
    assert "Add `BREVO_API_KEY`" not in paths["checklist"].read_text()


def test_the_command_writes_the_kit_and_never_prints_a_secret(tmp_path, monkeypatch):
    lines = []
    monkeypatch.setenv("MY_BREVO", "xkeysib-secret-value")
    code = provision.main(["new", "--name", "Acme Infra", "--url", "https://tend.acme.in", "--recipients", "a@acme.in", "--mail-from", "t@acme.in",
                           "--out", str(tmp_path / "c"), "--brevo-key-env", "MY_BREVO"], out=lines.append)
    assert code == 0
    written = (tmp_path / "c" / "acme-infra" / "acme-infra.env").read_text()
    password = next(x.split("=", 1)[1] for x in written.splitlines() if x.startswith("APP_PASSWORD="))
    shown = "\n".join(lines)
    assert "acme-infra.env" in shown and password not in shown and "xkeysib-secret-value" not in shown


def test_the_command_explains_a_mistake_instead_of_a_traceback(tmp_path):
    lines = []
    assert provision.main(["new", "--name", "X", "--url", "http://x.in", "--recipients", "a@x.in", "--mail-from", "t@x.in", "--out", str(tmp_path)], out=lines.append) == 2
    assert "https" in lines[0] and not (tmp_path / "x").exists()


# ---- the go-live check -------------------------------------------------------------------------------------------

PASSWORD = "correct horse battery"


class Browser:
    """The app's test client, speaking the way the check's HTTP session does (the test client takes no timeout)."""

    def __init__(self, client):
        self.client = client

    def get(self, url, timeout=None):
        return self.client.get(url)

    def post(self, url, json=None, timeout=None):
        return self.client.post(url, json=json)


def site(*, mail=True, prove=True, encryption_key=None, monkeypatch=None, health_problem=False):
    if monkeypatch and encryption_key:
        monkeypatch.setenv("DATA_ENCRYPTION_KEY", encryption_key)
    store = SeenStore(":memory:")
    portal = Portal("maha", "Maharashtra", "MH", "https://m.gov.in/nicgep")
    env = Settings(portals=[portal], recipients=["a@x.in"] if mail else [], brevo_api_key="k" if mail else "", mail_from="f@x.in" if mail else "", mail_from_name="T",
                   state_path=":memory:", request_delay=0)
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False, username="admin"), env, store, RunManager(env, store, background=False))
    client = TestClient(app)
    now = datetime(2026, 11, 17, 9, 0)
    if prove:
        ts = [Tender("maha", f"T{i}", "Road", "", "PWD", datetime(2026, 11, 10), datetime(2026, 11, 30), None, detail_path="/d") for i in range(5)]

        class C:
            def organisations(self, attempts=1): return [("PWD", 5, "l")]
            def tenders_of(self, link, p): return ts
            def detail(self, t): return {"Work Description": "x"}

        coverage.record(store, portal, coverage.deep_probe(portal, client_factory=lambda p: C()), now)
    if health_problem:
        for i in range(3):
            store.put_portal_status("maha", "ok", "", now, 5, 40)
        store.put_portal_status("maha", "unreachable", "x", datetime.now(), 0, 0)
        store.put_portal_status("maha", "unreachable", "x", datetime.now(), 0, 0)
    return Browser(client)


def check(client, password=PASSWORD, url="https://tend.acme.in"):
    return {what: (status, detail) for status, what, detail in provision.verify(url, password, "admin", session=client)}


def test_a_healthy_site_has_nothing_to_fix_except_what_a_new_site_cannot_have_yet(monkeypatch):
    r = check(site(monkeypatch=monkeypatch, encryption_key="a long private phrase for tests"))
    assert r["Served over https"][0] == provision.PASS and r["The site answers"][0] == provision.PASS and r["Sign in works"][0] == provision.PASS
    assert r["Stored papers are encrypted"][0] == provision.PASS
    assert r["Daily email is set up (sender and recipients)"][0] == provision.PASS
    assert r["Portals are switched on and proven readable"][0] == provision.PASS
    assert r["No portal has stopped working"][0] == provision.PASS
    assert r["Off-site backup"][0] == provision.WARN and r["A daily backup copy exists"][0] == provision.WARN
    assert not [k for k, (s, _) in r.items() if s == provision.FAIL]


def test_a_wrong_password_is_reported_and_stops_the_check():
    r = check(site(), password="nope")
    assert r["Sign in works"][0] == provision.FAIL and "Wrong username or password" in r["Sign in works"][1]
    assert "Stored papers are encrypted" not in r


def test_a_site_without_an_encryption_key_fails():
    assert check(site())["Stored papers are encrypted"] == (provision.FAIL, "DATA_ENCRYPTION_KEY is not set")


def test_a_site_whose_email_is_not_set_up_fails():
    assert check(site(mail=False))["Daily email is set up (sender and recipients)"][0] == provision.FAIL


def test_portals_that_were_never_proven_fail_the_check():
    r = check(site(prove=False))["Portals are switched on and proven readable"]
    assert r[0] == provision.FAIL and "Test all switched-on portals" in r[1]


def test_a_portal_that_has_stopped_working_is_flagged():
    r = check(site(health_problem=True))["No portal has stopped working"]
    assert r[0] == provision.WARN and "Maharashtra" in r[1]


def test_http_is_a_failure():
    assert check(site(), url="http://tend.acme.in")["Served over https"][0] == provision.FAIL


def test_an_unreachable_site_is_reported_not_raised():
    import requests

    class Down:
        def get(self, *a, **k): raise requests.ConnectionError("no route")

    results = provision.verify("https://nowhere.example", "x", session=Down())
    assert results[-1][0] == provision.FAIL and "ConnectionError" in results[-1][2]


def test_the_summary_says_whether_it_is_ready(monkeypatch):
    good = [(provision.PASS, "a", "")]
    assert provision.format_results(good).endswith("Ready to hand over.")
    assert "1 to fix" in provision.format_results([(provision.FAIL, "a", "why")])
    assert "No blockers; 1 to look at." in provision.format_results([(provision.WARN, "a", "")])


def test_the_command_takes_the_password_from_the_environment_never_the_command_line(monkeypatch):
    lines = []
    monkeypatch.delenv("TENDAI_PASSWORD", raising=False)
    assert provision.main(["verify", "--url", "https://tend.acme.in"], out=lines.append) == 2
    assert "TENDAI_PASSWORD" in lines[0]
    monkeypatch.setenv("TENDAI_PASSWORD", PASSWORD)
    lines.clear()
    assert provision.main(["verify", "--url", "https://tend.acme.in"], out=lines.append, session=site()) == 1       # no encryption key: a failure
    assert "FAIL  Stored papers are encrypted" in lines[0]


def test_a_site_without_the_customers_logo_gets_a_reminder_until_it_is_uploaded(monkeypatch):
    import base64

    from tests.test_branding import png
    client = site()
    assert check(client)["The customer's logo picture is uploaded"][0] == provision.WARN
    client.client.put("/api/settings/logo", json={"file_base64": base64.b64encode(png()).decode()})
    assert check(client)["The customer's logo picture is uploaded"][0] == provision.PASS
