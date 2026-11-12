"""Background synchronisation of a candidate's GitHub profile.

Mirrors the resume/JD processors: runs after HTTP 202, records every outcome on
the ``developer_profiles`` row, never raises. A failed retry keeps the previous
successful snapshot (``collected_data`` / ``collected_at``) so evidence is not
lost; the failure is reported separately via ``sync_status`` / ``sync_error_code``.

Ownership is never assumed: ``ownership_status`` stays ``unverified``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import CollectionErrorCode, CollectionStatus, Platform, SyncStatus
from app.integrations.github import GitHubError
from app.models import DeveloperProfile
from app.services.github.collector import GitHubCollector
from app.services.github.validation import profile_url

logger = logging.getLogger("app.github.sync")

# Profile-level failures that mean "this account is not usable", not "try again".
_UNAVAILABLE_CODES = {CollectionErrorCode.INVALID_PROFILE, CollectionErrorCode.NOT_A_USER}


class GitHubSyncService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        collector: GitHubCollector,
        enabled: Callable[[], bool],
    ) -> None:
        self._sessions = session_factory
        self._collector = collector
        self._enabled = enabled

    async def sync(self, profile_id: uuid.UUID) -> None:
        """Synchronise one profile. Never raises: outcomes are recorded or logged."""
        started = time.perf_counter()
        try:
            await self._run(profile_id)
        except Exception:
            logger.exception("github sync crashed", extra={"profile_id": str(profile_id)})
        finally:
            logger.info(
                "github sync finished",
                extra={
                    "profile_id": str(profile_id),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    async def _run(self, profile_id: uuid.UUID) -> None:
        async with self._sessions() as session:
            profile = await session.get(DeveloperProfile, profile_id)
            if profile is None or profile.platform != Platform.GITHUB:
                return
            profile.sync_status = SyncStatus.PROCESSING
            profile.sync_error = None
            profile.sync_error_code = None
            profile.last_sync_attempt_at = datetime.now(UTC)
            await session.commit()

            if not self._enabled():
                await self._fail(
                    session,
                    profile,
                    CollectionErrorCode.INTEGRATION_DISABLED,
                    "The GitHub integration is disabled "
                    "(ENABLED_PLATFORMS does not include github)",
                )
                return
            try:
                snapshot = await self._collector.collect(profile.username)
            except GitHubError as exc:
                await self._fail(session, profile, exc.code, exc.message)
                return
            except SQLAlchemyError:
                raise
            except Exception as exc:
                logger.exception(
                    "unexpected github collection error", extra={"profile_id": str(profile_id)}
                )
                await self._fail(
                    session,
                    profile,
                    CollectionErrorCode.INTERNAL_ERROR,
                    f"Unexpected error ({type(exc).__name__})",
                )
                return

            data = snapshot.profile.data
            assert data is not None  # noqa: S101 - collector guarantees a profile on success
            warnings = list(snapshot.warnings)
            if profile.external_id and profile.external_id != str(data.id):
                warnings.append(
                    "The GitHub account behind this username changed since the last sync"
                )
            if data.login != profile.username:
                if profile.username.lower() != data.login.lower():
                    warnings.append(
                        f"GitHub username changed from '{profile.username}' to '{data.login}'"
                    )
                profile.username = data.login

            others = await session.scalar(
                select(DeveloperProfile.id)
                .where(
                    DeveloperProfile.platform == Platform.GITHUB,
                    DeveloperProfile.external_id == str(data.id),
                    DeveloperProfile.candidate_id != profile.candidate_id,
                )
                .limit(1)
            )
            if others is not None:
                warnings.append("This GitHub account is also linked to another candidate")

            profile.external_id = str(data.id)
            profile.profile_url = profile_url(data.login)
            profile.source_url = data.html_url
            profile.collected_data = snapshot.model_dump(mode="json")
            profile.collected_at = snapshot.collected_at
            profile.collection_status = CollectionStatus.COLLECTED
            profile.sync_status = SyncStatus.COMPLETED
            profile.sync_warnings = warnings
            await session.commit()
            logger.info(
                "github profile collected",
                extra={
                    "profile_id": str(profile_id),
                    "repositories": len(snapshot.repositories.items),
                    "api_requests": snapshot.api_requests,
                    "warnings": len(warnings),
                },
            )

    @staticmethod
    async def _fail(
        session: AsyncSession, profile: DeveloperProfile, code: CollectionErrorCode, message: str
    ) -> None:
        profile.sync_status = SyncStatus.FAILED
        profile.sync_error_code = code
        profile.sync_error = message[:2000]
        if code in _UNAVAILABLE_CODES:
            profile.collection_status = CollectionStatus.UNAVAILABLE
        elif profile.collected_data is None:
            profile.collection_status = CollectionStatus.FAILED
        # else: keep COLLECTED - the previous snapshot is still valid evidence
        logger.warning(
            "github sync failed", extra={"profile_id": str(profile.id), "error_code": code.value}
        )
        try:
            await session.commit()
        except SQLAlchemyError:
            logger.exception(
                "could not record github sync failure", extra={"profile_id": str(profile.id)}
            )
