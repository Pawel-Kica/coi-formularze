"""`/api/auth`: whether a token is needed, and the local login that issues one (`app.api.security`)."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.security import authenticate, issue_token, local_login_enabled
from app.core.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


_CREDENTIALS_BODY = {"requestBody": {"required": True, "content": {
    ct: {"schema": Credentials.model_json_schema()} for ct in ("application/json", "application/x-www-form-urlencoded")}}}


@router.get("/config")
async def auth_config() -> dict:
    """Whether a token is required (AUTH_ENABLED), so the pages skip the login form when it is not,
    and whether the server keeps the upload page history (HISTORY_ENABLED)."""
    return {"enabled": settings.auth_enabled, "history": settings.history_enabled}


@router.post("/token", openapi_extra=_CREDENTIALS_BODY)
async def login(request: Request) -> Token:
    """Exchange the configured login and password (JSON or form) for a Bearer JWT."""
    if not local_login_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Logowanie lokalne jest wyłączone (brak AUTH_PASSWORD lub tokeny wydaje zewnętrzny serwer tożsamości).",
        )
    is_json = request.headers.get("content-type", "").startswith("application/json")
    try:
        raw = await request.json() if is_json else dict(await request.form())
        credentials = Credentials.model_validate(raw)
    except ValueError:  # bad JSON or missing fields (pydantic ValidationError is a ValueError)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Podaj username i password.")
    if not authenticate(credentials.username, credentials.password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Błędny login lub hasło.", headers={"WWW-Authenticate": "Bearer"}
        )
    return Token(access_token=issue_token(credentials.username), expires_in=settings.jwt_ttl_minutes * 60)
