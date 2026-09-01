import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import get_settings
from review_api.main import app
from storage.db import Base, get_db
from storage.models import Document, DocumentStatus


@pytest.fixture()
def api():
    """A TestClient wired to an isolated in-memory SQLite DB, so these tests
    don't need a live Postgres instance.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), TestSession
    finally:
        app.dependency_overrides.clear()


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().review_api_token}"}


class TestAuth:
    def test_list_documents_without_token_is_rejected(self, api):
        client, _ = api
        response = client.get("/documents")
        assert response.status_code == 401

    def test_list_documents_with_wrong_token_is_rejected(self, api):
        client, _ = api
        response = client.get("/documents", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401

    def test_list_documents_with_valid_token_succeeds(self, api):
        client, _ = api
        response = client.get("/documents", headers=auth_headers())
        assert response.status_code == 200
        assert response.json() == []

    def test_get_document_without_token_is_rejected(self, api):
        client, _ = api
        response = client.get("/documents/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 401

    def test_health_does_not_require_a_token(self, api):
        client, _ = api
        response = client.get("/health")
        assert response.status_code == 200


class TestPagination:
    def _seed(self, session_factory, count: int) -> None:
        session = session_factory()
        for i in range(count):
            session.add(
                Document(
                    filename=f"doc-{i}.png",
                    status=DocumentStatus.uploaded,
                    raw_image_path=f"/data/documents/doc-{i}.png",
                )
            )
        session.commit()
        session.close()

    def test_limit_restricts_page_size(self, api):
        client, TestSession = api
        self._seed(TestSession, 5)

        response = client.get("/documents", params={"limit": 2}, headers=auth_headers())

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_offset_skips_earlier_rows(self, api):
        client, TestSession = api
        self._seed(TestSession, 5)

        page1 = client.get("/documents", params={"limit": 2, "offset": 0}, headers=auth_headers()).json()
        page2 = client.get("/documents", params={"limit": 2, "offset": 2}, headers=auth_headers()).json()

        assert {d["id"] for d in page1}.isdisjoint({d["id"] for d in page2})

    def test_limit_above_max_is_rejected(self, api):
        client, _ = api
        response = client.get("/documents", params={"limit": 100_000}, headers=auth_headers())
        assert response.status_code == 422

    def test_default_page_size_is_bounded(self, api):
        client, TestSession = api
        self._seed(TestSession, 5)

        response = client.get("/documents", headers=auth_headers())

        assert response.status_code == 200
        assert len(response.json()) == 5  # under the default limit, all rows returned
