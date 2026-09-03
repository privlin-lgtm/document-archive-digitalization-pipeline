"""review_api.rate_limit tests.

TestEnforceRateLimit (unit) mocks Redis entirely, so it always runs.
TestRateLimitingIntegration needs a live Redis reachable at
RATE_LIMIT_REDIS_URL -- skipped automatically when it isn't, same
convention as test_queries.py's Postgres-gated tests (most dev/CI
environments won't have one up). To actually run it: `docker compose up -d
redis`, then run pytest with RATE_LIMIT_REDIS_URL pointed at a reachable
Redis (e.g. temporarily publish redis's port, or run inside the compose
network).
"""

import time
from unittest.mock import MagicMock

import pytest
import redis
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from config import get_settings
from review_api import rate_limit

settings = get_settings()


def _make_request(
    path: str = "/documents",
    client_host: str = "1.2.3.4",
    forwarded_for: str | None = None,
) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


class TestClientIp:
    """A reverse proxy appends the real client IP to any X-Forwarded-For the
    client already sent, so the trustworthy value is the *last* hop, not the
    first -- reading the first let a client pick its own apparent IP by
    sending a fabricated header, defeating per-IP rate limiting entirely.
    """

    def test_untrusted_by_default_ignores_forwarded_header(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", "")
        request = _make_request(client_host="1.2.3.4", forwarded_for="9.9.9.9")
        assert rate_limit.client_ip(request) == "1.2.3.4"

    def test_trusted_proxy_uses_the_last_hop_not_the_first(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.5")
        # A client-forged leading entry followed by the trusted proxy's own
        # appended (real) address.
        request = _make_request(client_host="10.0.0.5", forwarded_for="9.9.9.9, 203.0.113.7")
        assert rate_limit.client_ip(request) == "203.0.113.7"

    def test_untrusted_remote_is_not_fooled_by_forwarded_header(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.5")
        # Request didn't come from the trusted proxy -- ignore the header
        # even though one is present, whatever it claims.
        request = _make_request(client_host="6.6.6.6", forwarded_for="9.9.9.9")
        assert rate_limit.client_ip(request) == "6.6.6.6"


class TestParseRateLimit:
    def test_parses_count_and_unit(self):
        assert rate_limit.parse_rate_limit("60/minute") == (60, 60)
        assert rate_limit.parse_rate_limit("20/second") == (20, 1)
        assert rate_limit.parse_rate_limit("5/hour") == (5, 3600)
        assert rate_limit.parse_rate_limit("100/day") == (100, 86400)


class TestEnforceRateLimit:
    @pytest.fixture(autouse=True)
    def _reset_module_state(self, monkeypatch):
        monkeypatch.setattr(rate_limit, "_redis_client", None)
        monkeypatch.setattr(rate_limit, "_circuit_open_until", 0.0)

    async def test_allows_requests_under_the_limit(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.incr.return_value = 1
        monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: fake_redis)

        await rate_limit.enforce_rate_limit(_make_request())  # must not raise

        fake_redis.expire.assert_called_once()  # first hit in the window -> TTL set

    async def test_rejects_requests_over_the_limit(self, monkeypatch):
        fake_redis = MagicMock()
        limit, _ = rate_limit.parse_rate_limit(settings.rate_limit_default)
        fake_redis.incr.return_value = limit + 1
        monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: fake_redis)

        with pytest.raises(HTTPException) as exc_info:
            await rate_limit.enforce_rate_limit(_make_request())
        assert exc_info.value.status_code == 429

    async def test_does_not_reset_ttl_on_every_hit(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.incr.return_value = 2  # not the first hit in this window
        monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: fake_redis)

        await rate_limit.enforce_rate_limit(_make_request())

        fake_redis.expire.assert_not_called()

    async def test_fails_open_when_redis_is_unreachable(self, monkeypatch):
        def broken():
            raise redis.exceptions.ConnectionError("refused")

        monkeypatch.setattr(rate_limit, "_get_redis_client", broken)

        await rate_limit.enforce_rate_limit(_make_request())  # GET still fails open

    async def test_mutating_requests_fail_closed_when_configured(self, monkeypatch):
        def broken():
            raise redis.exceptions.ConnectionError("refused")

        monkeypatch.setattr(rate_limit, "_get_redis_client", broken)
        monkeypatch.setattr(settings, "rate_limit_fail_closed_mutating", True)

        request = _make_request()
        request.scope["method"] = "POST"
        with pytest.raises(HTTPException) as exc_info:
            await rate_limit.enforce_rate_limit(request)
        assert exc_info.value.status_code == 503

    async def test_a_redis_failure_opens_the_circuit_so_the_next_call_skips_the_connection_attempt(
        self, monkeypatch
    ):
        """The naive fail-open (try/except on every call) pays a full
        connection-timeout on *every* request while Redis is down -- a
        latency cliff, not graceful degradation (empirically: the test
        suite went 15s -> 110s under exactly this condition). One failure
        must open the circuit so subsequent calls don't even attempt a
        connection until the cooldown elapses.
        """
        call_count = 0

        def broken():
            nonlocal call_count
            call_count += 1
            raise redis.exceptions.ConnectionError("refused")

        monkeypatch.setattr(rate_limit, "_get_redis_client", broken)

        await rate_limit.enforce_rate_limit(_make_request())  # opens the circuit
        assert call_count == 1

        await rate_limit.enforce_rate_limit(_make_request())  # circuit open -> skipped entirely
        assert call_count == 1

    async def test_the_circuit_closes_again_after_the_cooldown(self, monkeypatch):
        monkeypatch.setattr(rate_limit, "_circuit_open_until", 0.0)
        fake_redis = MagicMock()
        fake_redis.incr.return_value = 1
        monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: fake_redis)

        # Simulate "the cooldown already elapsed" by opening the circuit in
        # the past rather than actually sleeping in a test.
        monkeypatch.setattr(rate_limit, "_circuit_open_until", time.monotonic() - 1)

        await rate_limit.enforce_rate_limit(_make_request())

        fake_redis.incr.assert_called_once()  # circuit closed -> Redis was actually checked

    async def test_different_paths_use_independent_buckets(self, monkeypatch):
        counts: dict[str, int] = {}

        class FakeRedis:
            def incr(self, key):
                counts[key] = counts.get(key, 0) + 1
                return counts[key]

            def expire(self, key, seconds):
                pass

        monkeypatch.setattr(rate_limit, "_get_redis_client", FakeRedis)

        limit, _ = rate_limit.parse_rate_limit(settings.rate_limit_default)
        for _ in range(limit):
            await rate_limit.enforce_rate_limit(_make_request(path="/documents"))

        # /documents is now exactly at its limit; a different path for the
        # same client must not be affected.
        await rate_limit.enforce_rate_limit(_make_request(path="/search"))  # must not raise


def _redis_client() -> redis.Redis | None:
    try:
        client = redis.from_url(settings.rate_limit_redis_url, socket_connect_timeout=1)
        client.ping()
        return client
    except Exception:  # noqa: BLE001 - any connectivity failure means "skip", not "fail"
        return None


_live_client = _redis_client()

_integration_skip = pytest.mark.skipif(
    _live_client is None, reason="requires a live Redis at RATE_LIMIT_REDIS_URL"
)


@_integration_skip
class TestRateLimitingIntegration:
    @pytest.fixture(autouse=True)
    def _clean_rate_limit_bucket(self):
        _live_client.flushdb()
        yield
        _live_client.flushdb()

    def test_exceeding_the_default_limit_returns_429(self):
        from review_api.main import app

        api_client = TestClient(app)
        limit = int(settings.rate_limit_default.split("/")[0])

        # Unauthenticated requests still count -- enforce_rate_limit is
        # listed before require_api_token in each router's dependencies
        # (deliberately: rate-limiting should gate *before* spending effort
        # validating credentials, e.g. against a brute-force/credential-
        # guessing client), and FastAPI stops at the first dependency that
        # raises, so this doesn't need a valid token or real DB access to
        # prove the limit itself works.
        statuses = [api_client.get("/documents").status_code for _ in range(limit + 10)]

        assert 429 in statuses

    def test_health_is_not_rate_limited(self):
        from review_api.main import app

        api_client = TestClient(app)
        limit = int(settings.rate_limit_default.split("/")[0])

        statuses = [api_client.get("/health").status_code for _ in range(limit + 10)]

        assert all(status == 200 for status in statuses)
