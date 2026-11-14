"""Deterministic estimate of professional experience from resume date strings.

Resume dates are free text ("Jan 2021 - Present", "2019 - 2022", "03/2020 to 06/2021").
Each work-experience entry is parsed into a month interval; overlapping intervals are
merged so parallel jobs are not double-counted. Entries that cannot be parsed are
counted and reported. Internships are excluded. The result is an estimate and is
labelled as such; ``None`` means "unknown", never zero.
"""

from __future__ import annotations

import re
from datetime import date

from app.schemas.evaluation import ExperienceEstimate
from app.schemas.resume_parsing import WorkExperience

_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_DASHES = "-" + chr(0x2013) + chr(0x2014)  # hyphen, en dash, em dash
_RANGE_SEP = re.compile(f"[{_DASHES}]|\\bto\\b")
_RANGE_SPLIT = re.compile(f"\\s*(?:[{_DASHES}]|\\bto\\b)\\s*")
_PRESENT = re.compile(r"\b(present|current|now|today|ongoing|till date|to date)\b", re.I)


def parse_month(text: str | None, *, end: bool, today: date) -> int | None:
    """Return a month index (year*12 + month-1) or None."""
    if not text:
        return None
    if _PRESENT.search(text):
        return today.year * 12 + today.month - 1
    lowered = text.lower()
    year_match = re.search(r"\b(19[5-9]\d|20\d\d)\b", lowered)
    if not year_match:
        return None
    year = int(year_match.group(1))
    month: int | None = None
    numeric = re.search(r"\b(0?[1-9]|1[0-2])\s*[/.-]\s*(?:19|20)\d\d\b", lowered)
    if numeric:
        month = int(numeric.group(1))
    else:
        for token in re.findall(r"[a-z]+", lowered):
            if token in _MONTHS:
                month = _MONTHS[token]
                break
    if month is None:
        month = 12 if end else 1
    return year * 12 + month - 1


def _split_range(entry: WorkExperience) -> tuple[str | None, str | None]:
    if entry.start_date or entry.end_date:
        end = entry.end_date
        if end is None and entry.is_current:
            end = "present"
        return entry.start_date, end
    return None, None


def estimate_experience(
    entries: list[WorkExperience], today: date | None = None
) -> ExperienceEstimate:
    today = today or date.today()
    now_index = today.year * 12 + today.month - 1
    intervals: list[tuple[int, int]] = []
    unparsed = 0
    for entry in entries:
        start_text, end_text = _split_range(entry)
        if start_text and not end_text and _RANGE_SEP.search(start_text):
            # whole range written in one field: "2019 - 2021"
            pieces = _RANGE_SPLIT.split(start_text, maxsplit=1)
            start_text, end_text = pieces[0], pieces[1] if len(pieces) > 1 else None
        start = parse_month(start_text, end=False, today=today)
        end = parse_month(end_text, end=True, today=today)
        if start is None or end is None or end < start or start > now_index:
            unparsed += 1
            continue
        intervals.append((start, min(end, now_index)))

    if not intervals:
        return ExperienceEstimate(
            years=None,
            entries_used=0,
            entries_unparsed=unparsed,
            method="No work-experience dates could be parsed; experience is unknown (not zero)",
        )
    intervals.sort()
    merged: list[list[int]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    months = sum(end - start + 1 for start, end in merged)
    return ExperienceEstimate(
        years=round(months / 12, 1),
        entries_used=len(intervals),
        entries_unparsed=unparsed,
        method=(
            "Sum of non-overlapping work-experience date ranges from the resume "
            "(internships excluded; month precision; an estimate)"
        ),
    )
