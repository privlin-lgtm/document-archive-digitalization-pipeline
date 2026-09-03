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


def test_logout_revokes_the_token_not_just_the_client_cookie():
    """A copy of the cookie made before logout (a shared machine, another
    tab) must stop working immediately, not remain valid for the rest of
    its TTL -- deleting only the browser's copy doesn't achieve that.
    Skipped, not failed, without a reachable Redis: revocation degrades to
    "not revoked" when the denylist can't be checked (see
    review_api.session_revocation's module docstring for why that's the
    deliberate fail-open behavior, same as the rate limiter's).
    """
    import redis as redis_module

    try:
        redis_module.from_url(
            get_settings().rate_limit_redis_url, socket_connect_timeout=0.5
        ).ping()
    except Exception:  # noqa: BLE001 - any connectivity failure means "skip", not "fail"
        pytest.skip("requires a live Redis at RATE_LIMIT_REDIS_URL")

    client = TestClient(app)
    client.post(
        "/auth/login",
        json={"reviewer": "paul@archive", "password": get_settings().review_api_token},
    )
    old_cookie_value = client.cookies.get("archive_session")

    client.post("/auth/logout")

    # Simulate a second holder of the pre-logout cookie value.
    other_client = TestClient(app)
    other_client.cookies.set("archive_session", old_cookie_value)
    assert other_client.get("/auth/me").status_code == 401
