"""Cookie flags on the HTTPS path (staging and production), which the flow tests never hit."""

import pytest
from fastapi import Response

from app.core.config import Settings
from app.web.cookies import CSRF, OAUTH, SESSION, delete_cookie, set_cookie


def _settings(monkeypatch, env: str) -> Settings:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_ENV", env)
    return Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("base", [SESSION, CSRF, OAUTH])
@pytest.mark.parametrize("env", ["staging", "production"])
def test_https_cookies_use_the_host_prefix_and_secure(monkeypatch, env: str, base: str) -> None:
    response = Response()
    set_cookie(response, base, "value", _settings(monkeypatch, env), max_age=60)
    header = response.headers["set-cookie"]
    assert header.startswith(f"__Host-{base}=value;")
    for flag in ("HttpOnly", "Path=/", "SameSite=lax", "Secure"):
        assert flag in header
    # __Host- cookies must not carry a Domain, or browsers refuse them.
    assert "Domain" not in header


@pytest.mark.parametrize("base", [SESSION, CSRF, OAUTH])
def test_development_cookies_drop_the_prefix_and_secure(monkeypatch, base: str) -> None:
    response = Response()
    set_cookie(response, base, "value", _settings(monkeypatch, "development"), max_age=60)
    header = response.headers["set-cookie"]
    assert header.startswith(f"{base}=value;")
    assert "Secure" not in header
    assert "HttpOnly" in header and "SameSite=lax" in header


def test_sign_out_deletes_the_prefixed_cookie_on_https(monkeypatch) -> None:
    response = Response()
    delete_cookie(response, SESSION, _settings(monkeypatch, "staging"))
    header = response.headers["set-cookie"]
    assert header.startswith("__Host-mc_session=")
    assert "Max-Age=0" in header and "Secure" in header
