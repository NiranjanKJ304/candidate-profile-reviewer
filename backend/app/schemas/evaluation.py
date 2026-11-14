"""Candidate-JD evaluation schemas (Phase 5).

Flow: deterministic evidence bundle (``EvidenceItem``) + deterministic requirement
matching (``RequirementMatch``) -> LLM verdicts (``LLMAssessment``, strictly
validated) -> deterministic score (``ScoreBreakdown``) -> stored ``EvaluationResult``.

Rules encoded here:

* every evidence item carries its source and trust level (platform-collected,
  resume claim, self-reported);
* the LLM must cite evidence ids; ids are checked against the bundle;
* ``insufficient_evidence`` is distinct from ``not_met`` and never becomes zero
  silently: the score is ``null`` when too little can be judged;
* the overall score is computed by the backend, never by the LLM.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.enums import EvaluationErrorCode, EvaluationStatus, EvidenceTrust, Verdict

RESULT_SCHEMA_VERSION = 1

EvidenceSource = Literal["resume", "github", "codeforces", "leetcode"]
RequirementKind = Literal["required", "preferred"]
RequirementType = Literal["skill", "experience", "education", "qualification"]
BaselineStatus = Literal[
    "platform_evidence",  # at least one platform-collected item mentions it
    "resume_claim_only",
    "self_reported_only",
    "no_evidence",
    "not_applicable",  # non-skill requirement: judged by the LLM from the evidence
]
FitLabel = Literal["strong", "moderate", "weak", "insufficient_evidence"]

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^[RGCL]\d{1,4}$")]
RequirementId = Annotated[str, StringConstraints(pattern=r"^Q\d{1,4}$")]


# --------------------------------------------------------------------------- evidence
class EvidenceItem(BaseModel):
    id: EvidenceId = Field(description="R* resume, G* GitHub, C* Codeforces, L* LeetCode")
    source: EvidenceSource
    trust: EvidenceTrust
    kind: str = Field(max_length=40)
    text: str = Field(max_length=800, description="Identity-redacted description")
    skills: list[str] = Field(default_factory=list, description="Normalised skill keys")
    source_url: str | None = None
    sent_to_llm: bool = Field(
        default=True,
        description="False when left out to fit the LLM request limit (cannot back a verdict)",
    )


class SourceStatus(BaseModel):
    source: EvidenceSource
    status: Literal["used", "not_linked", "not_collected", "unavailable"]
    note: str | None = None
    collected_at: datetime | None = None


class ExperienceEstimate(BaseModel):
    years: float | None = Field(
        description="Non-overlapping professional experience; null if unknown"
    )
    entries_used: int
    entries_unparsed: int
    method: str


# --------------------------------------------------------------------------- requirements
class RequirementMatch(BaseModel):
    """A JD requirement with its deterministic evidence baseline."""

    id: RequirementId
    kind: RequirementKind
    type: RequirementType
    text: str
    skill_key: str | None = None
    baseline_status: BaselineStatus
    baseline_evidence_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- LLM output
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LLMRequirementVerdict(_Strict):
    requirement_id: RequirementId
    verdict: Verdict
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)
    rationale: Text


class LLMPoint(_Strict):
    text: Text
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)


class LLMGap(_Strict):
    text: Text
    requirement_ids: list[RequirementId] = Field(default_factory=list, max_length=20)


class LLMAssessment(_Strict):
    """The only shape accepted from the LLM."""

    requirements: list[LLMRequirementVerdict] = Field(max_length=200)
    strengths: list[LLMPoint] = Field(default_factory=list, max_length=10)
    gaps: list[LLMGap] = Field(default_factory=list, max_length=10)
    discrepancies: list[LLMPoint] = Field(default_factory=list, max_length=10)
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]


# --------------------------------------------------------------------------- stored result
class AssessedRequirement(RequirementMatch):
    verdict: Verdict
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str
    weight: float
    credit: float = Field(description="Points awarded (weight x verdict points x trust factor)")
    adjusted: str | None = Field(
        default=None, description="Why the backend changed the model's verdict, if it did"
    )


class ScoreBreakdown(BaseModel):
    overall: float | None = Field(description="0-100; null when too little could be judged")
    fit_label: FitLabel
    coverage: float = Field(description="Share of requirement weight with a determinable verdict")
    required_met: int
    required_total: int
    preferred_met: int
    preferred_total: int
    unmet_required: list[str] = Field(default_factory=list, description="Requirement ids")
    unevidenced_required: list[str] = Field(
        default_factory=list,
        description="Required requirement ids with insufficient evidence (unproven, not unmet)",
    )
    method: str


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = RESULT_SCHEMA_VERSION
    prompt_version: str
    jd_version: Literal["reviewed", "extracted"]
    job_title: str | None
    resume_id: uuid.UUID | None
    sources: list[SourceStatus]
    evidence: list[EvidenceItem]
    experience: ExperienceEstimate
    requirements: list[AssessedRequirement]
    score: ScoreBreakdown
    strengths: list[LLMPoint]
    gaps: list[LLMGap]
    discrepancies: list[LLMPoint]
    summary: str
    privacy: dict[str, int | bool | str]
    decision_support_notice: str


# --------------------------------------------------------------------------- API
class EvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: uuid.UUID
    job_description_id: uuid.UUID
    resume_id: uuid.UUID | None = Field(
        default=None, description="Defaults to the candidate's most recent parsed resume"
    )


class EvaluationSummary(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_description_id: uuid.UUID
    resume_id: uuid.UUID | None
    status: EvaluationStatus
    overall_score: float | None = Field(
        description="0-100; null until scored, never conflated with 0"
    )
    fit_label: FitLabel | None
    job_title: str | None
    model_name: str | None
    error_code: EvaluationErrorCode | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class EvaluationResponse(EvaluationSummary):
    warnings: list[str]
    result: EvaluationResult | None


class EvaluationPage(BaseModel):
    items: list[EvaluationSummary]
    total: int
    limit: int
    offset: int
