"""Tolerant normalisation of Codeforces API objects.

Unexpected or missing fields never crash a sync: optional values become ``None``
and records missing their identifying fields are skipped and counted. Fields
that are not needed, or are personal, are dropped: e-mail, VK/OpenID ids,
avatars, team-mate handles. Source code is not part of these API objects.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.schemas.codeforces import CodeforcesProfileData, RatingChange, Submission
from app.services.codeforces.validation import HANDLE_RE, profile_url

MAX_TEXT = 300
MAX_TAGS = 20
# Codeforces reports this relativeTimeSeconds for submissions outside a contest.
PRACTICE_RELATIVE_TIME = 2_147_483_647
_INDEX_RE = re.compile(r"^[A-Za-z0-9]{1,5}$")
_MAX_TS = 4_102_444_800  # 2100-01-01: anything later is treated as invalid


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _ts(value: Any) -> datetime | None:
    seconds = _int(value)
    if seconds is None or not 0 < seconds < _MAX_TS:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


def normalize_user(raw: Any) -> CodeforcesProfileData:
    """Normalise a ``User`` object; raise ValueError if the handle is unusable."""
    if not isinstance(raw, dict):
        raise ValueError("user is not an object")
    handle = raw.get("handle")
    if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
        raise ValueError("user has no valid handle")
    return CodeforcesProfileData(
        handle=handle,
        first_name=_text(raw.get("firstName")),
        last_name=_text(raw.get("lastName")),
        country=_text(raw.get("country")),
        city=_text(raw.get("city")),
        organization=_text(raw.get("organization")),
        rating=_int(raw.get("rating")),
        max_rating=_int(raw.get("maxRating")),
        rank=_text(raw.get("rank"), 50),
        max_rank=_text(raw.get("maxRank"), 50),
        contribution=_int(raw.get("contribution")),
        registered_at=_ts(raw.get("registrationTimeSeconds")),
        last_online_at=_ts(raw.get("lastOnlineTimeSeconds")),
        profile_url=profile_url(handle),
    )


def contest_url(contest_id: int) -> str:
    kind = "gym" if contest_id >= 100_000 else "contest"
    return f"https://codeforces.com/{kind}/{contest_id}"


def normalize_rating_change(raw: Any) -> RatingChange | None:
    """Normalise a ``RatingChange``; None if it lacks the contest or ratings."""
    if not isinstance(raw, dict):
        return None
    contest_id = _int(raw.get("contestId"))
    old, new = _int(raw.get("oldRating")), _int(raw.get("newRating"))
    if contest_id is None or contest_id <= 0 or old is None or new is None:
        return None
    return RatingChange(
        contest_id=contest_id,
        contest_name=_text(raw.get("contestName")),
        rank=_int(raw.get("rank")),
        old_rating=old,
        new_rating=new,
        delta=new - old,
        rating_updated_at=_ts(raw.get("ratingUpdateTimeSeconds")),
        contest_url=contest_url(contest_id),
    )


def _problem_key(problem: dict[str, Any], contest_id: int | None) -> str | None:
    index = _text(problem.get("index"), 10)
    problem_contest = _int(problem.get("contestId")) or contest_id
    if problem_contest and index:
        return f"{problem_contest}/{index}"
    problemset = _text(problem.get("problemsetName"), 50)
    if problemset and index:
        return f"{problemset}/{index}"
    name = _text(problem.get("name"))
    return f"name:{name}" if name else None


def normalize_submission(raw: Any) -> Submission | None:
    """Normalise a ``Submission``; None if it lacks an id, time or problem."""
    if not isinstance(raw, dict):
        return None
    sub_id = _int(raw.get("id"))
    submitted_at = _ts(raw.get("creationTimeSeconds"))
    problem = raw.get("problem")
    if sub_id is None or submitted_at is None or not isinstance(problem, dict):
        return None
    contest_id = _int(raw.get("contestId"))
    key = _problem_key(problem, contest_id)
    if key is None:
        return None
    index = _text(problem.get("index"), 10)
    problem_contest = _int(problem.get("contestId")) or contest_id
    url = None
    if problem_contest and problem_contest > 0 and index and _INDEX_RE.fullmatch(index):
        url = f"{contest_url(problem_contest)}/problem/{index}"
    tags_raw = problem.get("tags")
    tags = (
        sorted({t.strip() for t in tags_raw if isinstance(t, str) and t.strip()})[:MAX_TAGS]
        if isinstance(tags_raw, list)
        else []
    )
    relative = _int(raw.get("relativeTimeSeconds"))
    if relative is not None and (relative < 0 or relative >= PRACTICE_RELATIVE_TIME):
        relative = None
    author = raw.get("author")
    participant = _text(author.get("participantType"), 40) if isinstance(author, dict) else None
    return Submission(
        id=sub_id,
        contest_id=contest_id,
        problem_index=index,
        problem_name=_text(problem.get("name")),
        problem_rating=_int(problem.get("rating")),
        tags=tags,
        programming_language=_text(raw.get("programmingLanguage"), 100),
        verdict=_text(raw.get("verdict"), 40),
        passed_test_count=_int(raw.get("passedTestCount")),
        submitted_at=submitted_at,
        relative_time_seconds=relative,
        participant_type=participant,
        problem_key=key,
        problem_url=url,
    )
