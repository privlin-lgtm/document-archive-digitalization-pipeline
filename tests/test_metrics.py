"""review_api.metrics tests.

TestMetricsAuth needs no Redis -- it only checks the route is gated.
TestMetricsCounters needs a live Redis reachable at RATE_LIMIT_REDIS_URL
(same convention as test_rate_limit.py's integration class) since the
counters are Redis-backed specifically so they aggregate across processes;
skipped automatically when one isn't reachable.
"""

import pytest
import redis
from fastapi.testclient import TestClient

from config import get_settings
from review_api import metrics
from review_api.main import app

settings = get_settings()


class TestMetricsAuth:
    def test_unauthenticated_request_is_rejected(self):
        client = TestClient(app)
        response = client.get("/metrics")
        assert response.status_code == 401

    def test_valid_bearer_token_is_accepted(self):
        client = TestClient(app)
        response = client.get(
            "/metrics", headers={"Authorization": f"Bearer {settings.review_api_token}"}
        )
        assert response.status_code == 200


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
class TestMetricsCounters:
    @pytest.fixture(autouse=True)
    def _clean_metrics_keys(self):
        for key in _live_client.scan_iter(match=f"{metrics._KEY_PREFIX}*"):
            _live_client.delete(key)
        yield
        for key in _live_client.scan_iter(match=f"{metrics._KEY_PREFIX}*"):
            _live_client.delete(key)

    def test_inc_is_visible_through_a_second_client(self):
        """The whole point of moving off collections.Counter: a count from
        one "process" (here, a second redis.Redis connection standing in for
        a second worker) must be visible to another, not stuck in
        process-local memory.
        """
        metrics.inc("http_200", 3)
        metrics.inc("http_200")

        assert int(_live_client.get(f"{metrics._KEY_PREFIX}http_200")) == 4

    def test_metrics_endpoint_reflects_redis_state(self):
        client = TestClient(app)
        metrics.inc("pipeline_complete", 2)

        response = client.get(
            "/metrics", headers={"Authorization": f"Bearer {settings.review_api_token}"}
        )

        assert response.status_code == 200
        assert response.json()["pipeline_complete"] == 2
