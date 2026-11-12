"""Shared "LLM -> validated Pydantic model" loop used by resume and JD structuring.

Responsibilities (document-type independent):

* call the configured chat client in JSON mode;
* parse the reply (tolerating a Markdown code fence) and validate it against a
  Pydantic schema - nothing unvalidated is ever returned;
* on invalid output, send one corrective follow-up per remaining attempt;
* map transport errors (timeout, auth, rate limit, ...) to processing error codes.

Document-specific concerns - prompts, delimiters, grounding - stay with the callers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.core.enums import ResumeErrorCode
from app.integrations.llm import ChatMessage, ChatResult, LLMError
from app.services.resume.errors import ProcessingError

logger = logging.getLogger("app.llm_json")


class SupportsCompleteJson(Protocol):
    model: str

    @property
    def configured(self) -> bool: ...

    async def complete_json(self, messages: list[ChatMessage]) -> ChatResult: ...


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_llm_json(content: str) -> object:
    """Parse the model output, tolerating a Markdown code fence around the JSON."""
    match = _FENCE.match(content)
    if match:
        content = match.group(1)
    return json.loads(content)


def validation_summary(exc: ValidationError, limit: int = 8) -> str:
    parts = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def schema_json(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), separators=(",", ":"))


def prepare_document_text(
    text: str, *, max_chars: int, begin: str, end: str, label: str
) -> tuple[str, list[str]]:
    """Truncate to ``max_chars`` and neutralise delimiter spoofing inside the document."""
    warnings: list[str] = []
    if len(text) > max_chars:
        text = text[:max_chars]
        warnings.append(f"{label} text was truncated to {max_chars} characters before parsing")
    return text.replace(begin, "[removed]").replace(end, "[removed]"), warnings


def require_configured(client: SupportsCompleteJson, what: str) -> None:
    if not client.configured:
        raise ProcessingError(
            ResumeErrorCode.LLM_NOT_CONFIGURED,
            "No LLM API key is configured (set GROQ_API_KEY in backend/.env), so the "
            f"{what} could not be structured",
        )


async def complete_validated[ModelT: BaseModel](
    client: SupportsCompleteJson,
    messages: list[ChatMessage],
    schema: type[ModelT],
    *,
    max_attempts: int,
    log_label: str,
) -> tuple[ModelT, str]:
    """Return ``(validated_model, served_model_name)`` or raise :class:`ProcessingError`."""
    base_messages = list(messages)
    current = list(messages)
    last_problem = "unknown"
    model = client.model
    for attempt in range(1, max_attempts + 1):
        try:
            result = await client.complete_json(current)
        except LLMError as exc:
            raise ProcessingError(ResumeErrorCode(exc.code), str(exc)) from exc
        model = result.model or model
        try:
            return schema.model_validate(parse_llm_json(result.content)), model
        except json.JSONDecodeError as exc:
            last_problem = f"response was not valid JSON ({exc.msg})"
        except ValidationError as exc:
            last_problem = f"response did not match the schema: {validation_summary(exc)}"

        logger.warning(
            "llm returned invalid json",
            extra={"document": log_label, "attempt": attempt, "problem": last_problem[:300]},
        )
        current = [
            *base_messages,
            ChatMessage("assistant", result.content[:20_000]),
            ChatMessage(
                "user",
                f"Your previous reply was invalid: {last_problem}. Reply again with ONLY a "
                "JSON object that matches the schema exactly. Use null or [] for missing "
                "information and do not add any keys.",
            ),
        ]

    raise ProcessingError(
        ResumeErrorCode.LLM_INVALID_RESPONSE,
        f"The LLM did not return valid structured data after {max_attempts} "
        f"attempt(s): {last_problem}"[:1000],
    )
