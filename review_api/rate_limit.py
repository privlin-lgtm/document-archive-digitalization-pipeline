"""Redis-backed fixed-window rate limiter (see module history in git)."""

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
_circuit_open_until = 0.0


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(get_settings().rate_limit_redis_url, socket_connect_timeout=0.5)
    return _redis_client


def parse_rate_limit(spec: str) -> tuple[int, int]:
    count_str, _, unit = spec.partition("/")
    return int(count_str), _UNIT_SECONDS[unit.strip().lower()]


def client_ip(request: Request) -> str:
    """The real client IP for rate-limiting, trusting X-Forwarded-For only
    from a configured reverse proxy.

    A proxy that appends to (rather than replaces) X-Forwarded-For puts the
    real client at the *rightmost* position it added -- everything to its
    left is whatever the client itself sent, and is not trustworthy. Reading
    the leftmost entry instead (the previous behavior here) let any client
    pick its own apparent IP by sending a fabricated X-Forwarded-For header,
    which defeats per-IP rate limiting entirely -- confirmed live: distinct
    fake headers on successive requests each got their own limit bucket.
    """
    settings = get_settings()
    remote = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    trusted = settings.trusted_proxy_ips_list
    if not forwarded or not trusted:
        return remote
    if "any" not in trusted and remote not in trusted:
        return remote
    return forwarded.split(",")[-1].strip() or remote


async def _enforce(request: Request, limit_spec: str) -> None:
    global _circuit_open_until

    settings = get_settings()
    mutating = request.method in {"POST", "PATCH", "PUT", "DELETE"}
    fail_closed = bool(settings.rate_limit_fail_closed_mutating) and mutating

    if time.monotonic() < _circuit_open_until:
        if fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="rate limiter unavailable",
            )
        return

    limit, window_seconds = parse_rate_limit(limit_spec)
    window = int(time.time()) // window_seconds
    key = f"ratelimit:{client_ip(request)}:{request.url.path}:{window}"

    try:
        client = _get_redis_client()
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)
    except Exception:
        logger.warning(
            "rate limit check failed (Redis unreachable?)",
            exc_info=True,
        )
        _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS
        if fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="rate limiter unavailable",
            ) from None
        return

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit exceeded: {limit} requests per {window_seconds}s",
        )


async def enforce_rate_limit(request: Request) -> None:
    await _enforce(request, get_settings().rate_limit_default)


async def enforce_login_rate_limit(request: Request) -> None:
    """A stricter, dedicated limit for /auth/login: the general per-route
    limit (60/minute by default) is generous enough that it barely slows
    down credential guessing against the single shared review_api_token --
    login needs its own tighter bucket regardless of how the general limit
    is tuned. Keyed by the same (client_ip, path, window) scheme, so it
    doesn't share a bucket -- or a rate -- with any other route.
    """
    await _enforce(request, get_settings().rate_limit_login)
