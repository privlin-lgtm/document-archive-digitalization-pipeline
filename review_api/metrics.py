"""Request/pipeline counters, backed by Redis so they aggregate correctly
across processes.

An in-process collections.Counter only ever sees the requests *that one
process* handled -- fine for a single Uvicorn worker, silently wrong (each
process reports a different partial view, and /metrics reflects whichever
process happened to answer) the moment this runs with more than one worker
or alongside the Celery worker process, which also calls inc() (see
pipeline.run). Redis INCR is atomic across processes for the same reason
the rate limiter already relies on it for the same property.
"""

import logging
from collections import Counter

import redis
from fastapi import APIRouter, Depends

from config import get_settings
from review_api.auth import require_api_token

logger = logging.getLogger(__name__)

# No rate-limit dependency here (unlike every other authenticated router):
# enforce_rate_limit itself calls inc() on every request, and gating the one
# endpoint that reads counters behind the same limiter it feeds is a
# needless edge case for a route with no side effects worth throttling.
router = APIRouter(tags=["metrics"], dependencies=[Depends(require_api_token)])

_KEY_PREFIX = "metric:"
_redis_client: redis.Redis | None = None

# Best-effort local fallback for whatever this process incremented while
# Redis was unreachable -- not merged into the Redis-backed total (that
# would double-count once Redis comes back), just so a metrics read never
# goes fully blank during an outage.
_fallback_counters: Counter[str] = Counter()


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(get_settings().rate_limit_redis_url, socket_connect_timeout=0.5)
    return _redis_client


def inc(name: str, n: int = 1) -> None:
    try:
        _get_redis_client().incrby(f"{_KEY_PREFIX}{name}", n)
    except Exception:
        logger.warning("failed to record metric %r (Redis unreachable?)", name, exc_info=True)
        _fallback_counters[name] += n


@router.get("/metrics")
def metrics() -> dict[str, int]:
    try:
        client = _get_redis_client()
        keys = list(client.scan_iter(match=f"{_KEY_PREFIX}*"))
        if not keys:
            return dict(_fallback_counters)
        values = client.mget(keys)
        return {
            key.decode().removeprefix(_KEY_PREFIX): int(value)
            for key, value in zip(keys, values, strict=True)
            if value is not None
        }
    except Exception:
        logger.warning("failed to read metrics (Redis unreachable?)", exc_info=True)
        return dict(_fallback_counters)
