"""Transparent, reproducible statistics over collected Codeforces submissions.

Definitions (also in API_DOCUMENTATION.md):

* accepted submission: verdict ``OK``;
* pending submission: no verdict yet, ``TESTING`` or ``SUBMITTED``;
* rejected submission: any other verdict (WRONG_ANSWER, TIME_LIMIT_EXCEEDED,
  COMPILATION_ERROR, SKIPPED, CHALLENGED, ...);
* unique problem: one ``problem_key`` (contest id + index). The same statement
  used in two divisions has two contest ids and counts twice;
* accepted unique problems by rating / tag: each accepted problem counted once;
* contests in window: distinct contests with ``CONTESTANT`` or
  ``OUT_OF_COMPETITION`` submissions in the collected window.

Everything here describes the collected window only, never the whole history.
"""

from __future__ import annotations

from collections import Counter

from app.schemas.codeforces import (
    MonthActivity,
    NamedCount,
    RatingBucket,
    Submission,
    SubmissionStats,
)

ACCEPTED = "OK"
PENDING = frozenset({None, "TESTING", "SUBMITTED"})
LIVE_PARTICIPATION = frozenset({"CONTESTANT", "OUT_OF_COMPETITION"})


def _ranked(counter: Counter[str]) -> list[NamedCount]:
    return [
        NamedCount(name=name, count=count)
        for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def submission_stats(items: list[Submission], *, truncated: bool) -> SubmissionStats:
    accepted = [s for s in items if s.verdict == ACCEPTED]
    pending = [s for s in items if s.verdict in PENDING]

    # First accepted submission per problem (each problem counted once).
    accepted_problems: dict[str, Submission] = {}
    for sub in accepted:
        accepted_problems.setdefault(sub.problem_key, sub)

    by_rating: Counter[int | None] = Counter(s.problem_rating for s in accepted_problems.values())
    by_tag: Counter[str] = Counter(t for s in accepted_problems.values() for t in s.tags)

    months: dict[str, list[int]] = {}
    for sub in items:
        bucket = months.setdefault(sub.submitted_at.strftime("%Y-%m"), [0, 0])
        bucket[0] += 1
        if sub.verdict == ACCEPTED:
            bucket[1] += 1

    contests = {
        s.contest_id
        for s in items
        if s.contest_id is not None and s.participant_type in LIVE_PARTICIPATION
    }
    times = [s.submitted_at for s in items]
    return SubmissionStats(
        total_submissions=len(items),
        accepted_submissions=len(accepted),
        rejected_submissions=len(items) - len(accepted) - len(pending),
        pending_submissions=len(pending),
        attempted_unique_problems=len({s.problem_key for s in items}),
        accepted_unique_problems=len(accepted_problems),
        accepted_by_rating=[
            RatingBucket(rating=rating, problems=count)
            for rating, count in sorted(
                by_rating.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)
            )
        ],
        accepted_by_tag=_ranked(by_tag),
        verdicts=_ranked(Counter(s.verdict or "PENDING" for s in items)),
        languages=_ranked(Counter(s.programming_language or "Unknown" for s in items)),
        monthly_activity=[
            MonthActivity(month=month, submissions=counts[0], accepted=counts[1])
            for month, counts in sorted(months.items())
        ],
        contests_in_window=len(contests),
        window_start=min(times) if times else None,
        window_end=max(times) if times else None,
        truncated=truncated,
    )
