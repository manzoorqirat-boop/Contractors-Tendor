"""The routes live one module per area under tender_digest/routes. These guard the layout: nothing forgotten, nothing hidden by an earlier route."""
import collections
import pkgutil
import re
from pathlib import Path

from fastapi.testclient import TestClient

from tender_digest import routes, web
from tender_digest.routes import context
from tender_digest.runner import RunManager
from tender_digest.store import SeenStore
from tests.test_web import PASSWORD, FakeSender, settings


def make_app():
    env, store = settings(), SeenStore(":memory:")
    return web.create_app(web.AppConfig(PASSWORD, "k", cookie_secure=False, run_scheduler=False, username="admin"), env, store, RunManager(env, store, lambda p, s: None, FakeSender(), background=False))


def test_every_module_in_the_routes_package_is_registered_exactly_once():
    on_disk = {m.name for m in pkgutil.iter_modules(routes.__path__)} - {"context"}
    registered = [m.__name__.rsplit(".", 1)[-1] for m in routes.ORDER]
    assert sorted(registered) == sorted(on_disk), "a route module exists that register_all does not call (or the reverse)"
    assert len(registered) == len(set(registered))
    for m in routes.ORDER:
        assert callable(getattr(m, "register", None)), m.__name__


def test_no_route_is_added_twice_so_none_is_hidden_behind_another():
    app = make_app()
    seen = collections.Counter((method, r.path) for r in app.routes for method in (getattr(r, "methods", None) or {"MOUNT"}))
    assert {k: n for k, n in seen.items() if n > 1} == {}


def test_the_page_catch_all_is_added_after_every_api_route():
    app = make_app()
    paths = [r.path for r in app.routes]
    if "/{path:path}" in paths:
        assert paths.index("/{path:path}") > max(i for i, p in enumerate(paths) if p.startswith("/api/"))


def test_a_fixed_path_comes_before_a_path_with_a_parameter_that_could_match_it():
    app = make_app()
    gets = [r.path for r in app.routes if "GET" in (getattr(r, "methods", None) or ())]
    for fixed, param in (("/api/bids/stats", "/api/bids/{bid_id}"), ("/api/bids/alerts", "/api/bids/{bid_id}"), ("/api/bids/bulk", "/api/bids/{bid_id}"),
                         ("/api/portals/catalogue", "/api/portals/{key}/sample")):
        if fixed in gets and param in gets:
            assert gets.index(fixed) < gets.index(param), f"{fixed} would be swallowed by {param}"


def test_the_shared_context_holds_everything_the_modules_read_from_it():
    fields = set(context.Ctx.__dataclass_fields__)
    for m in routes.ORDER:
        used = set(re.findall(r"\bctx\.(\w+)", Path(m.__file__).read_text()))
        assert used <= fields, (m.__name__, sorted(used - fields))
    assert fields, "the context is empty"


def test_the_app_still_answers_and_a_signed_in_request_reaches_a_moved_route():
    app = make_app()
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/bids").status_code == 401                                    # protected, as before
    assert c.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    assert c.get("/api/bids").status_code == 200 and c.get("/api/money").status_code == 200 and c.get("/api/learning").status_code == 200
