"""Environment-based application configuration.

All configuration comes from environment variables (optionally a local ``.env``
file). Credentials are typed as :class:`pydantic.SecretStr` so they are masked in
``repr()``, logs and tracebacks. Invalid or missing required settings fail fast at
startup with a clear validation error instead of failing later at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.enums import Platform

__all__ = ["Platform", "Settings", "get_settings"]

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LLMProvider = Literal["groq", "openrouter"]

# Both providers expose the OpenAI-compatible chat completions API.
LLM_DEFAULT_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
# Defaults only; override with LLM_MODEL when a provider retires a model.
# Groq retired llama-3.3-70b-versatile for this account (HTTP 404 model_not_found,
# checked 2026-11-14); openai/gpt-oss-120b was verified to work in JSON mode.
LLM_DEFAULT_MODELS: dict[str, str] = {
    "groq": "openai/gpt-oss-120b",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
}

# Placeholder used in .env.example files; never acceptable in production.
PLACEHOLDER_SECRET_MARKER = "change-me"  # noqa: S105 - a marker to detect, not a credential


def _split_csv(value: object) -> object:
    """Accept comma-separated strings for list settings (e.g. ``a,b , c``)."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "Candidate Intelligence API"
    environment: Environment = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Logging -------------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_json: bool = True

    # --- HTTP / CORS ---------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- PostgreSQL ----------------------------------------------------------
    database_url: SecretStr
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    db_echo: bool = False

    # --- Redis ---------------------------------------------------------------
    redis_url: SecretStr
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # --- Health checks -------------------------------------------------------
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # --- Integrations (collectors arrive in later phases) ----------------------
    enabled_platforms: Annotated[list[Platform], NoDecode] = Field(default_factory=list)

    # --- LLM (OpenAI-compatible chat completions API) ---------------------------
    llm_provider: LLMProvider = "groq"
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    llm_model: str | None = Field(default=None, max_length=200)
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    llm_max_attempts: int = Field(default=2, ge=1, le=5)
    llm_max_input_chars: int = Field(default=60_000, ge=1_000, le=500_000)
    # Candidate evaluation prompt budget (tokens). Groq's free tier allows 8,000 tokens per
    # minute for openai/gpt-oss-120b including the answer; raise this on a paid tier.
    llm_evaluation_max_prompt_tokens: int = Field(default=4500, ge=1000, le=200_000)

    # --- Resume upload & parsing --------------------------------------------------
    resume_storage_dir: Path = Path("storage/resumes")
    resume_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    resume_max_pages: int = Field(default=30, ge=1, le=200)
    ocr_enabled: bool = True
    ocr_tessdata_dir: Path | None = None
    ocr_language: str = Field(default="eng", pattern=r"^[a-z_]{3,}(\+[a-z_]{3,})*$")
    ocr_dpi: int = Field(default=300, ge=72, le=600)

    # --- GitHub integration (Phase 2; enabled via ENABLED_PLATFORMS=github) -------
    # Optional token: raises the REST limit from 60 to 5,000 requests/hour. Needs no
    # scopes (public data only). Never logged or returned.
    github_token: SecretStr | None = None
    github_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    github_max_retries: int = Field(default=2, ge=0, le=5)
    github_max_repo_pages: int = Field(default=3, ge=1, le=10)  # x100 repositories
    github_readme_limit: int = Field(default=10, ge=0, le=50)  # READMEs fetched per sync
    github_readme_max_bytes: int = Field(default=100_000, ge=1_000, le=1_000_000)
    github_max_event_pages: int = Field(default=3, ge=0, le=3)  # API caps at 300 events
    github_search_items: int = Field(default=10, ge=0, le=100)  # recent PRs / issues kept

    # --- Codeforces integration (Phase 3; enabled via ENABLED_PLATFORMS=codeforces) --
    # Public API methods only (no API key). Codeforces allows about 1 call per
    # 2 seconds, so requests are paced; the interval cannot be set below 2 s.
    codeforces_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    codeforces_max_retries: int = Field(default=2, ge=0, le=5)
    codeforces_min_request_interval_seconds: float = Field(default=2.0, ge=2.0, le=30.0)
    codeforces_submission_limit: int = Field(default=500, ge=1, le=1000)  # hard max 1000

    # --- LeetCode (Phase 4; enabled via ENABLED_PLATFORMS=leetcode) ------------------
    # No official/permitted API exists, so LeetCode is never contacted. Only manual,
    # self-reported evidence is stored; it is flagged stale after this many days.
    leetcode_evidence_stale_after_days: int = Field(default=30, ge=1, le=365)

    # --- Job-description upload & parsing (Phase 1B; OCR settings shared) ----------
    job_description_storage_dir: Path = Path("storage/job_descriptions")
    job_description_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    job_description_max_pages: int = Field(default=20, ge=1, le=200)

    # ------------------------------------------------------------------------
    @field_validator("cors_origins", "enabled_platforms", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("api_v1_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_v1_prefix must start with '/' and must not end with '/'")
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the async driver: postgresql+asyncpg://...")
        try:
            url = make_url(raw)
        except Exception as exc:  # sqlalchemy raises ArgumentError for malformed URLs
            raise ValueError("DATABASE_URL is not a valid database URL") from exc
        if not url.host or not url.database:
            raise ValueError("DATABASE_URL must include a host and a database name")
        return SecretStr(raw)

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must start with redis:// or rediss://")
        if raw in ("redis://", "rediss://"):
            raise ValueError("REDIS_URL must include a host")
        return SecretStr(raw)

    @field_validator("cors_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if origin != "*" and not origin.startswith(("http://", "https://")):
                raise ValueError(f"Invalid CORS origin {origin!r}: must start with http(s)://")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS origin is not allowed in production")
            if self.db_echo:
                raise ValueError("DB_ECHO must be false in production (it logs SQL parameters)")
            for name, secret in (
                ("DATABASE_URL", self.database_url),
                ("REDIS_URL", self.redis_url),
            ):
                if PLACEHOLDER_SECRET_MARKER in secret.get_secret_value():
                    raise ValueError(f"{name} still contains a placeholder password in production")
        return self

    # ------------------------------------------------------------------------
    @field_validator("llm_base_url")
    @classmethod
    def _validate_llm_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().rstrip("/")
        if not value.startswith("https://"):
            raise ValueError("LLM_BASE_URL must use https://")
        return value

    @field_validator("llm_model")
    @classmethod
    def _blank_model_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def llm_api_key(self) -> SecretStr | None:
        """API key for the selected provider (None when not configured)."""
        key = self.groq_api_key if self.llm_provider == "groq" else self.openrouter_api_key
        if key is None or not key.get_secret_value().strip():
            return None
        return key

    @property
    def github_enabled(self) -> bool:
        return Platform.GITHUB in self.enabled_platforms

    @property
    def codeforces_enabled(self) -> bool:
        return Platform.CODEFORCES in self.enabled_platforms

    @property
    def leetcode_enabled(self) -> bool:
        return Platform.LEETCODE in self.enabled_platforms

    @property
    def github_token_value(self) -> str | None:
        if self.github_token is None or not self.github_token.get_secret_value().strip():
            return None
        return self.github_token.get_secret_value().strip()

    @property
    def llm_resolved_base_url(self) -> str:
        return self.llm_base_url or LLM_DEFAULT_BASE_URLS[self.llm_provider]

    @property
    def llm_resolved_model(self) -> str:
        return self.llm_model or LLM_DEFAULT_MODELS[self.llm_provider]

    @property
    def database_url_redacted(self) -> str:
        """Database URL safe for logs (password masked)."""
        return make_url(self.database_url.get_secret_value()).render_as_string(hide_password=True)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (loaded once)."""
    return Settings()  # required fields come from the environment
