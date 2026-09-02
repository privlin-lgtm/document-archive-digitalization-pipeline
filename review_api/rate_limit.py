"""Minimal Redis-backed fixed-window rate limiter, applied as a FastAPI
dependency on each protected router (see e.g. documents.py).

Written in-house rather than using a library (slowapi was tried first and
dropped): slowapi's ASGI middleware resolves each request's route handler by
manually re-matching against `app.routes` (its `_find_route_handler`), which
assumes routes are flat `Route`/`APIRoute` objects. The FastAPI version this
project is on wraps `include_router(...)` results in `_IncludedRouter`
objects instead, so that matcher never finds a handler for any application
route; slowapi's `_should_exempt` then treats an unresolved handler as
exempt, and every request silently skips rate limiting entirely. This was
caught empirically, not assumed -- a test against a live Redis showed zero
keys were ever written across 70 requests to a non-exempt route (see
tests/test_rate_limit.py). Separately, slowapi's `@limiter.limit(...)` route
decorator also crashes when combined with `swallow_errors=True` (a swallowed
storage exception still falls through to code reading
`request.state.view_rate_limit`, which the failed check never set). Given
two real, independently-discovered bugs in short order, a small in-house
implementation that only relies on FastAPI's normal (thoroughly-exercised
elsewhere in this codebase) dependency-injection system is more trustworthy
than continuing to debug a third-party ASGI-middleware integration.

Fixed-window counting via Redis INCR + EXPIRE: simple and atomic enough for
abuse protection (not a precise billing meter) -- the known tradeoff is a
client can get up to ~2x the nominal limit through by timing requests
across a window boundary, which is an accepted imprecision here.

A circuit breaker guards the fail-open path above: the naive version (just
try Redis, catch, log, continue) pays a full connection-attempt timeout on
*every single request* whenever Redis is down, which is a latency cliff for
the whole API, not a graceful degradation -- caught empirically, not
assumed (the test suite went from ~15s to ~110s when Redis was reachable
for one earlier run and then torn down without a breaker in place). Once a
failure is observed, checks are skipped outright (assumed allowed,
immediately, no connection attempt) for `_CIRCUIT_COOLDOWN_SECONDS` before
trying again.
"""

from __future__ import annotations

import logging
import time

import redis
from fastapi import HTTPException, Request, status

from config import get_settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

_CIRCUIT_COOLDOWN_SECONDS = 5.0
_circuit_open_until = 0.0  # monotonic timestamp; 0 = circuit closed (Redis assumed healthy)


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(get_settings().rate_limit_redis_url, socket_connect_timeout=0.5)
    return _redis_client


def parse_rate_limit(spec: str) -> tuple[int, int]:
    """"60/minute" -> (60, 60); "20/second" -> (20, 1)."""
    count_str, _, unit = spec.partition("/")
    return int(count_str), _UNIT_SECONDS[unit.strip().lower()]


async def enforce_rate_limit(request: Request) -> None:
    """Add `Depends(enforce_rate_limit)` to a router's `dependencies` to
    rate-limit every route on it, keyed by (client IP, path, current
    window). Different routes never share a bucket, so one endpoint being
    hit hard doesn't exhaust another's budget.

    Fails *open* on a Redis error -- logs a warning and lets the request
    through rather than raising, since a rate limiter that fails closed
    turns a Redis hiccup into a full API outage, a worse failure mode than
    briefly running unlimited.
    """
    global _circuit_open_until

    if time.monotonic() < _circuit_open_until:
        return

    settings = get_settings()
    limit, window_seconds = parse_rate_limit(settings.rate_limit_default)
    client_host = request.client.host if request.client else "unknown"
    window = int(time.time()) // window_seconds
    key = f"ratelimit:{client_host}:{request.url.path}:{window}"

    try:
        client = _get_redis_client()
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)
    except Exception:
        logger.warning("rate limit check failed (Redis unreachable?) -- allowing the request through", exc_info=True)
        _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS
        return

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit exceeded: {limit} requests per {window_seconds}s",
        )
