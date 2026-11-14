"""Identity removal before any candidate data is sent to the LLM.

Removes the candidate's name (full name and each name part of 3+ letters), e-mail
addresses, phone numbers and URLs from free text. Counts are recorded so the
evaluation can report what was redacted (never the values themselves).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s)>\]]+", re.IGNORECASE)
# Digit runs with separators; only treated as a phone number when they contain 9+
# digits, so year ranges ("2019 - 2022") and small numbers are left alone.
_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_MIN_PHONE_DIGITS = 9


@dataclass
class Redactor:
    """``name`` is the candidate's full name; ``identifiers`` are platform usernames."""

    name: str | None = None
    identifiers: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(
        default_factory=lambda: {"names": 0, "emails": 0, "phones": 0, "urls": 0}
    )

    def __post_init__(self) -> None:
        parts: list[str] = []
        if self.name:
            full = self.name.strip()
            if len(full) >= 3:
                parts.append(full)
            parts.extend(p for p in re.split(r"[\s.,]+", full) if len(p) >= 3)
        parts.extend(i.strip() for i in self.identifiers if len(i.strip()) >= 3)
        # Longest first so the full name is replaced before its parts.
        unique = sorted(set(parts), key=len, reverse=True)
        self._name_patterns = [
            re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.IGNORECASE) for p in unique
        ]

    def _sub(self, pattern: re.Pattern[str], repl: str, text: str, key: str) -> str:
        new, n = pattern.subn(repl, text)
        self.counts[key] += n
        return new

    def _phone(self, match: re.Match[str]) -> str:
        if sum(ch.isdigit() for ch in match.group(0)) < _MIN_PHONE_DIGITS:
            return match.group(0)
        self.counts["phones"] += 1
        return "[phone]"

    def __call__(self, text: str | None) -> str:
        if not text:
            return ""
        text = self._sub(_EMAIL, "[email]", text, "emails")
        text = self._sub(_URL, "[link]", text, "urls")
        text = _PHONE_CANDIDATE.sub(self._phone, text)
        for pattern in self._name_patterns:
            text = self._sub(pattern, "[candidate]", text, "names")
        return text
