"""Runtime configuration from environment variables (or a local `.env`).

Everything is read once at import into `settings`; other modules use
`from app.config import settings`. Secrets come only from the environment at
runtime, nothing secret has a default.
"""

import logging
import secrets
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # Upload
    max_file_size_mb: float = 10

    # Auth: the API issues its own JWTs for AUTH_USERNAME/AUTH_PASSWORD, signed with JWT_SECRET.
    # With JWT_JWKS_URL set, tokens come from an external identity provider instead
    # (local login is off and JWT_ALGORITHM must be asymmetric, e.g. RS256).
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "coi-formularze"
    jwt_audience: str = "coi-formularze"
    jwt_ttl_minutes: int = 60
    jwt_jwks_url: str | None = None
    auth_username: str = "admin"
    auth_password: SecretStr | None = None

    # Local model (Ollama) and OCR, read by the pipeline
    llm_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:30b-a3b-instruct-2507-q4_K_M"
    llm_timeout_s: float = 900
    ocr_engine: str = "tesseract"  # "tesseract" or "vlm" (vision model in Ollama)
    ocr_vlm_model: str = "qwen2.5vl:7b"

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)

    @model_validator(mode="after")
    def _check_jwt(self) -> Self:
        if self.jwt_jwks_url:
            if self.jwt_algorithm.startswith("HS"):
                raise ValueError("JWT_JWKS_URL needs an asymmetric JWT_ALGORITHM such as RS256")
            return self
        if self.jwt_secret is None:
            # Safe dev fallback: unguessable, but tokens die with the process. Docker compose
            # refuses to start without JWT_SECRET, so this only happens in local runs.
            log.warning("JWT_SECRET not set: using a random per-process secret (set it in production)")
            self.jwt_secret = SecretStr(secrets.token_urlsafe(32))
        elif len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters (e.g. `openssl rand -hex 32`)")
        return self


settings = Settings()
