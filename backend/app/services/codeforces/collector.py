"""Collect a Codeforces user's public evidence into a :class:`CodeforcesSnapshot`.

API budget per sync (defaults): ``user.info`` + ``user.rating`` + one
``user.status`` page = 3 requests, paced at one call per 2 seconds or slower.

Failure model:

* ``user.info`` is required: not found, rate limit, network or API failure aborts
  the sync with a typed :class:`CodeforcesError` (the sync keeps any previous
  snapshot);
* ``user.rating`` and ``user.status`` fail independently and record why
  (partial snapshot);
* after a rate-limit failure, later methods are marked ``rate_limited`` without
  spending more calls;
* a missing submission history is never turned into zeros: derived submission
  statistics are ``null`` unless submissions were actually collected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.enums import AvailabilityStatus
from app.integrations.codeforces import (
    CodeforcesClient,
    CodeforcesError,
    CodeforcesNotFoundError,
    CodeforcesRateLimitError,
)
from app.schemas.codeforces import (
    AnalyticsSection,
    CodeforcesProfileData,
    CodeforcesSnapshot,
    LifetimeStats,
    ProfileSection,
    Provenance,
    RatingChange,
    RatingHistorySection,
    Submission,
    SubmissionsSection,
)
from app.services.codeforces.analytics import submission_stats
from app.services.codeforces.normalize import (
    normalize_rating_change,
    normalize_submission,
    normalize_user,
)

logger = logging.getLogger("app.codeforces.collector")

SUBMISSION_HARD_MAX = 1000
_COLLECTED = (AvailabilityStatus.AVAILABLE, AvailabilityStatus.EMPTY)


@dataclass(frozen=True)
class CollectorLimits:
    submission_limit: int = 500
    page_size: int = 1000  # user.status ``count`` per request

    def effective_limit(self) -> int:
        return max(1, min(self.submission_limit, SUBMISSION_HARD_MAX))


class CodeforcesCollector:
    def __init__(self, client: CodeforcesClient, limits: CollectorLimits) -> None:
        self._client = client
        self._limits = limits

    @staticmethod
    def _prov(api_method: str, source_url: str | None) -> Provenance:
        return Provenance(
            source_url=source_url, api_method=api_method, collected_at=datetime.now(UTC)
        )

    async def collect(self, handle: str) -> CodeforcesSnapshot:
        started = self._client.requests_made
        warnings: list[str] = []

        # ---- user.info (required) ------------------------------------------------
        try:
            response = await self._client.call("user.info", {"handles": handle})
        except CodeforcesNotFoundError as exc:
            raise CodeforcesNotFoundError(f"Codeforces user '{handle}' does not exist") from exc
        user = self._first_user(response.result)
        canonical = user.handle
        profile_section = ProfileSection(
            status=AvailabilityStatus.AVAILABLE,
            provenance=self._prov("user.info", user.profile_url),
            data=user,
        )

        # ---- user.rating ---------------------------------------------------------
        rating_section, rate_limited = await self._rating(canonical)

        # ---- user.status ---------------------------------------------------------
        submissions_section = await self._submissions(canonical, rate_limited, warnings)

        # ---- derived analytics ---------------------------------------------------
        analytics = self._analytics(user, rating_section, submissions_section)
        if user.rating is None:
            warnings.append(
                "The account has no current rating (unrated); "
                "this is not evidence of missing skills"
            )
        return CodeforcesSnapshot(
            collected_at=datetime.now(UTC),
            profile=profile_section,
            rating_history=rating_section,
            submissions=submissions_section,
            analytics=analytics,
            warnings=warnings,
            api_requests=self._client.requests_made - started,
        )

    @staticmethod
    def _first_user(result: Any) -> CodeforcesProfileData:
        if not isinstance(result, list) or not result:
            raise CodeforcesError("Codeforces returned no user for this handle")
        try:
            return normalize_user(result[0])
        except ValueError as exc:
            raise CodeforcesError("Codeforces returned an incomplete profile") from exc

    async def _rating(self, handle: str) -> tuple[RatingHistorySection, bool]:
        """Return the section and whether the call limit was hit."""
        prov = self._prov("user.rating", f"https://codeforces.com/contests/with/{handle}")
        try:
            response = await self._client.call("user.rating", {"handle": handle})
        except CodeforcesError as exc:
            return RatingHistorySection(
                status=exc.availability,
                note=f"Rating history could not be collected: {exc.message}",
                provenance=prov,
            ), isinstance(exc, CodeforcesRateLimitError)
        if not isinstance(response.result, list):
            return RatingHistorySection(
                status=AvailabilityStatus.ERROR,
                note="Codeforces returned an unexpected rating payload",
                provenance=prov,
            ), False
        items: list[RatingChange] = []
        skipped = 0
        for raw in response.result:
            change = normalize_rating_change(raw)
            if change is None:
                skipped += 1
            else:
                items.append(change)
        items.sort(key=lambda c: (c.rating_updated_at is None, c.rating_updated_at))
        return RatingHistorySection(
            status=AvailabilityStatus.AVAILABLE if items else AvailabilityStatus.EMPTY,
            note=None if items else "No rated contests on this account",
            provenance=prov,
            items=items,
            skipped_malformed=skipped,
        ), False

    async def _submissions(
        self, handle: str, rate_limited: bool, warnings: list[str]
    ) -> SubmissionsSection:
        limit = self._limits.effective_limit()
        prov = self._prov("user.status", f"https://codeforces.com/submissions/{handle}")
        if rate_limited:
            return SubmissionsSection(
                status=AvailabilityStatus.RATE_LIMITED,
                note="Skipped: the Codeforces call limit was reached earlier in this sync",
                provenance=prov,
                requested_limit=limit,
            )
        page_size = max(1, min(self._limits.page_size, SUBMISSION_HARD_MAX + 1))
        items: list[Submission] = []
        seen: set[int] = set()
        skipped = 0
        start = 1
        truncated = False
        # Guard against an API that ignores ``from`` (would repeat the same page).
        max_pages = limit // page_size + 2
        pages = 0
        while True:
            pages += 1
            if pages > max_pages:
                truncated = True
                break
            remaining = limit - (len(items) + skipped)
            # Ask for one extra record on the last page to learn whether more exist.
            count = min(page_size, remaining + 1)
            try:
                response = await self._client.call(
                    "user.status", {"handle": handle, "from": start, "count": count}
                )
            except CodeforcesError as exc:
                if not items and not skipped:
                    return SubmissionsSection(
                        status=exc.availability,
                        note=f"Submissions could not be collected: {exc.message}",
                        provenance=prov,
                        requested_limit=limit,
                    )
                warnings.append(
                    "Submission collection stopped early "
                    f"({exc.message}); statistics cover {len(items)} submissions"
                )
                truncated = True
                break
            page = response.result
            if not isinstance(page, list):
                return SubmissionsSection(
                    status=AvailabilityStatus.ERROR,
                    note="Codeforces returned an unexpected submissions payload",
                    provenance=prov,
                    requested_limit=limit,
                )
            for raw in page[:remaining]:
                sub = normalize_submission(raw)
                if sub is None:
                    skipped += 1
                elif sub.id not in seen:  # pages can shift while a user submits
                    seen.add(sub.id)
                    items.append(sub)
            if len(page) > remaining:
                truncated = True
                break
            if len(page) < count or len(items) + skipped >= limit:
                break
            start += len(page)

        if skipped:
            warnings.append(f"{skipped} malformed submission record(s) were skipped")
        if truncated:
            warnings.append(
                f"Only the most recent {len(items)} submissions were collected "
                f"(limit {limit}); submission statistics describe that window, "
                "not the whole history"
            )
        items.sort(key=lambda s: (s.submitted_at, s.id), reverse=True)
        return SubmissionsSection(
            status=AvailabilityStatus.AVAILABLE if items else AvailabilityStatus.EMPTY,
            note=None if items else "No submissions on this account",
            provenance=prov,
            items=items,
            requested_limit=limit,
            truncated=truncated,
            skipped_malformed=skipped,
            window_start=items[-1].submitted_at if items else None,
            window_end=items[0].submitted_at if items else None,
        )

    def _analytics(
        self,
        user: CodeforcesProfileData,
        rating: RatingHistorySection,
        submissions: SubmissionsSection,
    ) -> AnalyticsSection:
        lifetime = LifetimeStats(
            current_rating=user.rating,
            max_rating=user.max_rating,
            rank=user.rank,
            max_rank=user.max_rank,
            rated_contests=len(rating.items) if rating.status in _COLLECTED else None,
        )
        prov = self._prov("derived: user.info, user.rating, user.status", user.profile_url)
        if submissions.status not in _COLLECTED:
            return AnalyticsSection(
                status=submissions.status,
                note=(
                    "Submission statistics are unavailable because submissions could not "
                    "be collected; this is not the same as zero activity"
                ),
                provenance=prov,
                lifetime=lifetime,
                submissions=None,
            )
        stats = submission_stats(submissions.items, truncated=submissions.truncated)
        note = (
            f"Derived from the {stats.total_submissions} most recent submissions only"
            if submissions.truncated
            else "Derived from all submissions returned by Codeforces"
        )
        return AnalyticsSection(
            status=submissions.status,
            note=note,
            provenance=prov,
            lifetime=lifetime,
            submissions=stats,
        )
