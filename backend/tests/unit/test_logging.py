"""Structured logging and secret redaction."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from app.core.context import request_id_ctx
from app.core.logging import (
    REDACTED,
    JsonFormatter,
    RedactionFilter,
    RequestIdFilter,
    TextFormatter,
    configure_logging,
    redact_text,
    redact_value,
)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.capture")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, stream
    logger.handlers = []


def _last_json(stream: io.StringIO) -> dict[str, object]:
    line = stream.getvalue().strip().splitlines()[-1]
    parsed: dict[str, object] = json.loads(line)
    return parsed


class TestRedactText:
    @pytest.mark.parametrize(
        ("raw", "secret"),
        [
            ("postgresql+asyncpg://user:hunter2@db:5432/x", "hunter2"),
            ("redis://:r3dis@cache:6379/0", "r3dis"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def", "eyJhbGciOiJIUzI1NiJ9"),
            ("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "ghp_ABCDEFGHIJ"),
            ("using github_pat_11ABCDEFG0123456789_abcdefghijklmnop", "github_pat_11"),
            ("groq key gsk_abcdefghijklmnopqrstuvwxyz012345", "gsk_abcdefghij"),
            ("or key sk-or-v1-abcdef0123456789abcdef0123", "sk-or-v1-abcdef"),
            ("password=supersecret&user=bob", "supersecret"),
            ('api_key: "k-12345678"', "k-12345678"),
            ("GITHUB_TOKEN=abc123xyz", "abc123xyz"),
        ],
    )
    def test_masks_known_secret_shapes(self, raw: str, secret: str) -> None:
        out = redact_text(raw)
        assert secret not in out
        assert REDACTED in out

    def test_leaves_ordinary_text_untouched(self) -> None:
        text = "candidate has 5 repositories at https://github.com/octocat"
        assert redact_text(text) == text

    def test_url_username_and_host_are_kept(self) -> None:
        out = redact_text("postgresql://alice:pw@db.local/app")
        assert out == f"postgresql://alice:{REDACTED}@db.local/app"

    def test_empty_string(self) -> None:
        assert redact_text("") == ""


class TestRedactValue:
    def test_masks_sensitive_keys_recursively(self) -> None:
        data = {
            "user": "bob",
            "Authorization": "Basic Zm9vOmJhcg==",
            "nested": {"api_key": "k", "items": [{"refresh_token": "t"}, "plain"]},
            "database_url": "postgresql+asyncpg://u:p@h/d",
        }
        out = redact_value(data)
        assert out["user"] == "bob"
        assert out["Authorization"] == REDACTED
        assert out["nested"]["api_key"] == REDACTED
        assert out["nested"]["items"][0]["refresh_token"] == REDACTED
        assert out["nested"]["items"][1] == "plain"
        assert out["database_url"] == REDACTED

    def test_non_string_values_pass_through(self) -> None:
        assert redact_value(42) == 42
        assert redact_value(None) is None


class TestLogRecords:
    def test_json_output_has_core_fields(self, capture: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = capture
        logger.info("hello %s", "world", extra={"candidate_count": 3})
        record = _last_json(stream)
        assert record["message"] == "hello world"
        assert record["level"] == "INFO"
        assert record["logger"] == "test.capture"
        assert record["candidate_count"] == 3
        assert record["request_id"] is None
        assert "timestamp" in record

    def test_request_id_is_attached(self, capture: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = capture
        token = request_id_ctx.set("req-123")
        try:
            logger.info("inside request")
        finally:
            request_id_ctx.reset(token)
        assert _last_json(stream)["request_id"] == "req-123"

    def test_secrets_in_message_args_are_redacted(
        self, capture: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = capture
        logger.info("connecting to %s", "redis://:topsecret@cache:6379")
        assert "topsecret" not in stream.getvalue()

    def test_secrets_in_extra_are_redacted(
        self, capture: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = capture
        logger.info("calling api", extra={"github_token": "ghp_x", "headers": {"Cookie": "c=1"}})
        record = _last_json(stream)
        assert record["github_token"] == REDACTED
        assert record["headers"] == {"Cookie": REDACTED}

    def test_secrets_in_exception_are_redacted(
        self, capture: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = capture
        try:
            raise ConnectionError("cannot reach postgresql://u:leaked-pw@h/db")
        except ConnectionError:
            logger.exception("failure")
        output = stream.getvalue()
        assert "leaked-pw" not in output
        assert "ConnectionError" in output

    def test_malformed_format_args_do_not_crash(
        self, capture: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = capture
        logger.info("value %d", "not-a-number")
        assert "value %d" in stream.getvalue()


class TestConfigureLogging:
    def test_configures_root_once_and_is_idempotent(self) -> None:
        configure_logging("WARNING", json_logs=True)
        configure_logging("INFO", json_logs=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert root.level == logging.INFO
        assert isinstance(root.handlers[0].formatter, TextFormatter)
        assert logging.getLogger("uvicorn.access").disabled is True

    def test_text_formatter_includes_extras(self) -> None:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        record.request_id = "abc"
        record.foo = "bar"
        line = TextFormatter().format(record)
        assert "[abc]" in line
        assert "foo=bar" in line
