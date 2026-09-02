import pytest
from fastapi.testclient import TestClient

from config import get_settings
from review_api.main import app
from review_api.session import SessionError, issue_session, read_session


def test_session_round_trip():
    token = issue_session("paul@archive")
    assert read_session(token) == "paul@archive"


def test_tampered_session_is_rejected():
    token = issue_session("paul@archive")
    with pytest.raises(SessionError):
        read_session(token[:-1] + ("0" if token[-1] != "0" else "1"))


def test_login_sets_httponly_cookie():
    client = TestClient(app)
    response = client.post(
        "/auth/login",
        json={"reviewer": "paul@archive", "password": get_settings().review_api_token},
    )
    assert response.status_code == 200
    assert response.json() == {"reviewer": "paul@archive"}
    assert "archive_session" in response.cookies
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {"reviewer": "paul@archive"}


def test_login_rejects_wrong_password():
    client = TestClient(app)
    response = client.post("/auth/login", json={"reviewer": "paul", "password": "nope"})
    assert response.status_code == 401


def test_logout_clears_session():
    client = TestClient(app)
    client.post(
        "/auth/login",
        json={"reviewer": "paul@archive", "password": get_settings().review_api_token},
    )
    response = client.post("/auth/logout")
    assert response.status_code == 204
    assert client.get("/auth/me").status_code == 401
