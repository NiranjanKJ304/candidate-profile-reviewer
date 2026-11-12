"""OpenAI-compatible chat-completions client (Groq, OpenRouter).

API integration only - no resume logic. Prompts and responses may contain
personal data, so they are never logged; only sizes, model and timings are.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger("app.llm")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_S = 5.0


class LLMError(Exception):
    """Base class; ``code`` maps onto ResumeErrorCode values."""

    code = "llm_error"


class LLMNotConfiguredError(LLMError):
    code = "llm_not_configured"


class LLMAuthError(LLMError):
    code = "llm_auth_failed"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"


class LLMResponseError(LLMError):
    code = "llm_error"


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    duration_ms: float


class ChatCompletionClient:
    """Minimal async client for ``POST {base_url}/chat/completions`` in JSON mode."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_s: float,
        http_client: httpx.AsyncClient,
        transport_retries: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self._http = http_client
        self._transport_retries = transport_retries

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @classmethod
    def from_settings(
        cls, settings: Settings, http_client: httpx.AsyncClient
    ) -> ChatCompletionClient:
        key = settings.llm_api_key
        return cls(
            base_url=settings.llm_resolved_base_url,
            api_key=key.get_secret_value() if key else None,
            model=settings.llm_resolved_model,
            timeout_s=settings.llm_timeout_seconds,
            http_client=http_client,
        )

    async def complete_json(self, messages: list[ChatMessage]) -> ChatResult:
        """Return the assistant message content (expected to be a JSON object)."""
        if not self._api_key:
            raise LLMNotConfiguredError("No API key configured for the LLM provider")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        url = f"{self.base_url}/chat/completions"

        attempt = 0
        while True:
            attempt += 1
            start = time.perf_counter()
            try:
                response = await self._http.post(
                    url, json=payload, headers=headers, timeout=self.timeout_s
                )
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(f"LLM did not respond within {self.timeout_s}s") from exc
            except httpx.HTTPError as exc:
                if attempt <= self._transport_retries:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise LLMResponseError(
                    f"Could not reach the LLM provider ({type(exc).__name__})"
                ) from exc

            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            status = response.status_code
            if status in _RETRYABLE_STATUS and attempt <= self._transport_retries:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            if status in (401, 403):
                raise LLMAuthError(f"LLM provider rejected the API key (HTTP {status})")
            if status == 429:
                raise LLMRateLimitError("LLM provider rate limit reached")
            if status >= 400:
                raise LLMResponseError(f"LLM provider returned HTTP {status}")

            content, model = _parse_completion(response)
            logger.info(
                "llm completion",
                extra={
                    "llm_model": model,
                    "duration_ms": duration_ms,
                    "response_chars": len(content),
                    "attempt": attempt,
                },
            )
            return ChatResult(content=content, model=model, duration_ms=duration_ms)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    try:
        if header is not None:
            return min(max(float(header), 0.0), _MAX_RETRY_AFTER_S)
    except ValueError:
        pass
    return min(0.5 * attempt, _MAX_RETRY_AFTER_S)


def _parse_completion(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        model = str(body.get("model") or "")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("LLM response did not have the expected structure") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMResponseError("LLM response contained no content")
    return content, model
