from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings


def test_security_headers_on_pages(client: TestClient) -> None:
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in r.headers  # only behind HTTPS in staging/production


def test_unknown_host_is_rejected(client: TestClient) -> None:
    r = client.get("/health", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_database_url_accepts_host_style_urls(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example:5432/app")
    s = Settings()  # type: ignore[call-arg]
    assert s.database_url.unicode_string().startswith("postgresql+psycopg://")


def test_short_secret_key_is_refused(monkeypatch) -> None:
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_api_docs_hidden_in_production(monkeypatch) -> None:
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    s = Settings()  # type: ignore[call-arg]
    c = TestClient(create_app(s))
    assert c.get("/docs").status_code == 404
    assert c.get("/openapi.json").status_code == 404


def test_render_hostname_is_trusted(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "master-com-staging.onrender.com")
    s = Settings()  # type: ignore[call-arg]
    assert "master-com-staging.onrender.com" in s.allowed_hosts


def test_form_action_allows_only_us_and_google(client: TestClient) -> None:
    # "Link Google" posts a form that redirects to Google; without this the browser blocks it.
    csp = client.get("/").headers["content-security-policy"]
    assert "form-action 'self' https://accounts.google.com;" in csp + ";"


def test_routes_commit_before_the_response_is_sent() -> None:
    # With the default scope FastAPI commits after the response: a session cookie could reach the
    # browser before its row exists. Every get_db dependency must be function-scoped (DbSession).
    from fastapi.routing import APIRoute

    from app.core.db import get_db
    from app.main import create_app

    def routes(items):  # type: ignore[no-untyped-def]
        for item in items:
            if isinstance(item, APIRoute):
                yield item
            elif hasattr(item, "original_router"):
                yield from routes(item.original_router.routes)

    def dependencies(dependant):  # type: ignore[no-untyped-def]
        for dep in dependant.dependencies:
            yield dep
            yield from dependencies(dep)

    found = 0
    for route in routes(create_app(get_settings()).routes):
        for dep in dependencies(route.dependant):
            if dep.call is get_db:
                found += 1
                assert dep.scope == "function", f"{route.path} uses get_db with scope {dep.scope}"
    assert found >= 10
