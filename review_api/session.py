"""HMAC-signed HttpOnly session cookies. The browser never holds the API token."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from config import get_settings

COOKIE_NAME = "archive_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


class SessionError(ValueError):
    pass


def _secret() -> bytes:
    return get_settings().review_api_token.encode()


def issue_session(reviewer: str) -> str:
    payload = {"reviewer": reviewer, "exp": int(time.time()) + SESSION_TTL_SECONDS}
    body = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def decode_session(token: str) -> tuple[str, str, int]:
    """Verify `token` and return (reviewer, signature, exp).

    The signature is exposed so callers can key a revocation denylist off it
    (see review_api.session_revocation) -- it's already the unique,
    unforgeable part of the token, so there's no need for a separate session
    ID just to support revocation.
    """
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError as exc:
        raise SessionError("malformed session") from exc
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise SessionError("invalid session signature")
    payload = json.loads(urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    exp = int(payload.get("exp", 0))
    if exp < time.time():
        raise SessionError("session expired")
    reviewer = str(payload.get("reviewer") or "").strip()
    if not reviewer:
        raise SessionError("session missing reviewer")
    return reviewer, sig, exp


def read_session(token: str) -> str:
    reviewer, _sig, _exp = decode_session(token)
    return reviewer
