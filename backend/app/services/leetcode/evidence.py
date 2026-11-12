"""Build the stored self-reported evidence document from validated input.

Pure functions, with no I/O and no network. Every section gets provenance marking
it self-reported and not verified. Values that were not provided stay ``null``,
and a section with nothing provided is ``not_collected`` ("not provided"), never zero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.core.enums import AvailabilityStatus
from app.schemas.leetcode import (
    ContestEntry,
    ContestHistorySection,
    ContestSection,
    CountsSection,
    LeetCodeEvidence,
    LeetCodeEvidenceInput,
    NamedCount,
    Provenance,
    SolvedSection,
)

NOT_PROVIDED = "Not provided (unknown, not zero)"
PARTIAL = "Some values were not provided; they are unknown, not zero"
SELF_REPORTED_WARNING = (
    "Self-reported and not verified: LeetCode offers no permitted API, "
    "so these figures could not be checked against LeetCode"
)


def _status(provided: bool) -> AvailabilityStatus:
    return AvailabilityStatus.AVAILABLE if provided else AvailabilityStatus.NOT_COLLECTED


def _note(values: list[object]) -> str | None:
    given = [v for v in values if v is not None]
    if not given:
        return NOT_PROVIDED
    return PARTIAL if len(given) < len(values) else None


def build_evidence(
    payload: LeetCodeEvidenceInput,
    *,
    claimed_profile_url: str | None,
    previous_revision: int = 0,
    now: datetime | None = None,
) -> LeetCodeEvidence:
    submitted_at = now or datetime.now(UTC)
    prov = Provenance(
        reported_by=payload.reported_by,
        claimed_profile_url=claimed_profile_url,
        submitted_at=submitted_at,
    )
    solved_values: list[object] = [
        payload.total_solved,
        payload.easy_solved,
        payload.medium_solved,
        payload.hard_solved,
    ]
    contest_values: list[object] = [
        payload.contest_rating,
        payload.contests_attended,
        payload.contest_top_percentage,
    ]
    warnings = [SELF_REPORTED_WARNING]
    if payload.as_of is None:
        warnings.append("No 'as of' date was given; the period these figures describe is unknown")

    return LeetCodeEvidence(
        reported_by=payload.reported_by,
        submitted_at=submitted_at,
        as_of=payload.as_of,
        revision=previous_revision + 1,
        solved=SolvedSection(
            status=_status(any(v is not None for v in solved_values)),
            note=_note(solved_values),
            provenance=prov,
            total=payload.total_solved,
            easy=payload.easy_solved,
            medium=payload.medium_solved,
            hard=payload.hard_solved,
        ),
        contest=ContestSection(
            status=_status(any(v is not None for v in contest_values)),
            note=_note(contest_values),
            provenance=prov,
            rating=payload.contest_rating,
            attended=payload.contests_attended,
            top_percentage=payload.contest_top_percentage,
        ),
        contest_history=ContestHistorySection(
            status=_status(bool(payload.contest_history)),
            note=None if payload.contest_history else NOT_PROVIDED,
            provenance=prov,
            items=[
                ContestEntry(
                    contest_name=c.contest_name,
                    rank=c.rank,
                    rating_after=c.rating_after,
                    contest_date=c.contest_date,
                )
                for c in payload.contest_history
            ],
        ),
        languages=CountsSection(
            status=_status(bool(payload.languages)),
            note=None if payload.languages else NOT_PROVIDED,
            provenance=prov,
            items=[
                NamedCount(name=x.name, problems_solved=x.problems_solved)
                for x in payload.languages
            ],
        ),
        topics=CountsSection(
            status=_status(bool(payload.topics)),
            note=None if payload.topics else NOT_PROVIDED,
            provenance=prov,
            items=[
                NamedCount(name=x.name, problems_solved=x.problems_solved) for x in payload.topics
            ],
        ),
        notes=payload.notes,
        warnings=warnings,
    )


def evidence_age_days(evidence: LeetCodeEvidence, today: date | None = None) -> int:
    """Days since the figures' ``as_of`` date (or the submission date if unknown)."""
    today = today or datetime.now(UTC).date()
    reference = evidence.as_of or evidence.submitted_at.date()
    return max(0, (today - reference).days)
