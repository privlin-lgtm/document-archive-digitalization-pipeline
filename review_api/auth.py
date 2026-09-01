import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Bearer-token auth dependency for routes that expose document data.

    Applied to the documents router only — /health stays open so container
    healthchecks/load balancers don't need a credential.
    """
    settings = get_settings()
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, settings.review_api_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
