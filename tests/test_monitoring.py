"""Sentry error monitoring (PRODUCT_RULES A3, R-024): off without a DSN, no personal data sent."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from app.core import monitoring
from app.core.config import Settings
from app.main import create_app

DSN = "https://publickey123@o1.ingest.de.sentry.io/4242"
EMAIL = "someone.private@example.com"
PHONE = "+992 93 123 45 67"
TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE"  # 43 characters, like token_urlsafe(32)
BOT_TOKEN = "123456789:AAH-bot-token-value-xyz"
SECRET_KEY = "test-secret-key-that-is-long-enough-123456"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "staging",
        "secret_key": SECRET_KEY,
        "database_url": "postgresql+psycopg://mc:dbpass-secret@localhost:5432/mastercom_test",
        "telegram_bot_token": BOT_TOKEN,
        "uploads_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def reset_monitoring(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monitoring._reset_for_tests()
    yield
    sentry_sdk.get_client().close()
    sentry_sdk.init(dsn=None)  # back to the inactive client so nothing leaks into other tests
    monitoring._reset_for_tests()


@pytest.fixture
def recorded(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(monitoring.sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    return calls


# --- settings and init --------------------------------------------------------------------


def test_blank_dsn_is_none() -> None:
    assert make_settings(sentry_dsn="  ").sentry_dsn is None
    assert make_settings().sentry_dsn is None


def test_release_falls_back_to_render_commit(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def")
    assert make_settings().sentry_release == "abc123def"
    assert make_settings(sentry_release="v1").sentry_release == "v1"


def test_init_without_dsn_is_off(recorded) -> None:
    assert monitoring.init_sentry(make_settings(), component="web") is False
    assert recorded == []


def test_init_with_dsn(recorded) -> None:
    settings = make_settings(sentry_dsn=DSN, sentry_release="rel-1")
    assert monitoring.init_sentry(settings, component="bot") is True
    assert len(recorded) == 1
    kwargs = recorded[0]
    assert kwargs["dsn"] == DSN
    assert kwargs["send_default_pii"] is False
    assert kwargs["traces_sample_rate"] == 0.0
    assert kwargs["profiles_sample_rate"] == 0.0
    assert kwargs["include_local_variables"] is False
    assert kwargs["max_breadcrumbs"] == 30
    assert kwargs["environment"] == "staging"
    assert kwargs["release"] == "rel-1"
    assert kwargs["server_name"] == "bot"
    assert kwargs["before_send"] is monitoring.scrub
    assert kwargs["before_breadcrumb"] is monitoring.scrub_breadcrumb


def test_init_is_idempotent(recorded) -> None:
    settings = make_settings(sentry_dsn=DSN)
    assert monitoring.init_sentry(settings, component="web") is True
    assert monitoring.init_sentry(settings, component="web") is True
    assert len(recorded) == 1


def test_create_app_initialises_web(monkeypatch, recorded) -> None:
    create_app(make_settings(sentry_dsn=DSN, app_env="test"))
    assert [c["server_name"] for c in recorded] == ["web"]


# --- scrub ---------------------------------------------------------------------------------


def crafted_event() -> dict[str, Any]:
    return {
        "request": {
            "method": "POST",
            "url": f"https://site.example/auth/telegram/{TOKEN}?next=/x&token={TOKEN}",
            "query_string": f"token={TOKEN}&email={EMAIL}",
            "data": {"email": EMAIL, "password": "hunter2-password"},
            "cookies": {"session": "cookie-session-value"},
            "headers": {"Authorization": "Bearer header-secret-value", "X-Phone": PHONE},
            "env": {"REMOTE_ADDR": "203.0.113.9"},
        },
        "user": {"id": 7, "email": EMAIL, "ip_address": "203.0.113.9"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": (
                        f"bad {EMAIL} phone {PHONE} token {TOKEN} bot {BOT_TOKEN} key {SECRET_KEY}"
                    ),
                }
            ]
        },
        "logentry": {"message": f"failed for {EMAIL}", "params": [PHONE], "formatted": PHONE},
        "message": f"plain {EMAIL}",
        "breadcrumbs": {
            "values": [
                {"category": "app", "message": f"user {EMAIL} signed in"},
                {"type": "http", "data": {"url": f"https://api.example/x?token={TOKEN}"}},
            ]
        },
        "extra": {"form": EMAIL},
        "contexts": {
            "runtime": {"name": "CPython"},
            "os": {"name": "Linux"},
            "trace": {"trace_id": "0" * 8},
            "response": {"body": EMAIL},
        },
    }


@pytest.fixture
def configured() -> Settings:
    settings = make_settings(sentry_dsn=DSN, google_client_secret="google-client-secret-value")
    monitoring.sentry_options(settings, component="web")
    return settings


def test_scrub_removes_personal_data_and_secrets(configured) -> None:
    result = monitoring.scrub(crafted_event(), {})
    assert result is not None
    dumped = json.dumps(result)
    for needle in (
        EMAIL,
        PHONE,
        "93 123 45 67",
        TOKEN,
        BOT_TOKEN,
        SECRET_KEY,
        DSN,
        "publickey123",
        "google-client-secret-value",
        "hunter2-password",
        "cookie-session-value",
        "header-secret-value",
        "203.0.113.9",
        "next=",
    ):
        assert needle not in dumped, needle
    assert "user" not in result
    assert "extra" not in result
    assert set(result["contexts"]) == {"runtime", "os", "trace"}
    assert result["request"] == {"method": "POST", "url": "/auth/telegram/[redacted]"}


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"request": None, "user": None, "exception": None, "breadcrumbs": None},
        {"request": "oops", "exception": {"values": "nope"}, "logentry": 5},
        {"exception": {"values": [None, {"value": 12}]}, "breadcrumbs": {"values": [None, 3]}},
        {"request": {"url": 42, "method": None}, "contexts": [1, 2], "extra": "x"},
    ],
)
def test_scrub_never_raises(configured, event) -> None:
    result = monitoring.scrub(event, {})
    assert result is None or isinstance(result, dict)
    if result is not None:
        assert "user" not in result


def test_scrub_falls_back_on_internal_error(configured, monkeypatch) -> None:
    def boom(_value: str) -> str:
        raise RuntimeError("bug")

    monkeypatch.setattr(monitoring, "redact", boom)
    result = monitoring.scrub(crafted_event(), {})
    assert result is not None
    assert "request" not in result and "user" not in result and "extra" not in result


def test_scrub_breadcrumb(configured) -> None:
    crumb = {
        "type": "http",
        "category": "httplib",
        "data": {"url": f"https://x.example/a?t={TOKEN}"},
    }
    result = monitoring.scrub_breadcrumb(crumb, {})
    assert result is not None
    assert result["data"]["url"] == "https://x.example/a"
    assert (
        monitoring.scrub_breadcrumb({"category": "botocore.endpoint", "message": "x"}, {}) is None
    )
    assert monitoring.scrub_breadcrumb({"category": "urllib3.connectionpool"}, {}) is None
    logged = monitoring.scrub_breadcrumb({"category": "app", "message": f"hi {EMAIL}"}, {})
    assert logged is not None and EMAIL not in json.dumps(logged)


def test_secret_settings_are_collected(configured) -> None:
    secrets = monitoring._secret_values(configured)
    assert BOT_TOKEN in secrets and SECRET_KEY in secrets and DSN in secrets
    assert "google-client-secret-value" in secrets and "dbpass-secret" in secrets


# --- end to end through the web app ----------------------------------------------------------


class CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope) -> None:  # type: ignore[no-untyped-def]
        for item in envelope.items:
            if item.headers.get("type") in ("event", "transaction"):
                self.events.append(item.payload.json)


@pytest.fixture
def sentry_client(settings: Settings) -> Iterator[tuple[TestClient, CapturingTransport]]:
    transport = CapturingTransport()
    options = monitoring.sentry_options(settings, component="web")
    options["dsn"] = "https://public@example.invalid/1"
    sentry_sdk.init(**options, transport=transport)
    app = create_app(settings)
    router = APIRouter()

    @router.get("/__boom")
    def boom() -> None:
        raise RuntimeError(f"exploded for {EMAIL}")

    app.include_router(router)
    client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    yield client, transport
    sentry_sdk.flush()


def test_unhandled_error_is_reported_without_personal_data(sentry_client) -> None:
    client, transport = sentry_client
    client.cookies.set("sid", "cookie-session-value")
    response = client.get("/__boom?email=x")
    assert response.status_code == 500
    sentry_sdk.flush()
    errors = [e for e in transport.events if e.get("exception")]
    assert len(errors) == 1
    dumped = json.dumps(errors[0])
    assert "RuntimeError" in dumped
    assert EMAIL not in dumped and "cookie-session-value" not in dumped and "email=x" not in dumped
    assert errors[0]["tags"]["component"] == "web"
    assert errors[0]["server_name"] == "web"


def test_404_and_csrf_are_not_reported(sentry_client) -> None:
    client, transport = sentry_client
    assert client.get("/no-such-page").status_code == 404
    assert client.post("/signout").status_code == 403
    sentry_sdk.flush()
    assert transport.events == []
