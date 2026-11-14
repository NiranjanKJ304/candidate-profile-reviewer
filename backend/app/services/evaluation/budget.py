"""Keep the evaluation prompt inside the LLM provider's request limit.

Groq's free tier allows 8,000 tokens per minute for ``openai/gpt-oss-120b``, and a
single request larger than that (prompt + room for the answer) is rejected with
HTTP 413. A real evaluation (23 requirements, 61 evidence items) measured about
6,500 prompt tokens, too close to the limit. This module trims the *evidence*
deterministically until the estimated prompt fits the configured budget:

1. evidence texts longer than 300 characters are shortened;
2. lower-priority items are left out, least relevant first (achievements and
   summaries, activity, topics, unreferenced skills and repositories, languages,
   projects). The best three items each requirement's baseline points to
   (platform data first, then resume claims), and work experience,
   internships, education, certifications and competitive-programming summaries,
   are kept;
3. remaining long texts are shortened to 160 characters.

Requirements are never dropped. If even the minimum does not fit, the caller gets
``None`` and reports a clear error instead of sending a request that will fail.
Token counts are estimated (about 3.6 characters per token was measured on Groq for
this JSON; the 4,500-token default budget leaves room for Groq's answer allowance).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.integrations.llm import ChatMessage
from app.schemas.evaluation import EvidenceItem, RequirementMatch

CHARS_PER_TOKEN = 3.6  # measured on Groq: 23,489 characters of this JSON = 6,491 tokens
ESSENTIAL_REFS_PER_REQUIREMENT = 3
FIRST_TEXT_CAP = 300
SECOND_TEXT_CAP = 160
CORE_KINDS = frozenset(
    {"work_experience", "internship", "education", "certification", "rating", "problem_solving",
     "solved", "contest"}
)  # fmt: skip
# Higher number = dropped earlier.
_DROP_ORDER = {
    "achievement": 8,
    "summary": 8,
    "activity": 7,
    "contributions": 7,
    "topic": 6,
    "problem_topic": 6,
    "skill": 5,
    "repository": 4,
    "language": 3,
    "project": 2,
}


@dataclass
class FittedPrompt:
    messages: list[ChatMessage]
    evidence: list[EvidenceItem]  # what was sent (texts possibly shortened)
    omitted_ids: list[str]
    shortened: int
    estimated_tokens: int


_TRUST_RANK = {"platform_collected": 0, "resume_claim": 1, "self_reported": 2}


def essential_references(
    requirements: list[RequirementMatch], evidence: list[EvidenceItem]
) -> set[str]:
    """The best few baseline items per requirement (platform data first, then resume)."""
    by_id = {e.id: e for e in evidence}
    keep: set[str] = set()
    for req in requirements:
        refs = [by_id[i] for i in req.baseline_evidence_ids if i in by_id]
        refs.sort(key=lambda e: _TRUST_RANK.get(e.trust.value, 3))
        keep.update(e.id for e in refs[:ESSENTIAL_REFS_PER_REQUIREMENT])
    return keep


def estimate_tokens(messages: list[ChatMessage]) -> int:
    return math.ceil(sum(len(m.content) for m in messages) / CHARS_PER_TOKEN)


def _shorten(items: list[EvidenceItem], cap: int) -> tuple[list[EvidenceItem], int]:
    out: list[EvidenceItem] = []
    changed = 0
    for item in items:
        if len(item.text) > cap:
            out.append(item.model_copy(update={"text": item.text[: cap - 3].rstrip() + "..."}))
            changed += 1
        else:
            out.append(item)
    return out, changed


def fit_to_budget(
    build: Callable[[list[EvidenceItem]], list[ChatMessage]],
    evidence: list[EvidenceItem],
    requirements: list[RequirementMatch],
    budget_tokens: int,
) -> FittedPrompt | None:
    """Messages that fit ``budget_tokens``, or None if essentials alone do not fit."""
    messages = build(evidence)
    if estimate_tokens(messages) <= budget_tokens:
        return FittedPrompt(messages, evidence, [], 0, estimate_tokens(messages))

    referenced = essential_references(requirements, evidence)
    current, shortened = _shorten(evidence, FIRST_TEXT_CAP)
    messages = build(current)

    droppable = sorted(
        (item for item in current if item.id not in referenced and item.kind not in CORE_KINDS),
        # least relevant first; within a kind, later items (older / less prominent) first
        key=lambda i: (-_DROP_ORDER.get(i.kind, 1), -current.index(i)),
    )
    omitted: list[str] = []
    for item in droppable:
        if estimate_tokens(messages) <= budget_tokens:
            break
        current = [c for c in current if c.id != item.id]
        omitted.append(item.id)
        messages = build(current)

    if estimate_tokens(messages) > budget_tokens:
        current, more = _shorten(current, SECOND_TEXT_CAP)
        shortened += more
        messages = build(current)
    if estimate_tokens(messages) > budget_tokens:
        return None
    return FittedPrompt(messages, current, omitted, shortened, estimate_tokens(messages))
