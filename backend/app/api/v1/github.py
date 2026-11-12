"""GitHub developer-profile endpoints (thin: validation, persistence, scheduling).

GitHub API calls happen only in ``app.services.github`` (background sync) and in
the status probe below - never with a user-supplied URL.
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

from app.api.deps import GitHubClientDep, GitHubSyncDep, SessionDep, SettingsDep
from app.api.v1.candidates import load_candidate
from app.core.enums import (
    AvailabilityStatus,
    CollectionStatus,
    OwnershipStatus,
    Platform,
    SyncStatus,
)
from app.core.errors import error_response
from app.integrations.github import GitHubError
from app.models import DeveloperProfile
from app.schemas.common import ErrorResponse
from app.schemas.github import (
    GitHubIntegrationStatus,
    GitHubProfileCreate,
    GitHubProfileResponse,
    GitHubRepositoryPage,
    GitHubSnapshot,
    RateLimitView,
)
from app.services.github.validation import (
    InvalidGitHubInputError,
    parse_github_profile,
    profile_url,
)

logger = logging.getLogger("app.api.github")

router = APIRouter(tags=["github"])

STALE_SYNC_AFTER = timedelta(minutes=10)
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
        "The GitHub integration is disabled. Set ENABLED_PLATFORMS=github to enable it.",
    )


def _snapshot(profile: DeveloperProfile) -> GitHubSnapshot | None:
    if not profile.collected_data:
        return None
    try:
        return GitHubSnapshot.model_validate(profile.collected_data)
    except ValidationError:
        logger.warning(
            "stored github snapshot is not readable", extra={"profile_id": str(profile.id)}
        )
        return None


def to_response(profile: DeveloperProfile) -> GitHubProfileResponse:
    return GitHubProfileResponse(
        id=profile.id,
        candidate_id=profile.candidate_id,
        username=profile.username,
        profile_url=profile.profile_url,
        ownership_status=profile.ownership_status,
        collection_status=profile.collection_status,
        sync_status=profile.sync_status,
        sync_error_code=profile.sync_error_code,
        sync_error=profile.sync_error,
        sync_warnings=list(profile.sync_warnings or []),
        last_sync_attempt_at=profile.last_sync_attempt_at,
        collected_at=profile.collected_at,
        created_at=profile.created_at,
        data=_snapshot(profile),
    )


async def _get_profile(session: SessionDep, candidate_id: uuid.UUID) -> DeveloperProfile:
    await load_candidate(session, candidate_id)
    profile = await session.scalar(
        select(DeveloperProfile).where(
            DeveloperProfile.candidate_id == candidate_id,
            DeveloperProfile.platform == Platform.GITHUB,
        )
    )
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This candidate has no GitHub profile")
    return profile


@router.post(
    "/candidates/{candidate_id}/github",
    response_model=GitHubProfileResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Add a GitHub profile (username or profile URL); collection runs in the background",
    responses=_ERRORS,
)
async def add_github_profile(
    candidate_id: uuid.UUID,
    payload: GitHubProfileCreate,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    syncer: GitHubSyncDep,
) -> Response | GitHubProfileResponse:
    if not settings.github_enabled:
        return _disabled()
    try:
        username = parse_github_profile(payload.profile)
    except InvalidGitHubInputError as exc:
        return error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message)

    await load_candidate(session, candidate_id)
    existing = await session.scalar(
        select(DeveloperProfile).where(
            DeveloperProfile.candidate_id == candidate_id,
            DeveloperProfile.platform == Platform.GITHUB,
        )
    )
    if existing is not None:
        same = existing.username.lower() == username.lower()
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_profile",
            "This GitHub profile is already linked to the candidate"
            if same
            else "The candidate already has a GitHub profile; remove it before adding another",
            details=[{"profile_id": str(existing.id), "username": existing.username}],
        )

    profile = DeveloperProfile(
        candidate_id=candidate_id,
        platform=Platform.GITHUB,
        username=username,
        profile_url=profile_url(username),
        # Explicit: a submitted URL is never proof of ownership.
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
            "The candidate already has a GitHub profile",
        )
    await session.refresh(profile)
    background.add_task(syncer.sync, profile.id)
    logger.info("github profile added", extra={"profile_id": str(profile.id)})
    return to_response(profile)


@router.get(
    "/candidates/{candidate_id}/github",
    response_model=GitHubProfileResponse,
    summary="Get the candidate's GitHub profile, sync status and last collected evidence",
    responses=_ERRORS,
)
async def get_github_profile(
    candidate_id: uuid.UUID, session: SessionDep, response: Response
) -> GitHubProfileResponse:
    response.headers["Cache-Control"] = "no-store"
    return to_response(await _get_profile(session, candidate_id))


@router.post(
    "/candidates/{candidate_id}/github/sync",
    response_model=GitHubProfileResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger or retry synchronisation",
    responses=_ERRORS,
)
async def sync_github_profile(
    candidate_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    syncer: GitHubSyncDep,
) -> Response | GitHubProfileResponse:
    if not settings.github_enabled:
        return _disabled()
    profile = await _get_profile(session, candidate_id)
    busy = profile.sync_status in (SyncStatus.PENDING, SyncStatus.PROCESSING)
    if busy and datetime.now(UTC) - profile.updated_at < STALE_SYNC_AFTER:
        raise HTTPException(status.HTTP_409_CONFLICT, "A synchronisation is already in progress")
    profile.sync_status = SyncStatus.PENDING
    profile.sync_error = None
    profile.sync_error_code = None
    await session.commit()
    await session.refresh(profile)
    background.add_task(syncer.sync, profile.id)
    return to_response(profile)


@router.get(
    "/candidates/{candidate_id}/github/repositories",
    response_model=GitHubRepositoryPage,
    summary="List repositories from the last collected snapshot",
    responses=_ERRORS,
)
async def list_github_repositories(
    candidate_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=300)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_forks: bool = True,
    include_archived: bool = True,
    language: Annotated[str | None, Query(max_length=100)] = None,
) -> GitHubRepositoryPage:
    profile = await _get_profile(session, candidate_id)
    snapshot = _snapshot(profile)
    if snapshot is None:
        return GitHubRepositoryPage(
            items=[],
            total=0,
            limit=limit,
            offset=offset,
            status=AvailabilityStatus.NOT_COLLECTED,
            collected_at=None,
        )
    repos = [
        r
        for r in snapshot.repositories.items
        if (include_forks or not r.is_fork)
        and (include_archived or not r.is_archived)
        and (language is None or (r.primary_language or "").lower() == language.lower())
    ]
    return GitHubRepositoryPage(
        items=repos[offset : offset + limit],
        total=len(repos),
        limit=limit,
        offset=offset,
        status=snapshot.repositories.status,
        collected_at=snapshot.collected_at,
    )


@router.delete(
    "/candidates/{candidate_id}/github",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the GitHub profile association and its collected data",
    responses=_ERRORS,
)
async def delete_github_profile(candidate_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await _get_profile(session, candidate_id)
    await session.delete(profile)
    await session.commit()
    logger.info("github profile removed", extra={"profile_id": str(profile.id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/integrations/github/status",
    response_model=GitHubIntegrationStatus,
    summary="GitHub integration availability, authentication mode and current rate limits",
)
async def github_status(
    settings: SettingsDep, client: GitHubClientDep, response: Response
) -> GitHubIntegrationStatus:
    response.headers["Cache-Control"] = "no-store"
    limits = {
        "max_repositories": settings.github_max_repo_pages * 100,
        "readmes_per_sync": settings.github_readme_limit,
        "readme_max_bytes": settings.github_readme_max_bytes,
        "max_events": settings.github_max_event_pages * 100,
        "recent_pull_requests_and_issues": settings.github_search_items,
    }
    if not settings.github_enabled:
        return GitHubIntegrationStatus(
            enabled=False,
            authenticated=client.authenticated,
            reachable=None,
            error="Disabled: set ENABLED_PLATFORMS=github",
            limits=limits,
        )
    try:
        rates = await client.rate_limit_status()  # does not count against the limit
    except GitHubError as exc:
        return GitHubIntegrationStatus(
            enabled=True,
            authenticated=client.authenticated,
            reachable=False,
            error=exc.message,
            limits=limits,
        )
    return GitHubIntegrationStatus(
        enabled=True,
        authenticated=client.authenticated,
        reachable=True,
        rate_limits=[
            RateLimitView(
                resource=r.resource, limit=r.limit, remaining=r.remaining, reset_at=r.reset_at
            )
            for r in rates.values()
        ],
        limits=limits,
    )
