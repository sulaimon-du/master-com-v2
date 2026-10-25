import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# Test defaults; CI and local runs can override DATABASE_URL.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-123456")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://mc:mc@localhost:5432/mastercom_test")
# Process env wins over a developer's .env, so a local .env cannot drop the test host.
os.environ.setdefault("ALLOWED_HOSTS", '["testserver", "localhost", "127.0.0.1"]')

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.main import create_app
from app.services.email import OutgoingEmail, get_email_sender

ALEMBIC_INI = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


class RecordingSender:
    """Keeps sent emails in memory so tests can open the links they contain."""

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    def send(self, message: OutgoingEmail) -> None:
        self.sent.append(message)


@pytest.fixture
def outbox() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def client(settings: Settings, outbox: RecordingSender) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_email_sender] = lambda: outbox
    # Redirects are asserted on directly (status, Location, Set-Cookie).
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def clean_auth(migrated_engine) -> None:
    """Start from empty account tables. Routes commit for real, so tests cannot roll back."""
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE auth_sessions, auth_tokens, oauth_flows, rate_limits, users "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(scope="session")
def migrated_engine():
    """The test database at the latest migration (the schema the app really runs on)."""
    command.upgrade(Config(ALEMBIC_INI), "head")
    return get_engine()


@pytest.fixture
def db(migrated_engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test; commits become savepoints."""
    with migrated_engine.connect() as conn:
        outer = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            outer.rollback()
