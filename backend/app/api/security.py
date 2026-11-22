"""JWT auth, switched by AUTH_ENABLED (default off = open API, every request passes as
"anonymous"; on = a valid Bearer token is required). When on, by default the API is its own token issuer (login + password from config,
tokens signed with JWT_SECRET). Setting JWT_JWKS_URL plugs in an external identity
provider: tokens are then verified against its published keys instead.
"""

import functools
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

# OAuth2 password flow, so the "Authorize" button in /docs works out of the box.
# auto_error=False: a missing token is decided in current_user, so open mode needs no header.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


def local_login_enabled() -> bool:
    return settings.auth_password is not None and not settings.jwt_jwks_url


def authenticate(username: str, password: str) -> bool:
    """Constant-time check of the configured login and password."""
    if settings.auth_password is None:
        return False
    user_ok = hmac.compare_digest(username.encode(), settings.auth_username.encode())
    pass_ok = hmac.compare_digest(password.encode(), settings.auth_password.get_secret_value().encode())
    return user_ok and pass_ok


def issue_token(subject: str) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": subject,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)


@functools.cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url)  # caches the provider's keys between requests


def _verification_key(token: str):
    if settings.jwt_jwks_url:
        return _jwks_client(settings.jwt_jwks_url).get_signing_key_from_jwt(token).key
    return settings.jwt_secret.get_secret_value()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Nieprawidłowy lub wygasły token.", headers={"WWW-Authenticate": "Bearer"}
    )


def current_user(token: Annotated[str | None, Depends(oauth2_scheme)]) -> str:
    """FastAPI dependency: "anonymous" with AUTH_ENABLED off, else the `sub` of a valid Bearer token, 401 otherwise."""
    if not settings.auth_enabled:
        return "anonymous"
    if not token:
        raise _unauthorized()
    try:
        claims = jwt.decode(
            token,
            _verification_key(token),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Serwer tożsamości jest niedostępny.")
    except jwt.PyJWTError:
        raise _unauthorized()
    return claims["sub"]
