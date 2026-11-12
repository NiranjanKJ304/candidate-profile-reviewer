"""Background synchronisation of a candidate's Codeforces profile.

Same contract as the GitHub sync: runs after HTTP 202, records every outcome on
the ``developer_profiles`` row and never raises. A failed retry keeps the
previous successful snapshot (``collected_data`` / ``collected_at``); the failure
is reported separately via ``sync_status`` / ``sync_error_code``.

Codeforces exposes no numeric account id. ``user.info`` resolves former handles
to the current one, so ``external_id`` stores the current handle in lower case.
It is used to report the same account linked to several candidates.

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
from app.integrations.codeforces import CodeforcesError
from app.models import DeveloperProfile
from app.services.codeforces.collector import CodeforcesCollector
from app.services.codeforces.validation import profile_url

logger = logging.getLogger("app.codeforces.sync")

_UNAVAILABLE_CODES = {CollectionErrorCode.INVALID_PROFILE, CollectionErrorCode.NOT_A_USER}


class CodeforcesSyncService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        collector: CodeforcesCollector,
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
            logger.exception("codeforces sync crashed", extra={"profile_id": str(profile_id)})
        finally:
            logger.info(
                "codeforces sync finished",
                extra={
                    "profile_id": str(profile_id),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    async def _run(self, profile_id: uuid.UUID) -> None:
        async with self._sessions() as session:
            profile = await session.get(DeveloperProfile, profile_id)
            if profile is None or profile.platform != Platform.CODEFORCES:
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
                    "The Codeforces integration is disabled "
                    "(ENABLED_PLATFORMS does not include codeforces)",
                )
                return
            try:
                snapshot = await self._collector.collect(profile.username)
            except CodeforcesError as exc:
                await self._fail(session, profile, exc.code, exc.message)
                return
            except SQLAlchemyError:
                raise
            except Exception as exc:
                logger.exception(
                    "unexpected codeforces collection error",
                    extra={"profile_id": str(profile_id)},
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
            if data.handle != profile.username:
                if data.handle.lower() != profile.username.lower():
                    warnings.append(
                        f"Codeforces handle changed from '{profile.username}' to '{data.handle}'"
                    )
                profile.username = data.handle
            account = data.handle.lower()

            others = await session.scalar(
                select(DeveloperProfile.id)
                .where(
                    DeveloperProfile.platform == Platform.CODEFORCES,
                    DeveloperProfile.external_id == account,
                    DeveloperProfile.candidate_id != profile.candidate_id,
                )
                .limit(1)
            )
            if others is not None:
                warnings.append("This Codeforces account is also linked to another candidate")

            profile.external_id = account
            profile.profile_url = profile_url(data.handle)
            profile.source_url = data.profile_url
            profile.collected_data = snapshot.model_dump(mode="json")
            profile.collected_at = snapshot.collected_at
            profile.collection_status = CollectionStatus.COLLECTED
            profile.sync_status = SyncStatus.COMPLETED
            profile.sync_warnings = warnings
            await session.commit()
            logger.info(
                "codeforces profile collected",
                extra={
                    "profile_id": str(profile_id),
                    "submissions": len(snapshot.submissions.items),
                    "rated_contests": len(snapshot.rating_history.items),
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
            "codeforces sync failed",
            extra={"profile_id": str(profile.id), "error_code": code.value},
        )
        try:
            await session.commit()
        except SQLAlchemyError:
            logger.exception(
                "could not record codeforces sync failure", extra={"profile_id": str(profile.id)}
            )
