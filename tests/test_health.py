from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from review_api.main import app
from storage.db import get_db

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class TestReadiness:
    def _mock_redis(self, monkeypatch, *, ok: bool = True):
        fake = MagicMock()
        if ok:
            fake.ping.return_value = True
        else:
            fake.ping.side_effect = Exception("redis refused")
        monkeypatch.setattr("review_api.health.redis.from_url", lambda *a, **k: fake)

    def test_ready_when_database_and_redis_are_reachable(self, monkeypatch):
        def working_db():
            yield MagicMock()

        self._mock_redis(monkeypatch, ok=True)
        app.dependency_overrides[get_db] = working_db
        try:
            response = client.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"
            assert response.json()["checks"] == {"database": True, "redis": True}
        finally:
            app.dependency_overrides.clear()

    def test_unavailable_when_database_is_unreachable(self, monkeypatch):
        def broken_db():
            db = MagicMock()
            db.execute.side_effect = Exception("connection refused")
            yield db

        self._mock_redis(monkeypatch, ok=True)
        app.dependency_overrides[get_db] = broken_db
        try:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["status"] == "unavailable"
            assert response.json()["checks"]["database"] is False
        finally:
            app.dependency_overrides.clear()

    def test_unavailable_when_redis_is_unreachable(self, monkeypatch):
        def working_db():
            yield MagicMock()

        self._mock_redis(monkeypatch, ok=False)
        app.dependency_overrides[get_db] = working_db
        try:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["checks"]["redis"] is False
        finally:
            app.dependency_overrides.clear()

    def test_readiness_does_not_require_a_bearer_token(self, monkeypatch):
        def working_db():
            yield MagicMock()

        self._mock_redis(monkeypatch, ok=True)
        app.dependency_overrides[get_db] = working_db
        try:
            response = client.get("/health/ready")
            assert response.status_code != 401
        finally:
            app.dependency_overrides.clear()
