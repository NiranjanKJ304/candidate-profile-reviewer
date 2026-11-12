"""Test double for the LLM client (tests only - never used by the application)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.integrations.llm import ChatMessage, ChatResult, LLMError

Reply = str | LLMError | Callable[[list[ChatMessage]], str]


class FakeLLM:
    """Returns scripted replies in order; the last reply repeats."""

    def __init__(self, *replies: Reply, configured: bool = True, delay: float = 0.0) -> None:
        self._replies = list(replies)
        self._configured = configured
        self._delay = delay
        self.model = "fake-model"
        self.calls: list[list[ChatMessage]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def complete_json(self, messages: list[ChatMessage]) -> ChatResult:
        self.calls.append(messages)
        if self._delay:
            await asyncio.sleep(self._delay)
        reply = self._replies[min(len(self.calls), len(self._replies)) - 1]
        if isinstance(reply, LLMError):
            raise reply
        content = reply(messages) if callable(reply) else reply
        return ChatResult(content=content, model="fake-model-served", duration_ms=1.0)
