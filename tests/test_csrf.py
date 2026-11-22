import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.web.csrf import verify_csrf
from tests.support import make_user

pytestmark = pytest.mark.usefixtures("clean_auth")


def test_sign_in_without_csrf_token_is_refused(client: TestClient) -> None:
    make_user()
    r = client.post("/signin", data={"email": "ana@example.com", "password": "correct horse 9"})
    assert r.status_code == 403
    assert "Обновите страницу" in r.text
    assert "mc_session" not in r.headers.get("set-cookie", "")


def test_wrong_csrf_token_is_refused(client: TestClient) -> None:
    make_user()
    client.get("/signin")
    r = client.post(
        "/signin",
        data={"email": "ana@example.com", "password": "correct horse 9", "csrf_token": "nope"},
    )
    assert r.status_code == 403


def test_token_from_another_visitor_is_refused(client: TestClient) -> None:
    make_user()
    other = TestClient(client.app, follow_redirects=False)
    other.get("/signin")
    client.get("/signin")
    r = client.post(
        "/signin",
        data={
            "email": "ana@example.com",
            "password": "correct horse 9",
            "csrf_token": other.cookies.get("mc_csrf"),
        },
    )
    assert r.status_code == 403


def test_htmx_header_token_is_accepted(client: TestClient) -> None:
    client.get("/signin")
    token = client.cookies.get("mc_csrf")
    r = client.post("/signout", headers={"X-CSRF-Token": token or ""})
    assert r.status_code == 303


def test_htmx_request_without_header_is_refused(client: TestClient) -> None:
    client.get("/signin")
    assert client.post("/signout").status_code == 403


def _api_routes(routes) -> list[APIRoute]:  # type: ignore[no-untyped-def]
    """All API routes, including those of included routers (FastAPI wraps them)."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(_api_routes(route.original_router.routes))
    return found


def test_every_post_route_checks_csrf() -> None:
    # A new POST route without the check fails here, before it reaches review.
    app = create_app(get_settings())
    posts = [route for route in _api_routes(app.routes) if "POST" in route.methods]
    assert len(posts) >= 9, "expected the nine auth POST routes"
    for route in posts:
        checks = [d.call for d in route.dependant.dependencies]
        assert verify_csrf in checks, f"{route.path} has no CSRF check"


@pytest.mark.parametrize("path", ["/register", "/password/forgot", "/confirm/resend"])
def test_each_form_is_protected(client: TestClient, path: str) -> None:
    r = client.post(path, data={"email": "ana@example.com"})
    assert r.status_code == 403
