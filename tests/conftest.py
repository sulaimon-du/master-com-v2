import os

import pytest
from fastapi.testclient import TestClient

# Test defaults; CI and local runs can override DATABASE_URL.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-123456")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://mc:mc@localhost:5432/mastercom_test")

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
