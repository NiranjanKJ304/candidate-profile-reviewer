"""Settings: valid, invalid, missing and edge-case configuration."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Platform, Settings, get_settings
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL, SettingsFactory


class TestValidSettings:
    def test_defaults_are_applied(self, make_settings: SettingsFactory) -> None:
        s = make_settings(environment="development")
        assert s.app_name == "Candidate Intelligence API"
        assert s.api_v1_prefix == "/api/v1"
        assert s.cors_origins == ["http://localhost:3000"]
        assert s.enabled_platforms == []
        assert s.debug is False
        assert s.is_production is False

    def test_loads_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
        monkeypatch.setenv("LOG_LEVEL", "debug")
        monkeypatch.setenv("CORS_ORIGINS", "http://a.test, https://b.test ,")
        monkeypatch.setenv("ENABLED_PLATFORMS", "github,codeforces")
        s = Settings(_env_file=None)
        assert s.log_level == "DEBUG"
        assert s.cors_origins == ["http://a.test", "https://b.test"]
        assert s.enabled_platforms == [Platform.GITHUB, Platform.CODEFORCES]

    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
        monkeypatch.chdir("/")  # ensure no stray .env is picked up
        assert get_settings() is get_settings()

    def test_rediss_tls_url_is_accepted(self, make_settings: SettingsFactory) -> None:
        s = make_settings(redis_url="rediss://:pw@cache.example.com:6380/0")
        assert s.redis_url.get_secret_value().startswith("rediss://")


class TestSecretHandling:
    def test_secrets_are_masked_in_repr_and_dump(self, make_settings: SettingsFactory) -> None:
        s = make_settings()
        for text in (repr(s), str(s), s.model_dump_json()):
            assert "s3cr3t-pw" not in text
            assert "r3dis-pw" not in text

    def test_redacted_database_url_hides_password(self, make_settings: SettingsFactory) -> None:
        redacted = make_settings().database_url_redacted
        assert "s3cr3t-pw" not in redacted
        assert "tester" in redacted
        assert "db.internal" in redacted

    def test_settings_are_immutable(self, make_settings: SettingsFactory) -> None:
        s = make_settings()
        with pytest.raises(ValidationError):
            s.debug = True  # type: ignore[misc]


class TestMissingSettings:
    def test_missing_database_url(self) -> None:
        with pytest.raises(ValidationError, match="database_url"):
            Settings(_env_file=None, redis_url=SecretStr(TEST_REDIS_URL))

    def test_missing_redis_url(self) -> None:
        with pytest.raises(ValidationError, match="redis_url"):
            Settings(_env_file=None, database_url=SecretStr(TEST_DATABASE_URL))

    def test_missing_everything_reports_both(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        fields = {err["loc"][0] for err in exc_info.value.errors()}
        assert fields == {"database_url", "redis_url"}


class TestInvalidSettings:
    @pytest.mark.parametrize(
        "url",
        [
            "postgresql://u:p@h:5432/db",  # sync driver
            "mysql+aiomysql://u:p@h/db",  # wrong database
            "postgresql+asyncpg://",  # no host/database
            "postgresql+asyncpg://u:p@h:5432",  # no database name
            "not a url",
            "",
        ],
    )
    def test_rejects_bad_database_url(self, make_settings: SettingsFactory, url: str) -> None:
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            make_settings(database_url=url)

    @pytest.mark.parametrize("url", ["http://cache:6379", "redis://", "localhost:6379", ""])
    def test_rejects_bad_redis_url(self, make_settings: SettingsFactory, url: str) -> None:
        with pytest.raises(ValidationError, match="REDIS_URL"):
            make_settings(redis_url=url)

    def test_rejects_unknown_environment(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError):
            make_settings(environment="prod")

    def test_rejects_unknown_log_level(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError):
            make_settings(log_level="VERBOSE")

    def test_rejects_unknown_platform(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError):
            make_settings(enabled_platforms="github,myspace")

    def test_rejects_invalid_cors_origin(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError, match="CORS"):
            make_settings(cors_origins="localhost:3000")

    @pytest.mark.parametrize("prefix", ["api/v1", "/api/v1/"])
    def test_rejects_bad_prefix(self, make_settings: SettingsFactory, prefix: str) -> None:
        with pytest.raises(ValidationError, match="api_v1_prefix"):
            make_settings(api_v1_prefix=prefix)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("readiness_timeout_seconds", 0),
            ("readiness_timeout_seconds", -1),
            ("readiness_timeout_seconds", 31),
            ("db_pool_size", 0),
            ("db_max_overflow", -1),
        ],
    )
    def test_rejects_out_of_range_numbers(
        self, make_settings: SettingsFactory, field: str, value: float
    ) -> None:
        with pytest.raises(ValidationError):
            make_settings(**{field: value})


class TestProductionGuards:
    def test_debug_forbidden_in_production(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError, match="DEBUG"):
            make_settings(environment="production", debug=True)

    def test_wildcard_cors_forbidden_in_production(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError, match="Wildcard"):
            make_settings(environment="production", cors_origins="*")

    def test_db_echo_forbidden_in_production(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError, match="DB_ECHO"):
            make_settings(environment="production", db_echo=True)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"database_url": "postgresql+asyncpg://u:change-me-postgres@db:5432/x"},
            {"redis_url": "redis://:change-me-redis@cache:6379/0"},
        ],
    )
    def test_placeholder_passwords_forbidden_in_production(
        self, make_settings: SettingsFactory, overrides: dict[str, str]
    ) -> None:
        with pytest.raises(ValidationError, match="placeholder password"):
            make_settings(
                environment="production", cors_origins="https://app.example.com", **overrides
            )

    def test_placeholder_passwords_allowed_in_development(
        self, make_settings: SettingsFactory
    ) -> None:
        s = make_settings(redis_url="redis://:change-me-redis@cache:6379/0")
        assert s.environment == "test"

    def test_wildcard_cors_allowed_in_development(self, make_settings: SettingsFactory) -> None:
        assert make_settings(environment="development", cors_origins="*").cors_origins == ["*"]

    def test_valid_production_settings(self, make_settings: SettingsFactory) -> None:
        s = make_settings(environment="production", cors_origins="https://app.example.com")
        assert s.is_production


class TestLLMAndResumeSettings:
    def test_defaults(self, make_settings: SettingsFactory) -> None:
        s = make_settings()
        assert s.llm_provider == "groq"
        assert s.llm_api_key is None
        assert s.llm_resolved_base_url == "https://api.groq.com/openai/v1"
        assert s.resume_max_bytes == 10 * 1024 * 1024
        assert s.ocr_enabled is True

    def test_api_key_is_secret(self, make_settings: SettingsFactory) -> None:
        s = make_settings(groq_api_key="gsk_should_not_leak_123456")
        assert "gsk_should_not_leak" not in repr(s)
        assert s.llm_api_key is not None

    def test_custom_base_url_must_be_https(self, make_settings: SettingsFactory) -> None:
        with pytest.raises(ValidationError, match="https"):
            make_settings(llm_base_url="http://insecure.example.com/v1")
        s = make_settings(llm_base_url="https://proxy.example.com/v1/")
        assert s.llm_resolved_base_url == "https://proxy.example.com/v1"

    def test_blank_model_falls_back_to_default(self, make_settings: SettingsFactory) -> None:
        assert make_settings(llm_model="  ").llm_resolved_model == "openai/gpt-oss-120b"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("llm_provider", "openai"),
            ("llm_timeout_seconds", 0),
            ("llm_max_attempts", 0),
            ("resume_max_bytes", 10),
            ("resume_max_pages", 0),
            ("ocr_dpi", 1000),
            ("ocr_language", "ENG;rm -rf"),
        ],
    )
    def test_rejects_invalid_values(
        self, make_settings: SettingsFactory, field: str, value: object
    ) -> None:
        with pytest.raises(ValidationError):
            make_settings(**{field: value})


class TestCodeforcesSettings:
    def test_defaults_and_enablement(self, make_settings: SettingsFactory) -> None:
        settings = make_settings()
        assert settings.codeforces_enabled is False
        assert settings.codeforces_submission_limit == 500
        assert settings.codeforces_min_request_interval_seconds == 2.0
        enabled = make_settings(enabled_platforms="codeforces")
        assert enabled.codeforces_enabled is True
        assert enabled.github_enabled is False

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("codeforces_min_request_interval_seconds", 1.0),  # Codeforces asks for >= 2 s
            ("codeforces_submission_limit", 1001),  # hard maximum
            ("codeforces_submission_limit", 0),
            ("codeforces_timeout_seconds", 0),
            ("codeforces_max_retries", 9),
        ],
    )
    def test_rejects_invalid_values(
        self, make_settings: SettingsFactory, field: str, value: object
    ) -> None:
        with pytest.raises(ValidationError):
            make_settings(**{field: value})


class TestLeetCodeSettings:
    def test_defaults_and_enablement(self, make_settings: SettingsFactory) -> None:
        assert make_settings().leetcode_enabled is False
        settings = make_settings(enabled_platforms="leetcode")
        assert settings.leetcode_enabled is True
        assert settings.leetcode_evidence_stale_after_days == 30

    @pytest.mark.parametrize("value", [0, 366])
    def test_rejects_invalid_staleness(self, make_settings: SettingsFactory, value: int) -> None:
        with pytest.raises(ValidationError):
            make_settings(leetcode_evidence_stale_after_days=value)
