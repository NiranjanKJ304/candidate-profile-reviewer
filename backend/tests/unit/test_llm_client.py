"""OpenAI-compatible LLM client: success, timeouts, rate limits, errors, retries."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.integrations.llm import (
    ChatCompletionClient,
    ChatMessage,
    LLMAuthError,
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from tests.conftest import SettingsFactory

KEY = "gsk_test_key_value_123456789012345"
MESSAGES = [ChatMessage("system", "s"), ChatMessage("user", "u")]


def _ok(content: str = '{"a": 1}', model: str = "served-model") -> httpx.Response:
    return httpx.Response(
        200, json={"model": model, "choices": [{"message": {"content": content}}]}
    )


def _client(
    handler: Callable[[httpx.Request], httpx.Response], key: str | None = KEY
) -> ChatCompletionClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ChatCompletionClient(
        base_url="https://api.example.test/openai/v1/",
        api_key=key,
        model="test-model",
        timeout_s=5,
        http_client=http,
    )


async def test_success_sends_json_mode_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    result = await _client(handler).complete_json(MESSAGES)
    assert result.content == '{"a": 1}'
    assert result.model == "served-model"
    request = seen[0]
    assert str(request.url) == "https://api.example.test/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


async def test_not_configured_makes_no_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _ok()

    client = _client(handler, key=None)
    assert client.configured is False
    with pytest.raises(LLMNotConfiguredError):
        await client.complete_json(MESSAGES)
    assert calls == 0


async def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMTimeoutError, match="did not respond"):
        await _client(handler).complete_json(MESSAGES)


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure(status: int) -> None:
    with pytest.raises(LLMAuthError):
        await _client(lambda r: httpx.Response(status, json={"error": "bad key"})).complete_json(
            MESSAGES
        )


async def test_rate_limit_after_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "0"})

    with pytest.raises(LLMRateLimitError):
        await _client(handler).complete_json(MESSAGES)
    assert calls == 2  # one retry


async def test_transient_5xx_is_retried_once_then_succeeds() -> None:
    responses = [httpx.Response(503, headers={"retry-after": "0"}), _ok()]
    result = await _client(lambda r: responses.pop(0)).complete_json(MESSAGES)
    assert result.content == '{"a": 1}'


async def test_unknown_model_names_the_model_and_the_fix() -> None:
    body = {"error": {"code": "model_not_found", "message": "The model does not exist"}}
    with pytest.raises(LLMResponseError) as info:
        await _client(lambda r: httpx.Response(404, json=body)).complete_json(MESSAGES)
    message = str(info.value)
    assert "HTTP 404" in message
    assert "'test-model'" in message
    assert "LLM_MODEL" in message
    assert KEY not in message


async def test_persistent_5xx() -> None:
    with pytest.raises(LLMResponseError, match="HTTP 500"):
        await _client(lambda r: httpx.Response(500, headers={"retry-after": "0"})).complete_json(
            MESSAGES
        )


async def test_connection_error_after_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMResponseError, match="ConnectError"):
        await _client(handler).complete_json(MESSAGES)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": 42}}]},
        {"unexpected": True},
    ],
)
async def test_malformed_envelope(body: object) -> None:
    with pytest.raises(LLMResponseError):
        await _client(lambda r: httpx.Response(200, json=body)).complete_json(MESSAGES)


async def test_non_json_body() -> None:
    with pytest.raises(LLMResponseError):
        await _client(lambda r: httpx.Response(200, text="<html>")).complete_json(MESSAGES)


async def test_errors_never_contain_the_api_key() -> None:
    for handler in (
        lambda r: httpx.Response(401),
        lambda r: httpx.Response(500, headers={"retry-after": "0"}),
        lambda r: httpx.Response(200, text="x"),
    ):
        with pytest.raises(LLMError) as info:
            await _client(handler).complete_json(MESSAGES)
        assert KEY not in str(info.value)


class TestFromSettings:
    def test_groq_defaults(self, make_settings: SettingsFactory) -> None:
        s = make_settings(groq_api_key=KEY)
        client = ChatCompletionClient.from_settings(s, httpx.AsyncClient())
        assert client.configured
        assert client.base_url == "https://api.groq.com/openai/v1"
        assert client.model == "openai/gpt-oss-120b"

    def test_openrouter_selected(self, make_settings: SettingsFactory) -> None:
        s = make_settings(llm_provider="openrouter", openrouter_api_key="sk-or-x", llm_model="m/x")
        client = ChatCompletionClient.from_settings(s, httpx.AsyncClient())
        assert client.base_url == "https://openrouter.ai/api/v1"
        assert client.model == "m/x"

    def test_key_for_other_provider_is_not_used(self, make_settings: SettingsFactory) -> None:
        s = make_settings(llm_provider="openrouter", groq_api_key=KEY)
        assert ChatCompletionClient.from_settings(s, httpx.AsyncClient()).configured is False

    def test_blank_key_is_not_configured(self, make_settings: SettingsFactory) -> None:
        assert make_settings(groq_api_key="   ").llm_api_key is None
