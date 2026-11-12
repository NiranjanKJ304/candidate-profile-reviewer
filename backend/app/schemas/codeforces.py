"""Codeforces evidence snapshot and API schemas.

The snapshot is stored in ``developer_profiles.collected_data``. Each data
category is a *section* with its own availability status and provenance
(platform, source URL, official API method, collection time), so "nothing there"
(``empty``) is never confused with "could not be obtained" (``unavailable``,
``rate_limited``, ...). Neither is evidence of a missing skill.

Two scopes are kept apart on purpose:

* **lifetime** values reported by Codeforces (current / maximum rating and rank,
  rated contests from ``user.rating``);
* **collected window** values derived from the most recent N submissions
  (``user.status``). They describe that sample only, not the whole history.

Not collected: source code, e-mail addresses, team-mate handles, private data.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.enums import (
    AvailabilityStatus,
    CollectionErrorCode,
    CollectionStatus,
    OwnershipStatus,
    SyncStatus,
)

SNAPSHOT_SCHEMA_VERSION = 1


class Provenance(BaseModel):
    source_platform: Literal["codeforces"] = "codeforces"
    source_url: str | None = Field(
        default=None, description="Public codeforces.com page for this data"
    )
    api_method: str = Field(description="Official API method, e.g. user.info")
    collected_at: datetime


class Section(BaseModel):
    status: AvailabilityStatus
    note: str | None = None
    provenance: Provenance


class CodeforcesProfileData(BaseModel):
    handle: str
    first_name: str | None = None
    last_name: str | None = None
    country: str | None = None
    city: str | None = None
    organization: str | None = None
    rating: int | None = Field(default=None, description="Current rating; null if unrated")
    max_rating: int | None = None
    rank: str | None = None
    max_rank: str | None = None
    contribution: int | None = None
    registered_at: datetime | None = None
    last_online_at: datetime | None = None
    profile_url: str


class ProfileSection(Section):
    data: CodeforcesProfileData | None = None


class RatingChange(BaseModel):
    contest_id: int
    contest_name: str | None = None
    rank: int | None = None
    old_rating: int
    new_rating: int
    delta: int
    rating_updated_at: datetime | None = None
    contest_url: str


class RatingHistorySection(Section):
    items: list[RatingChange] = Field(
        default_factory=list, description="Rated contests, oldest first"
    )
    skipped_malformed: int = 0


class Submission(BaseModel):
    id: int
    contest_id: int | None = None
    problem_index: str | None = None
    problem_name: str | None = None
    problem_rating: int | None = None
    tags: list[str] = Field(default_factory=list)
    programming_language: str | None = None
    verdict: str | None = Field(default=None, description="null while judging")
    passed_test_count: int | None = None
    submitted_at: datetime
    relative_time_seconds: int | None = Field(
        default=None, description="Seconds since contest start (very large if practice)"
    )
    participant_type: str | None = None
    problem_key: str = Field(description="Stable key used to count unique problems")
    problem_url: str | None = None


class SubmissionsSection(Section):
    items: list[Submission] = Field(default_factory=list, description="Newest first")
    requested_limit: int
    truncated: bool = Field(
        default=False, description="More submissions exist than were collected (window only)"
    )
    skipped_malformed: int = 0
    window_start: datetime | None = None
    window_end: datetime | None = None


class NamedCount(BaseModel):
    name: str
    count: int


class RatingBucket(BaseModel):
    rating: int | None = Field(description="Problem rating; null = problem has no rating")
    problems: int


class MonthActivity(BaseModel):
    month: str = Field(description="YYYY-MM (UTC)")
    submissions: int
    accepted: int


class SubmissionStats(BaseModel):
    """Derived from the collected submissions only (see ``truncated``)."""

    scope: Literal["collected_window"] = "collected_window"
    total_submissions: int
    accepted_submissions: int = Field(description="Submissions with verdict OK")
    rejected_submissions: int = Field(description="Judged with any verdict other than OK")
    pending_submissions: int = Field(description="Not yet judged (no verdict or TESTING)")
    attempted_unique_problems: int
    accepted_unique_problems: int
    accepted_by_rating: list[RatingBucket] = Field(
        default_factory=list, description="Unique accepted problems per problem rating"
    )
    accepted_by_tag: list[NamedCount] = Field(
        default_factory=list, description="Unique accepted problems per tag"
    )
    verdicts: list[NamedCount] = Field(default_factory=list, description="Submissions per verdict")
    languages: list[NamedCount] = Field(
        default_factory=list, description="Submissions per language"
    )
    monthly_activity: list[MonthActivity] = Field(default_factory=list)
    contests_in_window: int = Field(
        description="Distinct contests with contestant submissions in the window"
    )
    window_start: datetime | None = None
    window_end: datetime | None = None
    truncated: bool


class LifetimeStats(BaseModel):
    """Values Codeforces reports for the whole account."""

    scope: Literal["lifetime"] = "lifetime"
    current_rating: int | None = None
    max_rating: int | None = None
    rank: str | None = None
    max_rank: str | None = None
    rated_contests: int | None = Field(
        default=None, description="From user.rating; null when it could not be collected"
    )


class AnalyticsSection(Section):
    """Reproducible statistics; null means "could not be computed", never zero."""

    lifetime: LifetimeStats
    submissions: SubmissionStats | None = None


class CodeforcesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    source_platform: Literal["codeforces"] = "codeforces"
    collected_at: datetime
    profile: ProfileSection
    rating_history: RatingHistorySection
    submissions: SubmissionsSection
    analytics: AnalyticsSection
    warnings: list[str] = Field(default_factory=list)
    api_requests: int = 0


# --------------------------------------------------------------------------- API schemas
CodeforcesInput = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]


class CodeforcesProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: CodeforcesInput = Field(
        description="Codeforces handle or https://codeforces.com/profile/{handle} URL",
        examples=["tourist", "https://codeforces.com/profile/tourist"],
    )


class Freshness(BaseModel):
    collected_at: datetime | None
    age_seconds: int | None
    stale: bool = Field(description="Older than the freshness threshold (or never collected)")
    stale_after_hours: int


class CodeforcesProfileResponse(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    platform: Literal["codeforces"] = "codeforces"
    handle: str
    profile_url: str | None
    ownership_status: OwnershipStatus
    collection_status: CollectionStatus
    sync_status: SyncStatus
    sync_error_code: CollectionErrorCode | None
    sync_error: str | None
    sync_warnings: list[str]
    last_sync_attempt_at: datetime | None
    collected_at: datetime | None = Field(description="Time of the last successful collection")
    freshness: Freshness
    created_at: datetime
    data: CodeforcesSnapshot | None = Field(
        description="Last successful snapshot (may be older than a failed retry)"
    )


class SubmissionPage(BaseModel):
    items: list[Submission]
    total: int
    limit: int
    offset: int
    status: AvailabilityStatus
    truncated: bool
    collected_at: datetime | None


class RatingHistoryPage(BaseModel):
    items: list[RatingChange]
    total: int
    limit: int
    offset: int
    status: AvailabilityStatus
    collected_at: datetime | None


class CodeforcesIntegrationStatus(BaseModel):
    enabled: bool
    api_base_url: str
    api_methods: list[str]
    min_request_interval_seconds: float
    submission_limit: int
    submission_hard_max: int
    requests_made: int = Field(description="API requests made by this server process")
    last_success_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    note: str
