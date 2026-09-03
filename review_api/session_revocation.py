"""Redis-backed denylist for revoked session tokens.

review_api.session issues stateless, HMAC-signed cookies: logging out can't
make the token itself stop verifying before its own TTL expires, since the
signature is still valid to whoever holds the cookie (or a copy of it).
This closes that gap without making sessions stateful in the general case --
only a *revoked* token needs a lookup, keyed by its own signature (already
unique and unforgeable, so no separate session ID is needed), with the
denylist entry's TTL capped at the token's own remaining lifetime so it
never outlives the session it revokes.

Best-effort by design: if Redis is unreachable, revoke() and is_revoked()
both degrade to "not revoked" -- a session that can't be double-checked
against a denylist still behaves exactly as it did before this module
existed (valid until its natural expiry), not a new failure mode.
"""

from __future__ import annotations

import logging

import redis

from config import get_settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(get_settings().rate_limit_redis_url, socket_connect_timeout=0.5)
    return _redis_client


def _key(signature: str) -> str:
    return f"session_revoked:{signature}"


def revoke(signature: str, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    try:
        _get_redis_client().set(_key(signature), "1", ex=ttl_seconds)
    except Exception:
        logger.warning("failed to record session revocation (Redis unreachable?)", exc_info=True)


def is_revoked(signature: str) -> bool:
    try:
        return bool(_get_redis_client().exists(_key(signature)))
    except Exception:
        logger.warning("failed to check session revocation (Redis unreachable?)", exc_info=True)
        return False
