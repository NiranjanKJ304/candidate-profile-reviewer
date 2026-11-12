"""GitHub evidence snapshot and API schemas.

The snapshot is stored in ``developer_profiles.collected_data``. Every data
category is a *section* with its own availability status and provenance (source
platform, source URL, API call, collection time), so "nothing there" (``empty``)
is never confused with "could not be obtained" (``unavailable``, ``rate_limited``,
...). Neither is evidence of a missing skill.

No private data is collected: no e-mail addresses (not even public ones), no
commit messages, no private repositories.
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
    source_platform: Literal["github"] = "github"
    source_url: str | None = Field(default=None, description="Public github.com page for this data")
    api_reference: str = Field(
        description="REST call that produced the data, e.g. GET /users/{login}"
    )
    collected_at: datetime


class Section(BaseModel):
    status: AvailabilityStatus
    note: str | None = None
    provenance: Provenance


class GitHubProfileData(BaseModel):
    login: str
    id: int
    account_type: str
    name: str | None = None
    bio: str | None = None
    company: str | None = None
    location: str | None = None
    blog: str | None = None
    public_repos: int | None = None
    followers: int | None = None
    following: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    html_url: str


class ProfileSection(Section):
    data: GitHubProfileData | None = None


class ReadmeInfo(BaseModel):
    status: AvailabilityStatus
    excerpt: str | None = None
    size_bytes: int | None = None
    truncated: bool = False
    source_url: str | None = None
    note: str | None = None


class GitHubRepository(BaseModel):
    name: str
    full_name: str
    description: str | None = None
    primary_language: str | None = None
    html_url: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None
    stargazers_count: int | None = None
    forks_count: int | None = None
    is_fork: bool
    is_archived: bool
    topics: list[str] = Field(default_factory=list)
    readme: ReadmeInfo | None = Field(
        default=None, description="Null when the README was not requested (fetch limit)."
    )


class RepositoriesSection(Section):
    items: list[GitHubRepository] = Field(default_factory=list)
    reported_public_repos: int | None = None
    truncated: bool = False
    skipped_malformed: int = 0


class CountStat(BaseModel):
    name: str
    repositories: int


class AggregateSection(Section):
    """Derived from collected repositories (primary language / topics of non-forks)."""

    items: list[CountStat] = Field(default_factory=list)


class ActivitySection(Section):
    events_analyzed: int = 0
    event_counts: dict[str, int] = Field(default_factory=dict)
    push_events: int = 0
    commits_reported: int | None = Field(
        default=None, description="Sum of commit counts in push events; null if not reported."
    )
    active_repositories: list[str] = Field(default_factory=list)
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    truncated: bool = False


class ContributionItem(BaseModel):
    title: str
    repository: str | None = None
    state: str | None = None
    created_at: datetime | None = None
    closed_at: datetime | None = None
    merged: bool | None = None
    html_url: str


class ContributionsSection(Section):
    total_count: int | None = None
    incomplete_results: bool = False
    items: list[ContributionItem] = Field(default_factory=list)


class GitHubSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    source_platform: Literal["github"] = "github"
    collected_at: datetime
    authenticated: bool
    profile: ProfileSection
    repositories: RepositoriesSection
    languages: AggregateSection
    topics: AggregateSection
    activity: ActivitySection
    pull_requests: ContributionsSection
    issues: ContributionsSection
    contribution_calendar: Section
    warnings: list[str] = Field(default_factory=list)
    api_requests: int = 0


# --------------------------------------------------------------------------- API schemas
GitHubInput = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class GitHubProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: GitHubInput = Field(
        description="GitHub username or https://github.com/{username} profile URL",
        examples=["octocat", "https://github.com/octocat"],
    )


class GitHubProfileResponse(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    platform: Literal["github"] = "github"
    username: str
    profile_url: str | None
    ownership_status: OwnershipStatus
    collection_status: CollectionStatus
    sync_status: SyncStatus
    sync_error_code: CollectionErrorCode | None
    sync_error: str | None
    sync_warnings: list[str]
    last_sync_attempt_at: datetime | None
    collected_at: datetime | None = Field(description="Time of the last successful collection")
    created_at: datetime
    data: GitHubSnapshot | None = Field(
        description="Last successful snapshot (may be older than a failed retry)"
    )


class GitHubRepositoryPage(BaseModel):
    items: list[GitHubRepository]
    total: int
    limit: int
    offset: int
    status: AvailabilityStatus
    collected_at: datetime | None


class RateLimitView(BaseModel):
    resource: str
    limit: int
    remaining: int
    reset_at: datetime | None


class GitHubIntegrationStatus(BaseModel):
    enabled: bool
    authenticated: bool = Field(description="A GITHUB_TOKEN is configured (value never shown)")
    reachable: bool | None = Field(description="null when the integration is disabled (not probed)")
    rate_limits: list[RateLimitView] = Field(default_factory=list)
    error: str | None = None
    limits: dict[str, int]
