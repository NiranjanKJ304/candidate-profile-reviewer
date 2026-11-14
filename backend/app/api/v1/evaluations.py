"""Candidate-JD evaluation endpoints (Phase 5). The work runs in the background."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import EvaluationProcessorDep, SessionDep
from app.api.v1.candidates import load_candidate
from app.core.enums import EvaluationStatus, ParseStatus
from app.core.errors import error_response
from app.models import Evaluation, JobDescription, Resume
from app.schemas.common import ErrorResponse
from app.schemas.evaluation import (
    EvaluationCreate,
    EvaluationPage,
    EvaluationResponse,
    EvaluationResult,
    EvaluationSummary,
)
from app.services.evaluation.processor import jd_requirements_source

logger = logging.getLogger("app.api.evaluations")

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

STALE_AFTER = timedelta(minutes=10)
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse, "description": "LLM not configured or database unavailable"},
}


def _result(ev: Evaluation) -> EvaluationResult | None:
    if not ev.result:
        return None
    try:
        return EvaluationResult.model_validate(ev.result)
    except ValidationError:
        logger.warning(
            "stored evaluation result is not readable", extra={"evaluation_id": str(ev.id)}
        )
        return None


def _summary_fields(ev: Evaluation, result: EvaluationResult | None) -> dict[str, Any]:
    return {
        "id": ev.id,
        "candidate_id": ev.candidate_id,
        "job_description_id": ev.job_description_id,
        "resume_id": ev.resume_id,
        "status": ev.status,
        "overall_score": float(ev.overall_score) if ev.overall_score is not None else None,
        "fit_label": result.score.fit_label if result else None,
        "job_title": result.job_title if result else None,
        "model_name": ev.model_name,
        "error_code": ev.error_code,
        "error_message": ev.error_message,
        "created_at": ev.created_at,
        "completed_at": ev.completed_at,
    }


def to_summary(ev: Evaluation) -> EvaluationSummary:
    return EvaluationSummary(**_summary_fields(ev, _result(ev)))


def to_response(ev: Evaluation) -> EvaluationResponse:
    result = _result(ev)
    return EvaluationResponse(
        **_summary_fields(ev, result), warnings=list(ev.warnings or []), result=result
    )


async def _latest_parsed_resume(session: SessionDep, candidate_id: uuid.UUID) -> Resume | None:
    return await session.scalar(
        select(Resume)
        .where(Resume.candidate_id == candidate_id, Resume.parse_status == ParseStatus.COMPLETED)
        .order_by(Resume.parsed_at.desc().nulls_last(), Resume.created_at.desc())
        .limit(1)
    )


async def _get(session: SessionDep, evaluation_id: uuid.UUID) -> Evaluation:
    ev = await session.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation not found")
    return ev


def _busy(ev: Evaluation) -> bool:
    return (
        ev.status in (EvaluationStatus.PENDING, EvaluationStatus.RUNNING)
        and datetime.now(UTC) - ev.updated_at < STALE_AFTER
    )


@router.post(
    "",
    response_model=EvaluationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Evaluate a candidate against a job description (runs in the background)",
    responses=_ERRORS,
)
async def create_evaluation(
    payload: EvaluationCreate,
    background: BackgroundTasks,
    session: SessionDep,
    processor: EvaluationProcessorDep,
) -> Response | EvaluationResponse:
    if not processor.configured:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "llm_not_configured",
            "No LLM API key is configured (set GROQ_API_KEY in backend/.env)",
        )
    await load_candidate(session, payload.candidate_id)
    jd = await session.get(JobDescription, payload.job_description_id)
    if jd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job description not found")
    if jd.parse_status != ParseStatus.COMPLETED or jd_requirements_source(jd) is None:
        return error_response(
            status.HTTP_409_CONFLICT,
            "jd_not_parsed",
            "The job description has not been parsed successfully yet",
        )

    if payload.resume_id is not None:
        resume = await session.get(Resume, payload.resume_id)
        if resume is None or resume.candidate_id != payload.candidate_id:
            return error_response(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_resume",
                "The resume does not exist or belongs to another candidate",
            )
        if resume.parse_status != ParseStatus.COMPLETED:
            return error_response(
                status.HTTP_409_CONFLICT, "resume_not_parsed", "The resume has not been parsed yet"
            )
        resume_id: uuid.UUID | None = resume.id
    else:
        latest = await _latest_parsed_resume(session, payload.candidate_id)
        resume_id = latest.id if latest else None

    running = await session.scalar(
        select(Evaluation)
        .where(
            Evaluation.candidate_id == payload.candidate_id,
            Evaluation.job_description_id == payload.job_description_id,
            Evaluation.status.in_([EvaluationStatus.PENDING, EvaluationStatus.RUNNING]),
        )
        .limit(1)
    )
    if running is not None and _busy(running):
        return error_response(
            status.HTTP_409_CONFLICT,
            "evaluation_in_progress",
            "An evaluation of this candidate for this job description is already running",
            details=[{"evaluation_id": str(running.id)}],
        )

    ev = Evaluation(
        candidate_id=payload.candidate_id,
        job_description_id=payload.job_description_id,
        resume_id=resume_id,
        status=EvaluationStatus.PENDING,
    )
    session.add(ev)
    await session.commit()
    await session.refresh(ev)
    background.add_task(processor.process, ev.id)
    logger.info("evaluation requested", extra={"evaluation_id": str(ev.id)})
    return to_response(ev)


@router.get("", response_model=EvaluationPage, summary="List evaluations, newest first")
async def list_evaluations(
    session: SessionDep,
    candidate_id: uuid.UUID | None = None,
    job_description_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvaluationPage:
    filters = []
    if candidate_id is not None:
        filters.append(Evaluation.candidate_id == candidate_id)
    if job_description_id is not None:
        filters.append(Evaluation.job_description_id == job_description_id)
    total = await session.scalar(select(func.count()).select_from(Evaluation).where(*filters)) or 0
    rows = (
        await session.scalars(
            select(Evaluation)
            .where(*filters)
            .order_by(Evaluation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return EvaluationPage(
        items=[to_summary(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{evaluation_id}",
    response_model=EvaluationResponse,
    summary="Get an evaluation",
    responses=_ERRORS,
)
async def get_evaluation(
    evaluation_id: uuid.UUID, session: SessionDep, response: Response
) -> EvaluationResponse:
    response.headers["Cache-Control"] = "no-store"
    return to_response(await _get(session, evaluation_id))


@router.post(
    "/{evaluation_id}/rerun",
    response_model=EvaluationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the evaluation again with the current evidence",
    responses=_ERRORS,
)
async def rerun_evaluation(
    evaluation_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    processor: EvaluationProcessorDep,
) -> Response | EvaluationResponse:
    if not processor.configured:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "llm_not_configured",
            "No LLM API key is configured (set GROQ_API_KEY in backend/.env)",
        )
    ev = await _get(session, evaluation_id)
    if _busy(ev):
        return error_response(
            status.HTTP_409_CONFLICT, "evaluation_in_progress", "This evaluation is already running"
        )
    if ev.resume_id is None:
        latest = await _latest_parsed_resume(session, ev.candidate_id)
        ev.resume_id = latest.id if latest else None
    ev.status = EvaluationStatus.PENDING
    ev.error_code = None
    ev.error_message = None
    await session.commit()
    await session.refresh(ev)
    background.add_task(processor.process, ev.id)
    return to_response(ev)


@router.delete(
    "/{evaluation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an evaluation",
    responses=_ERRORS,
)
async def delete_evaluation(evaluation_id: uuid.UUID, session: SessionDep) -> Response:
    ev = await _get(session, evaluation_id)
    await session.delete(ev)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
