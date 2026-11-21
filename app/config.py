"""Runtime configuration from environment variables (or a local `.env`).

Everything is read once at import into `settings`; other modules use
`from app.config import settings`. Secrets come only from the environment at
runtime, nothing secret has a default.
"""

import logging
import secrets
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # Upload
    max_file_size_mb: float = 10
    # Upload page history (app/history.py): SQLite file with every processed file and its result.
    history_db: Path = Path(__file__).resolve().parent.parent / "history.sqlite3"

    # Auth switch: off (default for now) = open API and no login on the pages; 1 = JWT required.
    auth_enabled: bool = False
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

    # Model that plans the form (app/llm/client.py), any OpenAI-compatible chat API:
    # "ollama" = local Ollama (its /v1 endpoint, no key), "openai" = remote API with a key.
    llm_backend: Literal["ollama", "openai"] = "ollama"
    llm_base_url: str = Field("http://localhost:11434/v1", validation_alias=AliasChoices("LLM_BASE_URL", "LLM_URL"))
    llm_model: str = "qwen3:30b-a3b-instruct-2507-q4_K_M"
    llm_timeout_s: float = 1200  # one call; form 9 (16 pages) is the longest
    edenai_api_key: SecretStr | None = None  # Bearer key for LLM_BACKEND=openai (Eden AI)
    # Vision (app/llm/vision.py): PDF pages and JPG/PNG also go to the planner as page images,
    # next to the text (needs a vision model in LLM_MODEL). Qwen3-VL spends ~1 token per 32x32 px:
    # an A4 page at 1400 px is ~1.4k tokens, 8 of them keep pass 1 of a long form near 22k of 32k.
    llm_vision: bool = False
    llm_vision_max_side: int = 1400  # long side of a page image in pass 1 (structure), px
    llm_vision_max_pages: int = 8  # only the first N pages get an image, the rest is text only
    llm_vision_conditions_side: int = 1024  # long side in pass 2 (conditions), px; 0 = text only
    # 0 = images only, no extracted text, when every page of the file has an image (a longer file
    # keeps the text); quotes then come from the model's own reading, checked at THRESHOLD_OCR.
    llm_vision_text: bool = True
    llm_vision_gray: bool = False  # 1 = page images in grayscale
    # OCR
    ocr_engine: str = "tesseract"  # "tesseract" or "vlm" (vision model in Ollama)
    ocr_vlm_model: str | None = None  # model for OCR_ENGINE=vlm; unset = LLM_MODEL (must read images)
    # Dev only: folder of bench runs (e.g. bench/runs) for the review page /review (app/review.py).
    # Unset = the page and its endpoints answer 404, as in Docker and for the jury.
    review_runs_dir: Path | None = None

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
