"""Evaluation pipeline: load sources -> evidence bundle -> requirements -> LLM -> score.

Runs as a background task after HTTP 202 (same pattern as resume / JD processing),
records every outcome on the ``evaluations`` row and never raises.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import EvaluationErrorCode, EvaluationStatus, ParseStatus, Platform
from app.models import Candidate, DeveloperProfile, Evaluation, JobDescription, Resume
from app.schemas.codeforces import CodeforcesSnapshot
from app.schemas.evaluation import EvaluationResult
from app.schemas.github import GitHubSnapshot
from app.schemas.job_description_parsing import ParsedJobDescription
from app.schemas.leetcode import LeetCodeEvidence
from app.schemas.resume_parsing import ParsedResume
from app.services.evaluation.assessment import PROMPT_VERSION, EvaluationAssessor, EvaluationError
from app.services.evaluation.evidence import PlatformInput, build_bundle
from app.services.evaluation.matching import build_requirements, match
from app.services.evaluation.scoring import score

logger = logging.getLogger("app.evaluation.processor")

NOTICE = (
    "Decision support only: this assessment is generated from the evidence listed and may be "
    "incomplete or wrong. It is not a hiring decision. Unverified and self-reported evidence "
    "is labelled; missing evidence is not evidence of missing skills."
)


def jd_requirements_source(jd: JobDescription) -> tuple[ParsedJobDescription, str] | None:
    """The reviewed (human-corrected) version when present, otherwise the extraction."""
    for data, label in ((jd.reviewed_data, "reviewed"), (jd.analysis, "extracted")):
        if data:
            try:
                return ParsedJobDescription.model_validate(data), label
            except ValidationError:
                continue
    return None


def _load[ModelT: BaseModel](schema: type[ModelT], data: dict[str, Any] | None) -> ModelT | None:
    if not data:
        return None
    try:
        return schema.model_validate(data)
    except ValidationError:
        return None


class EvaluationProcessor:
    def __init__(
        self, *, session_factory: async_sessionmaker[AsyncSession], assessor: EvaluationAssessor
    ) -> None:
        self._sessions = session_factory
        self._assessor = assessor

    @property
    def configured(self) -> bool:
        return self._assessor.configured

    async def process(self, evaluation_id: uuid.UUID) -> None:
        """Run one evaluation. Never raises: failures are recorded or logged."""
        started = time.perf_counter()
        try:
            await self._run(evaluation_id)
        except Exception:
            logger.exception("evaluation crashed", extra={"evaluation_id": str(evaluation_id)})
        finally:
            logger.info(
                "evaluation finished",
                extra={
                    "evaluation_id": str(evaluation_id),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    async def _run(self, evaluation_id: uuid.UUID) -> None:
        async with self._sessions() as session:
            ev = await session.get(Evaluation, evaluation_id)
            if ev is None:
                return
            ev.status = EvaluationStatus.RUNNING
            ev.error_code = None
            ev.error_message = None
            await session.commit()
            try:
                result, model, warnings = await self._evaluate(session, ev)
            except EvaluationError as exc:
                await self._fail(session, ev, exc.code, exc.message)
                return
            except SQLAlchemyError:
                raise
            except Exception as exc:
                logger.exception(
                    "unexpected evaluation error", extra={"evaluation_id": str(evaluation_id)}
                )
                await self._fail(
                    session,
                    ev,
                    EvaluationErrorCode.INTERNAL_ERROR,
                    f"Unexpected error ({type(exc).__name__})",
                )
                return
            ev.result = result.model_dump(mode="json")
            ev.overall_score = (
                Decimal(str(result.score.overall)) if result.score.overall is not None else None
            )
            ev.model_name = model[:100]
            ev.warnings = warnings
            ev.status = EvaluationStatus.COMPLETED
            ev.completed_at = datetime.now(UTC)
            await session.commit()
            logger.info(
                "evaluation completed",
                extra={
                    "evaluation_id": str(evaluation_id),
                    "requirements": len(result.requirements),
                    "evidence_items": len(result.evidence),
                    "fit": result.score.fit_label,
                    "warnings": len(warnings),
                },
            )

    async def _evaluate(
        self, session: AsyncSession, ev: Evaluation
    ) -> tuple[EvaluationResult, str, list[str]]:
        candidate = await session.get(Candidate, ev.candidate_id)
        jd = await session.get(JobDescription, ev.job_description_id)
        if candidate is None or jd is None:
            raise EvaluationError(
                EvaluationErrorCode.SOURCE_MISSING,
                "The candidate or job description no longer exists",
            )
        parsed = jd_requirements_source(jd) if jd.parse_status == ParseStatus.COMPLETED else None
        if parsed is None:
            raise EvaluationError(
                EvaluationErrorCode.JD_NOT_PARSED,
                "The job description has not been parsed successfully",
            )
        jd_data, jd_version = parsed
        warnings: list[str] = []

        resume_data: ParsedResume | None = None
        if ev.resume_id is not None:
            resume = await session.get(Resume, ev.resume_id)
            if resume is None or resume.candidate_id != candidate.id:
                warnings.append("The selected resume no longer exists; evaluated without it")
            else:
                resume_data = _load(ParsedResume, resume.parsed_data)
                if resume_data is None:
                    warnings.append(
                        "The selected resume has no usable parsed data; evaluated without it"
                    )

        profiles = (
            await session.scalars(
                select(DeveloperProfile).where(DeveloperProfile.candidate_id == candidate.id)
            )
        ).all()
        by_platform = {p.platform: p for p in profiles}
        status: dict[str, PlatformInput] = {}
        github = codeforces = None
        leetcode = None
        identifiers = [p.username for p in profiles]
        for platform, key in ((Platform.GITHUB, "github"), (Platform.CODEFORCES, "codeforces")):
            profile = by_platform.get(platform)
            if profile is None:
                status[key] = PlatformInput("not_linked", "No profile linked")
                continue
            schema: type[BaseModel] = (
                GitHubSnapshot if platform == Platform.GITHUB else CodeforcesSnapshot
            )
            snap = _load(schema, profile.collected_data)
            if snap is None:
                status[key] = PlatformInput(
                    "not_collected", "Linked, but no data has been collected yet"
                )
                continue
            status[key] = PlatformInput("used", None, profile.collected_at)
            if platform == Platform.GITHUB:
                github = snap
            else:
                codeforces = snap
        lc = by_platform.get(Platform.LEETCODE)
        if lc is None:
            status["leetcode"] = PlatformInput("not_linked", "No profile linked")
        else:
            leetcode = _load(LeetCodeEvidence, lc.self_reported_data)
            status["leetcode"] = (
                PlatformInput("used", "Self-reported, not verified", lc.self_reported_at)
                if leetcode is not None
                else PlatformInput("not_collected", "Linked, but no self-reported evidence entered")
            )

        bundle = build_bundle(
            resume=resume_data,
            github=github,  # type: ignore[arg-type]
            codeforces=codeforces,  # type: ignore[arg-type]
            leetcode=leetcode,
            platform_status=status,
            identifiers=identifiers,
        )
        if not bundle.items:
            raise EvaluationError(
                EvaluationErrorCode.NO_EVIDENCE,
                "No evidence is available: parse a resume or collect a developer profile first",
            )
        requirements, req_warnings = build_requirements(jd_data)
        warnings.extend(req_warnings)
        if not requirements:
            raise EvaluationError(
                EvaluationErrorCode.JD_HAS_NO_REQUIREMENTS,
                "The job description has no skills or requirements to evaluate",
            )
        matched = match(requirements, bundle.items)
        outcome = await self._assessor.assess(
            jd_data, matched, bundle.items, bundle.sources, bundle.experience
        )
        warnings.extend(outcome.warnings)
        if resume_data is None:
            warnings.append(
                "No parsed resume was used; the assessment relies on platform evidence only"
            )

        breakdown = score(outcome.requirements)
        result = EvaluationResult(
            prompt_version=PROMPT_VERSION,
            jd_version=jd_version,  # type: ignore[arg-type]
            job_title=jd_data.job_title or jd.title,
            resume_id=ev.resume_id if resume_data is not None else None,
            sources=bundle.sources,
            evidence=bundle.items,
            experience=bundle.experience,
            requirements=outcome.requirements,
            score=breakdown,
            strengths=outcome.strengths,
            gaps=outcome.gaps,
            discrepancies=outcome.discrepancies,
            summary=outcome.summary,
            privacy={
                "identity_removed": True,
                "source_urls_sent_to_llm": False,
                **{f"redacted_{k}": v for k, v in bundle.redactions.items()},
            },
            decision_support_notice=NOTICE,
        )
        return result, outcome.model, warnings

    @staticmethod
    async def _fail(
        session: AsyncSession, ev: Evaluation, code: EvaluationErrorCode, message: str
    ) -> None:
        ev.status = EvaluationStatus.FAILED
        ev.error_code = code
        ev.error_message = message[:2000]
        ev.completed_at = datetime.now(UTC)
        logger.warning(
            "evaluation failed", extra={"evaluation_id": str(ev.id), "error_code": code.value}
        )
        try:
            await session.commit()
        except SQLAlchemyError:
            logger.exception(
                "could not record evaluation failure", extra={"evaluation_id": str(ev.id)}
            )
