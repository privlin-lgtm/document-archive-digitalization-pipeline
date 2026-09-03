import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from config import get_settings
from review_api.auth import require_api_token
from review_api.principal import AuthPrincipal
from review_api.rate_limit import enforce_login_rate_limit, enforce_rate_limit
from review_api.session import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    SessionError,
    decode_session,
    issue_session,
)
from review_api.session_revocation import revoke

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)


class SessionOut(BaseModel):
    reviewer: str


@router.post("/login", response_model=SessionOut, dependencies=[Depends(enforce_login_rate_limit)])
def login(body: LoginRequest, response: Response) -> SessionOut:
    """Exchange the shared API token for an HttpOnly session cookie.

    The token is typed once at login and never stored in JavaScript.
    """
    settings = get_settings()
    if not secrets.compare_digest(body.password, settings.review_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    reviewer = body.reviewer.strip()
    if not reviewer:
        raise HTTPException(status_code=400, detail="reviewer is required")
    response.set_cookie(
        key=COOKIE_NAME,
        value=issue_session(reviewer),
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )
    return SessionOut(reviewer=reviewer)


@router.post("/logout", status_code=204, dependencies=[Depends(enforce_rate_limit)])
def logout(request: Request, response: Response) -> None:
    """Clears the browser's cookie *and* revokes the token server-side, so a
    copy of the cookie made before logout (a shared machine, a proxy log,
    another tab that already had it) can't keep working for the rest of its
    12h TTL -- deleting only the client's copy, the previous behavior here,
    left the token itself still valid to anyone who held it.
    """
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        try:
            _reviewer, signature, exp = decode_session(cookie)
            revoke(signature, ttl_seconds=exp - int(time.time()))
        except SessionError:
            pass  # already invalid/expired -- nothing to revoke
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=SessionOut, dependencies=[Depends(enforce_rate_limit)])
def me(principal: AuthPrincipal = Depends(require_api_token)) -> SessionOut:
    return SessionOut(reviewer=principal.reviewer)
