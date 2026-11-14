"""Adding portals from the page, and telling the person which ones the app cannot read on its own."""
import pytest
from fastapi.testclient import TestClient

from tender_digest import access, appsettings, collector, digest, gepnic, runner, web
from tender_digest.config import Portal, Settings
from tender_digest.gepnic import GepnicClient, PortalError
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore

PASSWORD = "correct horse"
GOOD = "https://etenders.kerala.gov.in/nicgep"


def env_settings() -> Settings:
    return Settings(
        portals=[Portal("maha", "Maharashtra", "MH", "https://m.gov.in/nicgep")], recipients=["a@example.com"], brevo_api_key="k",
        mail_from="f@example.com", mail_from_name="T", state_path=":memory:", request_delay=0,
    )


class Prober:
    """Stands in for the real network check: answers by address."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def __call__(self, url):
        self.calls.append(url)
        return self.answers.get(url, {"kind": "ok", "detail": "", "departments": 12, "tenders": 340})


@pytest.fixture
def ctx():
    store, env, prober = SeenStore(":memory:"), env_settings(), Prober()
    manager = RunManager(env, store, background=False)
    app = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), env, store, manager, prober)
    client = TestClient(app)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return type("Ctx", (), {"store": store, "env": env, "prober": prober, "client": client})


# ---- reading a page that is not a department list ---------------------------------------------------------------

@pytest.mark.parametrize("html,kind", [
    ("<form>Enter OTP sent to your mobile <input name=otp></form>", "otp"),
    ("<p>One Time Password has been sent</p>", "otp"),
    ("<img src=c.png> Enter Captcha <input name=captcha>", "captcha"),
    ("<form>User ID <input> Password <input type='password'></form>", "login"),
    ("<html><body>Welcome</body></html>", "different_software"),
])
def test_a_page_without_departments_is_classified(html, kind):
    assert gepnic.classify_page(html) == kind


class Resp:
    def __init__(self, status, text="", url="https://x.gov.in/nicgep/app", history=()):
        self.status_code, self.text, self.url, self.history = status, text, url, list(history)


class Http:
    def __init__(self, *responses):
        self.responses, self.calls, self.headers = list(responses), 0, {}

    def get(self, url, timeout):
        self.calls += 1
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses):
    return GepnicClient("https://x.gov.in/nicgep", delay=0, session=Http(*responses))


@pytest.mark.parametrize("status,kind", [(401, "login"), (403, "blocked"), (404, "different_software")])
def test_permanent_answers_are_named_and_not_retried(status, kind):
    c = client(Resp(status))
    with pytest.raises(PortalError) as e:
        c.organisations()
    assert e.value.kind == kind and c.http.calls == 1


def test_server_errors_are_temporary_and_retried(monkeypatch):
    monkeypatch.setattr(gepnic.time, "sleep", lambda s: None)
    c = client(Resp(503))
    with pytest.raises(PortalError) as e:
        c.organisations()
    assert e.value.kind == "unreachable" and c.http.calls == 3


def test_redirect_to_a_login_page_is_a_login_wall():
    c = client(Resp(200, "<p>Sign in</p>", url="https://x.gov.in/sso/login?next=1", history=[Resp(302)]))
    with pytest.raises(PortalError) as e:
        c.organisations()
    assert e.value.kind == "login"


def test_an_otp_page_is_reported_as_otp():
    c = client(Resp(200, "<form>Please enter the OTP sent to your registered mobile</form>"))
    with pytest.raises(PortalError) as e:
        c.organisations()
    assert e.value.kind == "otp" and "OTP" in str(e.value)


def test_probe_never_raises_and_reports_why(monkeypatch):
    monkeypatch.setattr(access, "is_public_host", lambda h: True)

    def boom(self, attempts=3):
        raise PortalError("the portal asks for a captcha", "captcha")

    monkeypatch.setattr(GepnicClient, "organisations", boom)
    assert access.probe(GOOD)["kind"] == "captcha"
    monkeypatch.setattr(GepnicClient, "organisations", lambda self, attempts=3: [("PWD", 5, "l"), ("Irrigation", 7, "l")])
    assert access.probe(GOOD) == {"kind": "ok", "detail": "", "departments": 2, "tenders": 12}


def test_probe_refuses_private_addresses(monkeypatch):
    monkeypatch.setattr(access, "is_public_host", lambda h: False)
    assert access.probe("https://internal.gov.in/nicgep")["kind"] == "blocked"


def test_describe_marks_what_needs_manual_checking():
    assert access.describe("ok")["manual"] is False and access.describe("unknown")["manual"] is False
    assert all(access.describe(k)["manual"] for k in ("captcha", "login", "otp", "blocked", "different_software", "unreachable"))
    assert access.describe("nonsense")["kind"] == "unknown"


# ---- validating a portal typed in the page ----------------------------------------------------------------------

def test_a_good_address_is_accepted_and_keyed_from_the_name():
    p = appsettings.clean_portal({"name": " Kerala (etenders) ", "base_url": GOOD + "/"}, set())
    assert p == {"key": "kerala-etenders", "name": "Kerala (etenders)", "state": "Kerala (etenders)", "base_url": GOOD}


@pytest.mark.parametrize("raw", [
    {"name": "x", "base_url": "http://etenders.kerala.gov.in/nicgep"},          # not https
    {"name": "x", "base_url": "https://evil.example.com/nicgep"},               # not a government address
    {"name": "x", "base_url": "https://gov.in.evil.com/nicgep"},                # suffix trick
    {"name": "x", "base_url": "https://127.0.0.1/nicgep"},                      # an IP
    {"name": "x", "base_url": "https://u:p@a.gov.in/nicgep"},                   # credentials in the address
    {"name": "x", "base_url": "https://a.gov.in:8443/nicgep"},                  # odd port
    {"name": "x", "base_url": "https://a.gov.in/nicgep?x=1"},                   # query string
    {"name": "", "base_url": GOOD}, {"name": "!!!", "base_url": GOOD}, {"base_url": GOOD}, "text", None,
])
def test_bad_portals_are_rejected(raw):
    with pytest.raises(appsettings.SettingsError):
        appsettings.clean_portal(raw, set())


def test_a_name_that_is_taken_is_rejected():
    with pytest.raises(appsettings.SettingsError):
        appsettings.clean_portal({"name": "Kerala", "base_url": GOOD}, {"kerala"})


def test_extra_address_endings_can_be_allowed(monkeypatch):
    with pytest.raises(appsettings.SettingsError):
        appsettings.clean_portal({"name": "Gujarat", "base_url": "https://tender.nprocure.com"}, set())
    monkeypatch.setenv("PORTAL_HOST_SUFFIXES", ".gov.in, nprocure.com")
    assert appsettings.clean_portal({"name": "Gujarat", "base_url": "https://tender.nprocure.com"}, set())["key"] == "gujarat"


# ---- the page's API ---------------------------------------------------------------------------------------------

def test_requires_login(ctx):
    ctx.client.post("/api/logout")
    assert ctx.client.get("/api/portals").status_code == 401
    assert ctx.client.post("/api/portals", json={"name": "K", "base_url": GOOD}).status_code == 401
    assert ctx.client.delete("/api/portals/x").status_code == 401


def test_a_readable_portal_is_added_and_switched_on(ctx):
    r = ctx.client.post("/api/portals", json={"name": "Kerala", "state": "Kerala", "base_url": GOOD})
    assert r.status_code == 200 and r.json()["kind"] == "ok" and r.json()["enabled"] and r.json()["custom"] and r.json()["departments"] == 12
    assert ctx.prober.calls == [GOOD]
    settings = ctx.client.get("/api/settings").json()
    assert settings["portals"] == ["maha", "kerala"] and {"key": "kerala", "name": "Kerala"} in settings["available_portals"]
    assert "kerala" in {p["key"] for p in ctx.client.get("/api/config").json()["portals"]}
    assert ctx.client.get("/api/portals").json()["manual"] == []


def test_a_portal_that_needs_otp_goes_on_the_manual_list_and_is_not_run(ctx):
    url = "https://www.ireps.gov.in"
    ctx.prober.answers[url] = {"kind": "otp", "detail": "the portal asks for an OTP login", "departments": 0, "tenders": 0}
    r = ctx.client.post("/api/portals", json={"name": "Railways IREPS", "base_url": url})
    assert r.status_code == 200 and r.json()["kind"] == "otp" and r.json()["manual"] and not r.json()["enabled"]
    assert "check it by hand" in r.json()["advice"] or "by hand" in r.json()["advice"]
    manual = ctx.client.get("/api/portals").json()["manual"]
    assert [m["name"] for m in manual] == ["Railways IREPS"] and manual[0]["label"] == "OTP login needed"
    assert ctx.client.get("/api/settings").json()["portals"] == ["maha"]  # a portal the app cannot read is not run every day


def test_ten_portals_seven_readable_three_listed(ctx):
    bad = {"otp": "o", "login": "l", "captcha": "c"}
    for i in range(10):
        url = f"https://p{i}.gov.in/nicgep"
        if i < 3:
            kind = list(bad)[i]
            ctx.prober.answers[url] = {"kind": kind, "detail": bad[kind], "departments": 0, "tenders": 0}
        assert ctx.client.post("/api/portals", json={"name": f"Portal {i}", "base_url": url}).status_code == 200
    data = ctx.client.get("/api/portals").json()
    assert len(data["portals"]) == 11  # the built-in one plus ten
    assert sorted(m["name"] for m in data["manual"]) == ["Portal 0", "Portal 1", "Portal 2"]
    assert {m["kind"] for m in data["manual"]} == {"otp", "login", "captcha"}


def test_bad_or_duplicate_portals_are_refused_with_a_reason(ctx):
    assert ctx.client.post("/api/portals", json={"name": "Evil", "base_url": "https://evil.example.com"}).status_code == 422
    assert ctx.prober.calls == []  # never contacted
    assert ctx.client.post("/api/portals", json={"name": "Kerala", "base_url": GOOD}).status_code == 200
    again = ctx.client.post("/api/portals", json={"name": "Kerala two", "base_url": GOOD.upper().replace("HTTPS", "https")})
    assert again.status_code == 422 and "already" in again.json()["detail"]
    assert ctx.client.post("/api/portals", json={"name": "Kerala", "base_url": "https://other.gov.in/nicgep"}).status_code == 422


def test_check_again_updates_the_status(ctx):
    ctx.prober.answers[GOOD] = {"kind": "unreachable", "detail": "timed out", "departments": 0, "tenders": 0}
    ctx.client.post("/api/portals", json={"name": "Kerala", "base_url": GOOD})
    assert ctx.client.get("/api/portals").json()["manual"][0]["kind"] == "unreachable"
    del ctx.prober.answers[GOOD]
    r = ctx.client.post("/api/portals/kerala/check")
    assert r.json()["kind"] == "ok" and ctx.client.get("/api/portals").json()["manual"] == []
    assert ctx.client.post("/api/portals/nowhere/check").status_code == 404


def test_built_in_portals_can_be_checked_but_not_removed(ctx):
    assert ctx.client.post("/api/portals/maha/check").json()["custom"] is False
    r = ctx.client.delete("/api/portals/maha")
    assert r.status_code == 422 and "Built-in" in r.json()["detail"]
    assert ctx.client.delete("/api/portals/nowhere").status_code == 404


def test_removing_a_portal_forgets_it_everywhere(ctx):
    ctx.client.post("/api/portals", json={"name": "Kerala", "base_url": GOOD})
    assert ctx.client.delete("/api/portals/kerala").json() == {"removed": "kerala"}
    assert [p["key"] for p in ctx.client.get("/api/portals").json()["portals"]] == ["maha"]
    assert ctx.client.get("/api/settings").json()["portals"] == ["maha"]
    assert ctx.client.get("/api/settings").json()["custom_portals"] == []
    assert "kerala" not in ctx.store.portal_statuses()


def test_a_saved_portal_is_used_by_runs_and_survives_a_restart(ctx):
    ctx.client.post("/api/portals", json={"name": "Kerala", "base_url": GOOD})
    effective = appsettings.effective(ctx.env, ctx.store.get_settings())
    assert [p.key for p in effective.portals] == ["maha", "kerala"] and effective.portals[1].base_url == GOOD
    fresh = web.create_app(web.AppConfig(PASSWORD, "s3cret", cookie_secure=False, run_scheduler=False), ctx.env, ctx.store, prober=ctx.prober)
    c2 = TestClient(fresh)
    c2.post("/api/login", json={"password": PASSWORD})
    assert "kerala" in {p["key"] for p in c2.get("/api/portals").json()["portals"]}


# ---- runs record what the app could and could not read ----------------------------------------------------------

class WalledClient:
    search_url = "https://x/s"

    def __init__(self, kind=None):
        self.kind = kind

    def organisations(self):
        if self.kind:
            raise PortalError("walled", self.kind)
        return []


def test_a_run_records_the_reason_a_portal_could_not_be_read():
    store, now = SeenStore(":memory:"), runner.local_now()
    settings = env_settings()
    settings.portals = [Portal("a", "A", "A", "https://a.gov.in/n"), Portal("b", "B", "B", "https://b.gov.in/n")]
    clients = {"a": WalledClient("otp"), "b": WalledClient()}
    reports = collector.collect(settings, store, now, lambda p, s: clients[p.key])
    assert [(r.portal.key, r.access) for r in reports] == [("a", "otp"), ("b", "ok")]
    statuses = store.portal_statuses()
    assert statuses["a"]["kind"] == "otp" and statuses["b"]["kind"] == "ok"


def test_the_email_lists_portals_to_check_by_hand_but_not_mere_outages():
    now = runner.local_now()
    otp = collector.PortalReport(Portal("a", "Railways", "R", "https://a.gov.in"), errors=["could not read the portal: otp"], access="otp")
    down = collector.PortalReport(Portal("b", "Delhi", "D", "https://b.gov.in"), errors=["could not read the portal: timeout"], access="unreachable")
    built = digest.build(now, [], [], [otp, down])
    assert "Check these portals by hand" in built.html and "Railways" in built.html and "OTP login needed" in built.html
    assert "CHECK BY HAND: Railways" in built.text and "CHECK BY HAND: Delhi" not in built.text
