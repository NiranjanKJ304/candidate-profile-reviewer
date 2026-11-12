"""LeetCode schemas: manual, self-reported evidence (Phase 4).

LeetCode offers no permitted data API, so nothing here is collected from LeetCode.
All evidence is **self-reported and not verified**. Every stored section carries
provenance saying so (source type, verification status, who entered it, when).
It is stored in ``developer_profiles.self_reported_data``, never in
``collected_data``.

A value that was not provided stays ``null`` and is never turned into zero. A
section with nothing provided has status ``not_collected`` and a "not provided" note.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.enums import AvailabilityStatus, CollectionStatus, OwnershipStatus, SyncStatus

SNAPSHOT_SCHEMA_VERSION = 1
LEETCODE_FOUNDED = date(2015, 1, 1)  # earliest plausible date for LeetCode statistics
MAX_SOLVED = 10_000
MAX_RATING = 5_000

ReportedBy = Literal["candidate", "recruiter"]
_SPACES = re.compile(r"\s+")


def _clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in value):
        raise ValueError("must not contain control characters")
    return value.strip()


def _clean_name(value: str) -> str:
    value = _SPACES.sub(" ", _clean_text(value))
    if not value:
        raise ValueError("must not be blank")
    return value


def _not_future(value: date | None) -> date | None:
    if value is None:
        return None
    # One day of leeway: "today" for a user ahead of UTC can be tomorrow in UTC.
    if value > datetime.now(UTC).date() + timedelta(days=1):
        raise ValueError("must not be in the future")
    if value < LEETCODE_FOUNDED:
        raise ValueError(f"must not be before {LEETCODE_FOUNDED.isoformat()}")
    return value


Name = Annotated[str, StringConstraints(max_length=120), AfterValidator(_clean_name)]
PastDate = Annotated[date | None, AfterValidator(_not_future)]
Solved = Annotated[int | None, Field(ge=0, le=MAX_SOLVED, strict=True)]


# --------------------------------------------------------------------------- input
class NamedCountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[Name, StringConstraints(max_length=60)]
    problems_solved: Solved = None


class ContestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contest_name: Name
    rank: Annotated[int | None, Field(ge=1, le=10_000_000, strict=True)] = None
    rating_after: Annotated[int | None, Field(ge=0, le=MAX_RATING, strict=True)] = None
    contest_date: PastDate = None


def _unique(names: list[str], label: str) -> None:
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in seen:
            raise ValueError(f"{label} contains a duplicate entry: {name[:60]}")
        seen.add(key)


class LeetCodeEvidenceInput(BaseModel):
    """Evidence entered manually. Every field is optional; omitted means "not provided"."""

    model_config = ConfigDict(extra="forbid")

    reported_by: ReportedBy = Field(
        description="'candidate' (entered by the candidate) or 'recruiter' (entered by a "
        "recruiter from the candidate's statement). Both are self-reported."
    )
    as_of: PastDate = Field(default=None, description="Date the figures describe")
    total_solved: Solved = None
    easy_solved: Solved = None
    medium_solved: Solved = None
    hard_solved: Solved = None
    contest_rating: Annotated[int | None, Field(ge=0, le=MAX_RATING, strict=True)] = None
    contests_attended: Annotated[int | None, Field(ge=0, le=5_000, strict=True)] = None
    contest_top_percentage: Annotated[float | None, Field(gt=0, le=100, strict=True)] = None
    contest_history: list[ContestInput] = Field(default_factory=list, max_length=100)
    languages: list[NamedCountInput] = Field(default_factory=list, max_length=30)
    topics: list[NamedCountInput] = Field(default_factory=list, max_length=60)
    notes: Annotated[str | None, StringConstraints(max_length=1000)] = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.notes is not None:
            self.notes = _clean_text(self.notes) or None
        values = (
            self.total_solved,
            self.easy_solved,
            self.medium_solved,
            self.hard_solved,
            self.contest_rating,
            self.contests_attended,
            self.contest_top_percentage,
        )
        if all(v is None for v in values) and not (
            self.contest_history or self.languages or self.topics
        ):
            raise ValueError("provide at least one evidence value")

        parts = [
            v for v in (self.easy_solved, self.medium_solved, self.hard_solved) if v is not None
        ]
        if self.total_solved is not None:
            if sum(parts) > self.total_solved:
                raise ValueError("easy + medium + hard solved cannot exceed total solved")
            if len(parts) == 3 and sum(parts) != self.total_solved:
                raise ValueError("easy + medium + hard solved must equal total solved")
            for item in (*self.languages, *self.topics):
                if item.problems_solved is not None and item.problems_solved > self.total_solved:
                    raise ValueError(f"'{item.name[:60]}' cannot exceed total solved")

        if self.contests_attended == 0 and (
            self.contest_history
            or self.contest_rating is not None
            or self.contest_top_percentage is not None
        ):
            raise ValueError("contest details were given but contests attended is 0")
        if (
            self.contests_attended is not None
            and len(self.contest_history) > self.contests_attended
        ):
            raise ValueError("contest history has more entries than contests attended")

        _unique([lang.name for lang in self.languages], "languages")
        _unique([t.name for t in self.topics], "topics")
        _unique(
            [f"{c.contest_name}|{c.contest_date or ''}" for c in self.contest_history],
            "contest history",
        )
        return self


# --------------------------------------------------------------------------- stored snapshot
class Provenance(BaseModel):
    source_platform: Literal["leetcode"] = "leetcode"
    source_type: Literal["self_reported"] = "self_reported"
    verification_status: Literal["not_verified"] = "not_verified"
    method: Literal["manual_entry"] = "manual_entry"
    reported_by: ReportedBy
    claimed_profile_url: str | None = Field(
        description="Profile URL given for the candidate; never fetched or checked"
    )
    submitted_at: datetime


class Section(BaseModel):
    status: AvailabilityStatus = Field(
        description="available = provided; not_collected = not provided (unknown, not zero)"
    )
    note: str | None = None
    provenance: Provenance


class SolvedSection(Section):
    total: int | None = None
    easy: int | None = None
    medium: int | None = None
    hard: int | None = None


class ContestSection(Section):
    rating: int | None = None
    attended: int | None = None
    top_percentage: float | None = None


class ContestEntry(BaseModel):
    contest_name: str
    rank: int | None = None
    rating_after: int | None = None
    contest_date: date | None = None


class ContestHistorySection(Section):
    items: list[ContestEntry] = Field(default_factory=list)


class NamedCount(BaseModel):
    name: str
    problems_solved: int | None = None


class CountsSection(Section):
    items: list[NamedCount] = Field(default_factory=list)


class LeetCodeEvidence(BaseModel):
    """Self-reported evidence document stored in ``self_reported_data``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    source_platform: Literal["leetcode"] = "leetcode"
    source_type: Literal["self_reported"] = "self_reported"
    verification_status: Literal["not_verified"] = "not_verified"
    reported_by: ReportedBy
    submitted_at: datetime
    as_of: date | None = None
    revision: int = Field(ge=1, description="Increments each time the evidence is replaced")
    solved: SolvedSection
    contest: ContestSection
    contest_history: ContestHistorySection
    languages: CountsSection
    topics: CountsSection
    notes: str | None = None
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- API schemas
LeetCodeInput = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]


class LeetCodeProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: LeetCodeInput = Field(
        description="LeetCode username or https://leetcode.com/u/{username}/ URL (never fetched)",
        examples=["jane_doe", "https://leetcode.com/u/jane_doe/"],
    )


class PlatformData(BaseModel):
    status: Literal["unavailable"] = "unavailable"
    reason: str
    sources: list[str]


class EvidenceFreshness(BaseModel):
    submitted_at: datetime | None
    as_of: date | None
    age_days: int | None = Field(description="Days since as_of (or submission if as_of is unknown)")
    stale: bool
    stale_after_days: int


class LeetCodeProfileResponse(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    platform: Literal["leetcode"] = "leetcode"
    username: str
    profile_url: str | None
    ownership_status: OwnershipStatus
    collection_status: CollectionStatus = Field(
        description="Always 'unavailable': no data is collected from LeetCode"
    )
    sync_status: SyncStatus = Field(description="Always 'not_applicable'")
    integration_mode: Literal["manual_self_reported"] = "manual_self_reported"
    platform_data: PlatformData
    evidence: LeetCodeEvidence | None = Field(
        description="Self-reported, not verified. Null until evidence is submitted"
    )
    freshness: EvidenceFreshness
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


class LeetCodeIntegrationStatus(BaseModel):
    enabled: bool
    integration_mode: Literal["manual_self_reported"] = "manual_self_reported"
    platform_api: Literal["unavailable"] = "unavailable"
    sync_supported: Literal[False] = False
    contacts_leetcode: Literal[False] = Field(
        default=False, description="This integration never sends requests to LeetCode"
    )
    investigated_on: str
    findings: list[str]
    sources: list[str]
    evidence_stale_after_days: int
