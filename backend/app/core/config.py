"""Runtime configuration from environment variables (or a local `.env`).

Everything is read once at import into `settings`; other modules use
`from app.core.config import settings`. Secrets come only from the environment at
runtime, nothing secret has a default.
"""

import secrets
import tomllib
from pathlib import Path
from typing import Self

from pydantic import AliasChoices, Field, PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/ (app/core/config.py -> app/core -> app -> backend)
REPO_DIR = BACKEND_DIR.parent
# The app's version, written only in pyproject.toml (the Docker image copies it next to app/).
VERSION = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_DIR / ".env", ".env"), env_ignore_empty=True, extra="ignore")

    # The frontend's static pages, served by `app.main` (Docker: /app/frontend).
    frontend_dir: Path = REPO_DIR / "frontend"

    # DEBUG adds every plan item the quote filter dropped; WARNING keeps only problems.
    log_level: str = "INFO"

    # Upload
    max_file_size_mb: float = 10
    # Upload page history (app/history.py): SQLite file with every processed file and its result.
    history_db: Path = BACKEND_DIR / "history.sqlite3"
    # 0 = the server keeps no uploaded file or result after the request (/api/history answers 404, the
    # page keeps its list only until reload); the dictionary catalog stays, it holds names and values only.
    history_enabled: bool = True

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
    _jwt_secret_random: bool = PrivateAttr(False)  # set by `_check_jwt`, never from the environment
    auth_username: str = "admin"
    auth_password: SecretStr | None = None

    # Models on Eden AI (app/llm/client.py), an OpenAI-compatible chat API; EDENAI_API_KEY is the only
    # setting that must be set. The planner reads text only (the Markdown of the pages), so the page
    # images are transcribed by a vision model of its own (app/llm/transcribe.py), never by LLM_MODEL.
    llm_base_url: str = Field("https://api.edenai.run/v3", validation_alias=AliasChoices("LLM_BASE_URL", "LLM_URL"))
    llm_model: str = "deepinfra/zai-org/GLM-5.2"
    llm_timeout_s: float = 1200  # one call; form 9 (16 pages) is the longest
    edenai_api_key: SecretStr | None = None  # Bearer key for every model call; secret, never logged
    # Every file -> page images -> Markdown, one vision call per page -> the planner reads the Markdown (an office file
    # whose LibreOffice PDF lost text: its own text, `app.llm.transcribe`).
    transcribe_model: str = "deepinfra/Qwen/Qwen3.5-397B-A17B"
    transcribe_side: int = 1024  # long side of a page image sent for transcription, px (1024 reads form 8 fine)
    transcribe_workers: int = 4  # pages transcribed at the same time
    transcribe_timeout_s: float = 180  # per HTTP try; a looping page times out once, then its penalized retry gets 2
    # tries, so a stuck page costs <= ~9 min (a rate limit first: one more plain call of 2 tries before that)
    soffice: str = "soffice"  # LibreOffice binary (DOC/DOCX/ODS/XLS/XLSX -> PDF); macOS app path tried too

    @property
    def jwt_secret_random(self) -> bool:
        """JWT_SECRET was not set, tokens are signed with a random secret that dies with the process."""
        return self._jwt_secret_random

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
            # Safe fallback: unguessable, but tokens die with the process (and with AUTH_ENABLED=0 nobody
            # needs one); set JWT_SECRET in production. Nothing refuses to start without it (`app.main` warns).
            self._jwt_secret_random = True
            self.jwt_secret = SecretStr(secrets.token_urlsafe(32))
        elif len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters (e.g. `openssl rand -hex 32`)")
        return self


settings = Settings()
