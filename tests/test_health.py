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
    def test_ready_when_database_is_reachable(self):
        def working_db():
            yield MagicMock()

        app.dependency_overrides[get_db] = working_db
        try:
            response = client.get("/health/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        finally:
            app.dependency_overrides.clear()

    def test_unavailable_when_database_is_unreachable(self):
        def broken_db():
            db = MagicMock()
            db.execute.side_effect = Exception("connection refused")
            yield db

        app.dependency_overrides[get_db] = broken_db
        try:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable"}
        finally:
            app.dependency_overrides.clear()

    def test_readiness_does_not_require_a_bearer_token(self):
        def working_db():
            yield MagicMock()

        app.dependency_overrides[get_db] = working_db
        try:
            response = client.get("/health/ready")
            assert response.status_code != 401
        finally:
            app.dependency_overrides.clear()
