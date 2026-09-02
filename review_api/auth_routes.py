import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from config import get_settings
from review_api.auth import require_api_token
from review_api.principal import AuthPrincipal
from review_api.rate_limit import enforce_rate_limit
from review_api.session import COOKIE_NAME, SESSION_TTL_SECONDS, issue_session

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(enforce_rate_limit)])


class LoginRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)


class SessionOut(BaseModel):
    reviewer: str


@router.post("/login", response_model=SessionOut)
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


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=SessionOut)
def me(principal: AuthPrincipal = Depends(require_api_token)) -> SessionOut:
    return SessionOut(reviewer=principal.reviewer)
