"""Codeforces developer-profile endpoints (same conventions as the GitHub routes).

Codeforces API calls happen only in ``app.services.codeforces`` (background
sync), never with a user-supplied URL. The status endpoint reports configuration
and observed state without calling Codeforces (no probe requests).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CodeforcesClientDep, CodeforcesSyncDep, SessionDep, SettingsDep
from app.api.v1.candidates import load_candidate
from app.core.enums import (
    AvailabilityStatus,
    CollectionStatus,
    OwnershipStatus,
    Platform,
    SyncStatus,
)
from app.core.errors import error_response
from app.integrations.codeforces import ALLOWED_METHODS, API_BASE
from app.models import DeveloperProfile
from app.schemas.codeforces import (
    CodeforcesIntegrationStatus,
    CodeforcesProfileCreate,
    CodeforcesProfileResponse,
    CodeforcesSnapshot,
    Freshness,
    RatingHistoryPage,
    SubmissionPage,
)
from app.schemas.common import ErrorResponse
from app.services.codeforces.collector import SUBMISSION_HARD_MAX
from app.services.codeforces.validation import (
    InvalidCodeforcesInputError,
    parse_codeforces_profile,
    profile_url,
)

logger = logging.getLogger("app.api.codeforces")

router = APIRouter(tags=["codeforces"])

STALE_SYNC_AFTER = timedelta(minutes=10)
STALE_DATA_AFTER_HOURS = 24 * 7
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse, "description": "Integration disabled or database unavailable"},
}


def _disabled() -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "integration_disabled",
        "The Codeforces integration is disabled. Add codeforces to ENABLED_PLATFORMS to enable it.",
    )


def _snapshot(profile: DeveloperProfile) -> CodeforcesSnapshot | None:
    if not profile.collected_data:
        return None
    try:
        return CodeforcesSnapshot.model_validate(profile.collected_data)
    except ValidationError:
        logger.warning(
            "stored codeforces snapshot is not readable", extra={"profile_id": str(profile.id)}
        )
        return None


def _freshness(collected_at: datetime | None) -> Freshness:
    if collected_at is None:
        return Freshness(
            collected_at=None,
            age_seconds=None,
            stale=True,
            stale_after_hours=STALE_DATA_AFTER_HOURS,
        )
    age = max(0, int((datetime.now(UTC) - collected_at).total_seconds()))
    return Freshness(
        collected_at=collected_at,
        age_seconds=age,
        stale=age > STALE_DATA_AFTER_HOURS * 3600,
        stale_after_hours=STALE_DATA_AFTER_HOURS,
    )


def to_response(profile: DeveloperProfile) -> CodeforcesProfileResponse:
    return CodeforcesProfileResponse(
        id=profile.id,
        candidate_id=profile.candidate_id,
        handle=profile.username,
        profile_url=profile.profile_url,
        ownership_status=profile.ownership_status,
        collection_status=profile.collection_status,
        sync_status=profile.sync_status,
        sync_error_code=profile.sync_error_code,
        sync_error=profile.sync_error,
        sync_warnings=list(profile.sync_warnings or []),
        last_sync_attempt_at=profile.last_sync_attempt_at,
        collected_at=profile.collected_at,
        freshness=_freshness(profile.collected_at),
        created_at=profile.created_at,
        data=_snapshot(profile),
    )


async def _get_profile(session: SessionDep, candidate_id: uuid.UUID) -> DeveloperProfile:
    await load_candidate(session, candidate_id)
    profile = await session.scalar(
        select(DeveloperProfile).where(
            DeveloperProfile.candidate_id == candidate_id,
            DeveloperProfile.platform == Platform.CODEFORCES,
        )
    )
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This candidate has no Codeforces profile")
    return profile


async def _schedule(
    session: SessionDep,
    profile: DeveloperProfile,
    background: BackgroundTasks,
    syncer: CodeforcesSyncDep,
) -> CodeforcesProfileResponse:
    profile.sync_status = SyncStatus.PENDING
    profile.sync_error = None
    profile.sync_error_code = None
    await session.commit()
    await session.refresh(profile)
    background.add_task(syncer.sync, profile.id)
    return to_response(profile)


@router.post(
    "/candidates/{candidate_id}/codeforces",
    response_model=CodeforcesProfileResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Add a Codeforces profile (handle or profile URL); collection runs in the background",
    responses=_ERRORS,
)
async def add_codeforces_profile(
    candidate_id: uuid.UUID,
    payload: CodeforcesProfileCreate,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    syncer: CodeforcesSyncDep,
) -> Response | CodeforcesProfileResponse:
    if not settings.codeforces_enabled:
        return _disabled()
    try:
        handle = parse_codeforces_profile(payload.profile)
    except InvalidCodeforcesInputError as exc:
        return error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message)

    await load_candidate(session, candidate_id)
    existing = await session.scalar(
        select(DeveloperProfile).where(
            DeveloperProfile.candidate_id == candidate_id,
            DeveloperProfile.platform == Platform.CODEFORCES,
        )
    )
    if existing is not None:
        same = existing.username.lower() == handle.lower()
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_profile",
            "This Codeforces profile is already linked to the candidate"
            if same
            else "The candidate already has a Codeforces profile; remove it before adding another",
            details=[{"profile_id": str(existing.id), "handle": existing.username}],
        )

    profile = DeveloperProfile(
        candidate_id=candidate_id,
        platform=Platform.CODEFORCES,
        username=handle,
        profile_url=profile_url(handle),
        # Explicit: a submitted handle is never proof of ownership.
        ownership_status=OwnershipStatus.UNVERIFIED,
        collection_status=CollectionStatus.NOT_COLLECTED,
        sync_status=SyncStatus.PENDING,
    )
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_profile",
            "The candidate already has a Codeforces profile",
        )
    await session.refresh(profile)
    background.add_task(syncer.sync, profile.id)
    logger.info("codeforces profile added", extra={"profile_id": str(profile.id)})
    return to_response(profile)


@router.get(
    "/candidates/{candidate_id}/codeforces",
    response_model=CodeforcesProfileResponse,
    summary="Get the candidate's Codeforces profile, analytics, sync status and freshness",
    responses=_ERRORS,
)
async def get_codeforces_profile(
    candidate_id: uuid.UUID, session: SessionDep, response: Response
) -> CodeforcesProfileResponse:
    response.headers["Cache-Control"] = "no-store"
    return to_response(await _get_profile(session, candidate_id))


@router.post(
    "/candidates/{candidate_id}/codeforces/sync",
    response_model=CodeforcesProfileResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger synchronisation (refresh)",
    responses=_ERRORS,
)
async def sync_codeforces_profile(
    candidate_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    syncer: CodeforcesSyncDep,
) -> Response | CodeforcesProfileResponse:
    if not settings.codeforces_enabled:
        return _disabled()
    profile = await _get_profile(session, candidate_id)
    busy = profile.sync_status in (SyncStatus.PENDING, SyncStatus.PROCESSING)
    if busy and datetime.now(UTC) - profile.updated_at < STALE_SYNC_AFTER:
        raise HTTPException(status.HTTP_409_CONFLICT, "A synchronisation is already in progress")
    return await _schedule(session, profile, background, syncer)


@router.post(
    "/candidates/{candidate_id}/codeforces/retry",
    response_model=CodeforcesProfileResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed synchronisation (409 unless the last sync failed)",
    responses=_ERRORS,
)
async def retry_codeforces_profile(
    candidate_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    syncer: CodeforcesSyncDep,
) -> Response | CodeforcesProfileResponse:
    if not settings.codeforces_enabled:
        return _disabled()
    profile = await _get_profile(session, candidate_id)
    if profile.sync_status != SyncStatus.FAILED:
        return error_response(
            status.HTTP_409_CONFLICT,
            "not_failed",
            "The last synchronisation did not fail; use /sync to refresh",
        )
    return await _schedule(session, profile, background, syncer)


@router.get(
    "/candidates/{candidate_id}/codeforces/submissions",
    response_model=SubmissionPage,
    summary="List collected submissions (newest first) from the last snapshot",
    responses=_ERRORS,
)
async def list_codeforces_submissions(
    candidate_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=SUBMISSION_HARD_MAX)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    verdict: Annotated[str | None, Query(max_length=40)] = None,
    language: Annotated[str | None, Query(max_length=100)] = None,
    tag: Annotated[str | None, Query(max_length=100)] = None,
) -> SubmissionPage:
    profile = await _get_profile(session, candidate_id)
    snapshot = _snapshot(profile)
    if snapshot is None:
        return SubmissionPage(
            items=[],
            total=0,
            limit=limit,
            offset=offset,
            status=AvailabilityStatus.NOT_COLLECTED,
            truncated=False,
            collected_at=None,
        )
    items = [
        s
        for s in snapshot.submissions.items
        if (verdict is None or (s.verdict or "").lower() == verdict.lower())
        and (language is None or (s.programming_language or "").lower() == language.lower())
        and (tag is None or tag.lower() in (t.lower() for t in s.tags))
    ]
    return SubmissionPage(
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
        status=snapshot.submissions.status,
        truncated=snapshot.submissions.truncated,
        collected_at=snapshot.collected_at,
    )


@router.get(
    "/candidates/{candidate_id}/codeforces/rating-history",
    response_model=RatingHistoryPage,
    summary="List rated contests (oldest first) from the last snapshot",
    responses=_ERRORS,
)
async def list_codeforces_rating_history(
    candidate_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RatingHistoryPage:
    profile = await _get_profile(session, candidate_id)
    snapshot = _snapshot(profile)
    if snapshot is None:
        return RatingHistoryPage(
            items=[],
            total=0,
            limit=limit,
            offset=offset,
            status=AvailabilityStatus.NOT_COLLECTED,
            collected_at=None,
        )
    items = snapshot.rating_history.items
    return RatingHistoryPage(
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
        status=snapshot.rating_history.status,
        collected_at=snapshot.collected_at,
    )


@router.delete(
    "/candidates/{candidate_id}/codeforces",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the Codeforces profile association and its collected data",
    responses=_ERRORS,
)
async def delete_codeforces_profile(candidate_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await _get_profile(session, candidate_id)
    await session.delete(profile)
    await session.commit()
    logger.info("codeforces profile removed", extra={"profile_id": str(profile.id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/integrations/codeforces/status",
    response_model=CodeforcesIntegrationStatus,
    summary="Codeforces integration configuration, limits and observed API state (no probe)",
)
async def codeforces_status(
    settings: SettingsDep, client: CodeforcesClientDep, response: Response
) -> CodeforcesIntegrationStatus:
    response.headers["Cache-Control"] = "no-store"
    return CodeforcesIntegrationStatus(
        enabled=settings.codeforces_enabled,
        api_base_url=API_BASE,
        api_methods=sorted(ALLOWED_METHODS),
        min_request_interval_seconds=client.min_interval_s,
        submission_limit=settings.codeforces_submission_limit,
        submission_hard_max=SUBMISSION_HARD_MAX,
        requests_made=client.requests_made,
        last_success_at=client.last_success_at,
        last_error=client.last_error,
        last_error_at=client.last_error_at,
        note=(
            "Public API methods only, paced to at most one call per "
            f"{client.min_interval_s:g} s. No probe request is made by this endpoint."
            if settings.codeforces_enabled
            else "Disabled: add codeforces to ENABLED_PLATFORMS"
        ),
    )
