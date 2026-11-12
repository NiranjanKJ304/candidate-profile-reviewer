"""Candidate endpoints (minimal: a resume must belong to a candidate)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep
from app.models import Candidate, Resume
from app.schemas.common import ErrorResponse
from app.schemas.domain import CandidateCreate, CandidateRead, ResumeRead

router = APIRouter(prefix="/candidates", tags=["candidates"])


class CandidatePage(BaseModel):
    items: list[CandidateRead]
    total: int
    limit: int
    offset: int


async def load_candidate(session: SessionDep, candidate_id: uuid.UUID) -> Candidate:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    return candidate


@router.post(
    "",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a candidate",
    responses={409: {"model": ErrorResponse, "description": "E-mail already registered"}},
)
async def create_candidate(
    payload: CandidateCreate, session: SessionDep, response: Response
) -> Candidate:
    candidate = Candidate(**payload.model_dump())
    session.add(candidate)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A candidate with this e-mail already exists"
        ) from exc
    await session.refresh(candidate)
    response.headers["Location"] = f"/api/v1/candidates/{candidate.id}"
    return candidate


@router.get("", response_model=CandidatePage, summary="List candidates (newest first)")
async def list_candidates(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CandidatePage:
    total = await session.scalar(select(func.count()).select_from(Candidate)) or 0
    rows = await session.execute(
        select(Candidate).order_by(Candidate.created_at.desc()).limit(limit).offset(offset)
    )
    return CandidatePage(
        items=[CandidateRead.model_validate(c) for c in rows.scalars()],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{candidate_id}",
    response_model=CandidateRead,
    summary="Get a candidate",
    responses={404: {"model": ErrorResponse}},
)
async def get_candidate(candidate_id: uuid.UUID, session: SessionDep) -> Candidate:
    return await load_candidate(session, candidate_id)


@router.get(
    "/{candidate_id}/resumes",
    response_model=list[ResumeRead],
    summary="List a candidate's resumes (newest first)",
    responses={404: {"model": ErrorResponse}},
)
async def list_candidate_resumes(candidate_id: uuid.UUID, session: SessionDep) -> list[Resume]:
    await load_candidate(session, candidate_id)
    result = await session.execute(
        select(Resume).where(Resume.candidate_id == candidate_id).order_by(Resume.created_at.desc())
    )
    return list(result.scalars())
