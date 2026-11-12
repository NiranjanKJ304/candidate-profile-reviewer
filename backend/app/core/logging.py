"""Structured logging with secret redaction.

* JSON lines (or a readable text format for local development).
* Every record carries the current ``request_id`` when inside a request.
* A redaction filter masks credentials in both the message and structured
  ``extra`` fields before any handler formats the record. Redaction is applied
  defensively: code should never log secrets, but if it does they are masked.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.context import get_request_id

REDACTED = "***REDACTED***"

# Keys whose values are always masked (matched case-insensitively as substrings).
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "database_url",
    "redis_url",
    "dsn",
)

# Patterns masked inside free text. Order matters: specific tokens before generic ones.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host  -> scheme://user:***@host
    (
        re.compile(r"(?P<pre>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/@\s]*:)[^@\s]+(?P<post>@)"),
        rf"\g<pre>{REDACTED}\g<post>",
    ),
    # Authorization: Bearer xxx / Basic xxx / token xxx
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=\-]{8,}"), rf"\1 {REDACTED}"),
    # GitHub tokens (classic and fine-grained)
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), REDACTED),
    # Groq / OpenRouter / OpenAI-style keys
    (
        re.compile(r"\b(?:gsk_[A-Za-z0-9]{20,}|sk-or-[A-Za-z0-9\-]{20,}|sk-[A-Za-z0-9\-]{20,})\b"),
        REDACTED,
    ),
    # key=value / key: value pairs with sensitive key names
    (
        re.compile(
            r"(?i)\b(?P<key>[\w\-]*(?:password|passwd|secret|token|api[_\-]?key)[\w\-]*)"
            r"(?P<sep>\s*[=:]\s*)(?P<q>[\"']?)[^\s\"',;&]+"
        ),
        rf"\g<key>\g<sep>\g<q>{REDACTED}",
    ),
)

# Attributes present on every LogRecord; anything else came from ``extra=``.
_STANDARD_ATTRS: frozenset[str] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    | {"message", "asctime", "request_id", "taskName", "color_message"}
)


def redact_text(text: str) -> str:
    """Mask secrets that appear inside free text."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_value(value: Any, key: str | None = None) -> Any:
    """Recursively mask secrets in structured data."""
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact_value(v) for v in value]
    return value


class RedactionFilter(logging.Filter):
    """Masks secrets in the message, args and ``extra`` fields of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed %-args: keep the raw template
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = None
        for attr, value in list(vars(record).items()):
            if attr not in _STANDARD_ATTRS:
                setattr(record, attr, redact_value(value, attr))
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True


class RequestIdFilter(logging.Filter):
    """Attaches the current request ID to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    """Formats records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{datetime.fromtimestamp(record.created, tz=UTC):%H:%M:%S} "
            f"{record.levelname:<8} [{getattr(record, 'request_id', None) or '-'}] "
            f"{record.name}: {record.getMessage()}"
        )
        extras = _extras(record)
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + redact_text(self.formatException(record.exc_info))
        return base


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Configure root and uvicorn loggers. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter() if json_logs else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn through our handler; we emit our own access log line.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
