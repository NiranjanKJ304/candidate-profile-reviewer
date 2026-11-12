"""LeetCode endpoints (Phase 4): profile association plus manual, self-reported evidence.

LeetCode offers no official or permitted data API, and its Terms of Service forbid
scraping (see ``app.services.leetcode.feasibility``). These endpoints therefore
**never contact LeetCode**. There is no sync or retry. Evidence is entered manually
and always labelled self-reported and not verified.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep, SettingsDep
from app.api.v1.candidates import load_candidate
from app.core.config import Settings
from app.core.enums import CollectionStatus, OwnershipStatus, Platform, SyncStatus
from app.core.errors import error_response
from app.models import DeveloperProfile
from app.schemas.common import ErrorResponse
from app.schemas.leetcode import (
    EvidenceFreshness,
    LeetCodeEvidence,
    LeetCodeEvidenceInput,
    LeetCodeIntegrationStatus,
    LeetCodeProfileCreate,
    LeetCodeProfileResponse,
    PlatformData,
)
from app.services.leetcode.evidence import build_evidence, evidence_age_days
from app.services.leetcode.feasibility import FINDINGS, INVESTIGATED_ON, SOURCES
from app.services.leetcode.validation import (
    InvalidLeetCodeInputError,
    parse_leetcode_profile,
    profile_url,
)

logger = logging.getLogger("app.api.leetcode")

router = APIRouter(tags=["leetcode"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse, "description": "Integration disabled or database unavailable"},
}
PLATFORM_DATA = PlatformData(
    reason=(
        "LeetCode offers no official or permitted data API and its Terms of Service forbid "
        "scraping, so no data is collected from LeetCode. Only self-reported evidence is shown."
    ),
    sources=list(SOURCES),
)
BASE_WARNINGS = (
    "The username was not checked against LeetCode (no permitted API)",
    "Ownership not verified: a submitted profile is not proof that it belongs to the candidate",
)


def _disabled() -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "integration_disabled",
        "The LeetCode integration is disabled. Add leetcode to ENABLED_PLATFORMS to enable it.",
    )


def _evidence(profile: DeveloperProfile) -> LeetCodeEvidence | None:
    if not profile.self_reported_data:
        return None
    try:
        return LeetCodeEvidence.model_validate(profile.self_reported_data)
    except ValidationError:
        logger.warning(
            "stored leetcode evidence is not readable", extra={"profile_id": str(profile.id)}
        )
        return None


def to_response(profile: DeveloperProfile, settings: Settings) -> LeetCodeProfileResponse:
    evidence = _evidence(profile)
    warnings = list(BASE_WARNINGS)
    stale_after = settings.leetcode_evidence_stale_after_days
    if evidence is None:
        if profile.self_reported_data:
            warnings.append("Stored self-reported evidence could not be read")
        freshness = EvidenceFreshness(
            submitted_at=None, as_of=None, age_days=None, stale=True, stale_after_days=stale_after
        )
    else:
        warnings.extend(evidence.warnings)
        age = evidence_age_days(evidence)
        freshness = EvidenceFreshness(
            submitted_at=evidence.submitted_at,
            as_of=evidence.as_of,
            age_days=age,
            stale=age > stale_after,
            stale_after_days=stale_after,
        )
    return LeetCodeProfileResponse(
        id=profile.id,
        candidate_id=profile.candidate_id,
        username=profile.username,
        profile_url=profile.profile_url,
        ownership_status=profile.ownership_status,
        collection_status=profile.collection_status,
        sync_status=profile.sync_status,
        platform_data=PLATFORM_DATA,
        evidence=evidence,
        freshness=freshness,
        warnings=warnings,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def _get_profile(session: SessionDep, candidate_id: uuid.UUID) -> DeveloperProfile:
    await load_candidate(session, candidate_id)
    profile = await session.scalar(
        select(DeveloperProfile).where(
            DeveloperProfile.candidate_id == candidate_id,
            DeveloperProfile.platform == Platform.LEETCODE,
        )
    )
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This candidate has no LeetCode profile")
    return profile


@router.post(
    "/candidates/{candidate_id}/leetcode",
    response_model=LeetCodeProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a LeetCode username or profile URL (never fetched; no data is collected)",
    responses=_ERRORS,
)
async def add_leetcode_profile(
    candidate_id: uuid.UUID,
    payload: LeetCodeProfileCreate,
    session: SessionDep,
    settings: SettingsDep,
) -> Response | LeetCodeProfileResponse:
    if not settings.leetcode_enabled:
        return _disabled()
    try:
        username = parse_leetcode_profile(payload.profile)
    except InvalidLeetCodeInputError as exc:
        return error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message)

    await load_candidate(session, candidate_id)
    existing = await session.scalar(
        select(DeveloperProfile).where(
            DeveloperProfile.candidate_id == candidate_id,
            DeveloperProfile.platform == Platform.LEETCODE,
        )
    )
    if existing is not None:
        same = existing.username.lower() == username.lower()
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_profile",
            "This LeetCode profile is already linked to the candidate"
            if same
            else "The candidate already has a LeetCode profile; remove it before adding another",
            details=[{"profile_id": str(existing.id), "username": existing.username}],
        )

    profile = DeveloperProfile(
        candidate_id=candidate_id,
        platform=Platform.LEETCODE,
        username=username,
        profile_url=profile_url(username),
        ownership_status=OwnershipStatus.UNVERIFIED,
        # No permitted source exists: nothing is or will be collected from LeetCode.
        collection_status=CollectionStatus.UNAVAILABLE,
        sync_status=SyncStatus.NOT_APPLICABLE,
    )
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_profile",
            "The candidate already has a LeetCode profile",
        )
    await session.refresh(profile)
    logger.info("leetcode profile linked", extra={"profile_id": str(profile.id)})
    return to_response(profile, settings)


@router.get(
    "/candidates/{candidate_id}/leetcode",
    response_model=LeetCodeProfileResponse,
    summary="Get the LeetCode association and its self-reported evidence",
    responses=_ERRORS,
)
async def get_leetcode_profile(
    candidate_id: uuid.UUID, session: SessionDep, settings: SettingsDep, response: Response
) -> LeetCodeProfileResponse:
    response.headers["Cache-Control"] = "no-store"
    return to_response(await _get_profile(session, candidate_id), settings)


@router.put(
    "/candidates/{candidate_id}/leetcode/evidence",
    response_model=LeetCodeProfileResponse,
    summary="Submit or replace self-reported LeetCode evidence (labelled not verified)",
    responses=_ERRORS,
)
async def put_leetcode_evidence(
    candidate_id: uuid.UUID,
    payload: LeetCodeEvidenceInput,
    session: SessionDep,
    settings: SettingsDep,
) -> Response | LeetCodeProfileResponse:
    if not settings.leetcode_enabled:
        return _disabled()
    profile = await _get_profile(session, candidate_id)
    previous = _evidence(profile)
    evidence = build_evidence(
        payload,
        claimed_profile_url=profile.profile_url,
        previous_revision=previous.revision if previous else 0,
    )
    profile.self_reported_data = evidence.model_dump(mode="json")
    profile.self_reported_at = evidence.submitted_at
    await session.commit()
    await session.refresh(profile)
    logger.info(
        "leetcode self-reported evidence saved",
        extra={"profile_id": str(profile.id), "revision": evidence.revision},
    )
    return to_response(profile, settings)


@router.delete(
    "/candidates/{candidate_id}/leetcode/evidence",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear the self-reported evidence (keeps the profile association)",
    responses=_ERRORS,
)
async def clear_leetcode_evidence(candidate_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await _get_profile(session, candidate_id)
    profile.self_reported_data = None
    profile.self_reported_at = None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/candidates/{candidate_id}/leetcode",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the LeetCode association and its self-reported evidence",
    responses=_ERRORS,
)
async def delete_leetcode_profile(candidate_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await _get_profile(session, candidate_id)
    await session.delete(profile)
    await session.commit()
    logger.info("leetcode profile removed", extra={"profile_id": str(profile.id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/integrations/leetcode/status",
    response_model=LeetCodeIntegrationStatus,
    summary="LeetCode integration mode and the feasibility findings behind it",
)
async def leetcode_status(settings: SettingsDep, response: Response) -> LeetCodeIntegrationStatus:
    response.headers["Cache-Control"] = "no-store"
    return LeetCodeIntegrationStatus(
        enabled=settings.leetcode_enabled,
        investigated_on=INVESTIGATED_ON,
        findings=list(FINDINGS),
        sources=list(SOURCES),
        evidence_stale_after_days=settings.leetcode_evidence_stale_after_days,
    )
