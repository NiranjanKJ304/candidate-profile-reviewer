"""Real LLM provider tests (resume + JD structuring) - run only when configured.

Set INTEGRATION_GROQ_API_KEY (scripts/integration-test.ps1 does this automatically
from backend/.env when GROQ_API_KEY is set). Optional: INTEGRATION_LLM_MODEL.
These tests call the real provider, cost tokens and depend on the network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.config import LLM_DEFAULT_BASE_URLS, LLM_DEFAULT_MODELS
from app.integrations.llm import ChatCompletionClient
from app.services.job_description.structuring import JobDescriptionStructurer
from app.services.resume.structuring import ResumeStructurer
from tests import jd_fixtures as jf
from tests import resume_fixtures as rf
from tests.conftest import integration_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def client() -> AsyncIterator[ChatCompletionClient]:
    key = integration_env("INTEGRATION_GROQ_API_KEY")
    if not key:
        pytest.skip("INTEGRATION_GROQ_API_KEY not set (real-provider test)")
    http = httpx.AsyncClient(timeout=90)
    yield ChatCompletionClient(
        base_url=LLM_DEFAULT_BASE_URLS["groq"],
        api_key=key,
        model=integration_env("INTEGRATION_LLM_MODEL") or LLM_DEFAULT_MODELS["groq"],
        timeout_s=90,
        http_client=http,
    )
    await http.aclose()


async def test_real_resume_structuring(client: ChatCompletionClient) -> None:
    result = await ResumeStructurer(client, max_attempts=2, max_input_chars=60_000).structure(
        rf.RESUME_TEXT
    )
    names = {s.name.lower() for s in result.resume.technical_skills}
    assert result.resume.personal_details.email == "jordan.sample@example.org"
    assert {"python", "fastapi", "postgresql"} <= names
    assert result.model


async def test_real_jd_structuring(client: ChatCompletionClient) -> None:
    result = await JobDescriptionStructurer(
        client, max_attempts=2, max_input_chars=60_000
    ).structure(jf.JD_TEXT)
    data = result.data
    required = {s.name.lower() for s in data.required_skills}
    preferred = {s.name.lower() for s in data.preferred_skills}
    assert "python" in required
    assert "postgresql" in required
    assert {"docker", "aws"} & preferred
    assert not (required & preferred)
    assert data.minimum_experience is not None
    assert data.minimum_experience.years_min == 5
    assert result.model


async def test_real_candidate_evaluation(client: ChatCompletionClient) -> None:
    """Phase 5: real LLM verdicts over an identity-redacted synthetic evidence bundle."""
    from app.core.enums import Verdict
    from app.services.evaluation.assessment import EvaluationAssessor
    from app.services.evaluation.evidence import PlatformInput, build_bundle
    from app.services.evaluation.matching import build_requirements, match
    from app.services.evaluation.scoring import score
    from tests import evaluation_fakes as ef
    from tests import github_fakes as gf

    bundle = build_bundle(
        resume=ef.parsed_resume(),
        github=await ef.github_snapshot(),
        codeforces=None,
        leetcode=None,
        platform_status={"github": PlatformInput("used")},
        identifiers=[gf.LOGIN],
    )
    jd = ef.parsed_jd()
    reqs, _ = build_requirements(jd)
    outcome = await EvaluationAssessor(client, max_attempts=2).assess(
        jd, match(reqs, bundle.items), bundle.items, bundle.sources, bundle.experience
    )
    verdicts = {r.text: r.verdict for r in outcome.requirements}
    assert len(outcome.requirements) == len(reqs)
    assert verdicts["Python"] in (Verdict.MET, Verdict.PARTIALLY_MET)  # resume + GitHub evidence
    assert verdicts["Rust"] is not Verdict.MET  # no Rust evidence anywhere
    valid = {i.id for i in bundle.items}
    assert all(set(r.evidence_ids) <= valid for r in outcome.requirements)
    assert outcome.summary
    breakdown = score(outcome.requirements)
    assert breakdown.fit_label in ("strong", "moderate", "weak", "insufficient_evidence")
