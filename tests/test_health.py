from fastapi.testclient import TestClient

from app.core import db as db_module
from app.main import create_app


def test_liveness_is_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readiness_checks_database(client: TestClient) -> None:
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_readiness_reports_503_when_database_is_down(settings, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    def boom(_session):
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr("app.api.health.ping", boom)
    app = create_app(settings)
    r = TestClient(app).get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "database": "unavailable"}
    assert db_module  # module imported, engine untouched by this test
