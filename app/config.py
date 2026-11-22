"""Runtime configuration from environment variables (or a local `.env`).

Everything is read once at import into `settings`; other modules use
`from app.config import settings`. Secrets come only from the environment at
runtime, nothing secret has a default.
"""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # Upload
    max_file_size_mb: float = 10

    # Models on Eden AI (app/llm/client.py), an OpenAI-compatible chat API; EDENAI_API_KEY is the only
    # setting that must be set. The planner reads text only (the Markdown of the pages), so the page
    # images are transcribed by a vision model of its own (app/llm/transcribe.py), never by LLM_MODEL.
    llm_base_url: str = Field("https://api.edenai.run/v3", validation_alias=AliasChoices("LLM_BASE_URL", "LLM_URL"))
    llm_model: str = "deepinfra/zai-org/GLM-5.2"
    llm_timeout_s: float = 1200  # one call; a 16-page form is the longest
    edenai_api_key: SecretStr | None = None  # Bearer key for every model call; secret, never logged
    # Every file -> page images -> Markdown, one vision call per page -> the planner reads the Markdown (an office file
    # whose LibreOffice PDF lost text: its own text, `app.llm.transcribe`).
    transcribe_model: str = "deepinfra/Qwen/Qwen3.5-397B-A17B"
    transcribe_side: int = 1024  # long side of a page image sent for transcription, px
    transcribe_workers: int = 4  # pages transcribed at the same time
    transcribe_timeout_s: float = 180  # per HTTP try
    soffice: str = "soffice"  # LibreOffice binary (DOC/DOCX/ODS/XLS/XLSX -> PDF); macOS app path tried too

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


settings = Settings()
