import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_settings
from review_api.principal import AuthPrincipal
from review_api.session import COOKIE_NAME, SessionError, read_session

_bearer_scheme = HTTPBearer(auto_error=False)


def require_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthPrincipal:
    """Accept a session cookie (browser) or a bearer token (CLI/workers).

    Session identity is the reviewer recorded at login. Bearer callers are
    attributed as `service-account` so the client cannot spoof reviewer.
    """
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        try:
            return AuthPrincipal(reviewer=read_session(cookie), via="session")
        except SessionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired session",
            ) from None

    settings = get_settings()
    if credentials is not None and secrets.compare_digest(
        credentials.credentials, settings.review_api_token
    ):
        return AuthPrincipal(reviewer="service-account", via="bearer")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
