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
    settings = get_settings()
    remote = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not settings.trusted_proxy_ips:
        return remote
    if "any" not in settings.trusted_proxy_ips and remote not in settings.trusted_proxy_ips:
        return remote
    return forwarded.split(",")[0].strip() or remote


async def enforce_rate_limit(request: Request) -> None:
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

    limit, window_seconds = parse_rate_limit(settings.rate_limit_default)
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
