"""Evaluation prompt budget: trimming order, essentials kept, retry size, HTTP 413 handling."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.enums import EvaluationErrorCode, EvidenceTrust
from app.integrations.llm import ChatCompletionClient, ChatMessage, LLMResponseError
from app.schemas.evaluation import EvidenceItem, ExperienceEstimate, RequirementMatch, SourceStatus
from app.services.evaluation.assessment import EvaluationAssessor, EvaluationError
from app.services.evaluation.budget import (
    essential_references,
    estimate_tokens,
    fit_to_budget,
)
from app.services.evaluation.matching import build_requirements, match
from tests import evaluation_fakes as ef
from tests.llm_fakes import FakeLLM

EXPERIENCE = ExperienceEstimate(years=5.0, entries_used=1, entries_unparsed=0, method="m")
SOURCES = [SourceStatus(source="resume", status="used")]


def _item(i: int, kind: str, text: str, skills: list[str] | None = None) -> EvidenceItem:
    prefix = "G" if kind in ("repository", "language", "activity") else "R"
    return EvidenceItem(
        id=f"{prefix}{i}", source="github" if prefix == "G" else "resume",
        trust=EvidenceTrust.PLATFORM_COLLECTED if prefix == "G" else EvidenceTrust.RESUME_CLAIM,
        kind=kind, text=text, skills=skills or [],
    )  # fmt: skip


def _evidence() -> list[EvidenceItem]:
    items = [
        _item(1, "skill", "Lists skill: Python", ["python"]),
        _item(2, "work_experience", "Role: Backend Engineer; " + "built services. " * 35),
        _item(3, "education", "Education: B.Tech; field: Computer Science"),
    ]
    items += [_item(10 + i, "achievement", "Achievement: " + "award " * 50) for i in range(10)]
    items += [
        _item(30 + i, "repository", f"GitHub repository 'repo{i}' " + "x" * 400) for i in range(15)
    ]
    return items


def _build(items: list[EvidenceItem]) -> list[ChatMessage]:
    return [ChatMessage("user", json.dumps([i.model_dump(mode="json") for i in items]))]


def _requirements(evidence: list[EvidenceItem]) -> list[RequirementMatch]:
    reqs, _ = build_requirements(ef.parsed_jd())
    return match(reqs, evidence)


class TestFitToBudget:
    def test_small_prompt_is_untouched(self) -> None:
        ev = _evidence()[:3]
        fitted = fit_to_budget(_build, ev, _requirements(ev), 100_000)
        assert fitted is not None
        assert fitted.evidence == ev
        assert fitted.omitted_ids == []
        assert fitted.shortened == 0

    def test_trims_low_priority_first_and_keeps_essentials(self) -> None:
        ev = _evidence()
        reqs = _requirements(ev)
        full = estimate_tokens(_build(ev))
        fitted = fit_to_budget(_build, ev, reqs, full // 3)
        assert fitted is not None
        assert fitted.estimated_tokens <= full // 3
        kept = {i.id for i in fitted.evidence}
        assert {"R1", "R2", "R3"} <= kept  # referenced skill, work experience, education
        dropped_kinds = {i.kind for i in ev if i.id in fitted.omitted_ids}
        assert "achievement" in dropped_kinds
        # achievements are dropped before repositories
        first_repo = next((n for n, i in enumerate(fitted.omitted_ids) if i.startswith("G")), None)
        last_ach = max(n for n, i in enumerate(fitted.omitted_ids) if i.startswith("R1"))
        assert first_repo is None or first_repo > last_ach
        assert fitted.shortened >= 1  # the ~580-character work-experience text
        assert all(len(i.text) <= 300 for i in fitted.evidence)

    def test_impossible_budget_returns_none(self) -> None:
        ev = _evidence()
        assert fit_to_budget(_build, ev, _requirements(ev), 10) is None

    def test_estimate(self) -> None:
        assert estimate_tokens([ChatMessage("user", "x" * 360)]) == 100

    def test_only_best_three_references_per_requirement_are_essential(self) -> None:
        resume = [_item(i, "skill", f"Python claim {i}", ["python"]) for i in range(1, 4)]
        repos = [_item(40 + i, "repository", f"Python repo {i}", ["python"]) for i in range(5)]
        ev = resume + repos
        reqs = _requirements(ev)
        keep = essential_references(reqs, ev)
        python = next(r for r in reqs if r.text == "Python")
        assert len(python.baseline_evidence_ids) == 8
        assert {"G40", "G41", "G42"} <= keep  # platform data preferred
        assert not ({"R1", "R2", "R3", "G43", "G44"} & keep)


class TestAssessorBudget:
    async def test_omitted_evidence_cannot_back_a_verdict(self) -> None:
        ev = _evidence()
        reqs = _requirements(ev)

        # The model "cites" an achievement that was not sent.
        def reply(messages: list[ChatMessage]) -> str:
            payload = ef.request_payload(messages)
            sent = {e["id"] for e in payload["evidence"]}
            assert "R10" not in sent
            verdicts = [{"requirement_id": r["id"], "verdict": "met", "evidence_ids": ["R10"],
                         "rationale": "r"} for r in payload["requirements"]]  # fmt: skip
            return json.dumps({"requirements": verdicts, "strengths": [], "gaps": [],
                               "discrepancies": [], "summary": "s"})  # fmt: skip

        llm = FakeLLM(reply)
        out = await EvaluationAssessor(llm, max_attempts=1, max_prompt_tokens=2500).assess(
            ef.parsed_jd(),
            reqs,
            ev,
            SOURCES,
            EXPERIENCE,
        )
        assert "R10" not in out.sent_evidence_ids
        assert all(r.verdict.value == "insufficient_evidence" for r in out.requirements)
        assert out.warnings[0].startswith("To stay within the LLM provider's request limit")
        sent_chars = sum(len(m.content) for m in llm.calls[0])
        assert sent_chars / 3.6 <= 2500

    async def test_too_large_even_after_trimming(self) -> None:
        llm = FakeLLM("{}")
        with pytest.raises(EvaluationError) as info:
            await EvaluationAssessor(llm, max_attempts=1, max_prompt_tokens=1000).assess(
                ef.parsed_jd(),
                _requirements(_evidence()),
                _evidence(),
                SOURCES,
                EXPERIENCE,
            )
        assert info.value.code is EvaluationErrorCode.LLM_ERROR
        assert "LLM_EVALUATION_MAX_PROMPT_TOKENS" in info.value.message
        assert llm.calls == []  # nothing sent that would be rejected

    async def test_corrective_retry_does_not_resend_the_previous_reply(self) -> None:
        ev = _evidence()[:3]
        reqs = _requirements(ev)
        first = {
            "requirement_id": reqs[0].id,
            "verdict": "met",
            "evidence_ids": ["R1"],
            "rationale": "r",
        }
        partial = json.dumps({"requirements": [first], "summary": "s"})
        llm = FakeLLM(partial, ef.auto_reply)
        await EvaluationAssessor(llm, max_attempts=1).assess(
            ef.parsed_jd(),
            reqs,
            ev,
            SOURCES,
            EXPERIENCE,
        )
        assert len(llm.calls) == 2
        retry = llm.calls[1]
        assert [m.role for m in retry] == ["system", "user", "user"]
        assert "from scratch" in retry[-1].content
        assert sum(len(m.content) for m in retry) < sum(len(m.content) for m in llm.calls[0]) + 500


class TestHttp413:
    async def test_message_has_numbers_but_no_account_details(self) -> None:
        message = (
            "Request too large for model `x` in organization `org_secret123` service tier "
            "`on_demand` on tokens per minute (TPM): Limit 8000, Requested 9563, please reduce"
        )
        body = {"error": {"message": message, "code": "rate_limit_exceeded"}}
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(413, json=body))
        )
        client = ChatCompletionClient(base_url="https://api.example.test/v1", api_key="k-123",
                                      model="m", timeout_s=5, http_client=http)  # fmt: skip
        with pytest.raises(LLMResponseError) as info:
            await client.complete_json([ChatMessage("user", "x")])
        message = str(info.value)
        assert "HTTP 413" in message
        assert "limit 8000, requested 9563" in message
        assert "org_secret123" not in message
        assert "k-123" not in message

    async def test_message_without_numbers(self) -> None:
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(413, text="too big"))
        )
        client = ChatCompletionClient(base_url="https://api.example.test/v1", api_key="k",
                                      model="m", timeout_s=5, http_client=http)  # fmt: skip
        with pytest.raises(LLMResponseError, match="too large for the provider's limit"):
            await client.complete_json([ChatMessage("user", "x")])
