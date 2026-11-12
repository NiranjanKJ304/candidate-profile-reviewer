"""LeetCode profile input validation and normalisation.

Accepts a username or a profile URL: ``https://leetcode.com/u/{username}/`` (current)
or ``https://leetcode.com/{username}/`` (legacy). ``http``/``www.`` are tolerated, and
the trailing slash, query and fragment are ignored. Everything else is rejected:
problem, contest, discuss and other site pages, other hosts (including the
separate ``leetcode.cn`` service), credentials, ports and non-http schemes.

The result is only ever a username, and **it is never sent to LeetCode**. The
platform offers no permitted API (see ``app.services.leetcode.feasibility``), so the
username is not checked against LeetCode and cannot be verified here.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")
ALLOWED_HOSTS = frozenset({"leetcode.com", "www.leetcode.com"})

# First path segments that are leetcode.com pages, not legacy profile URLs.
RESERVED_PATHS = frozenset(
    {
        "u",
        "accounts",
        "api",
        "assessment",
        "bugbounty",
        "company",
        "contest",
        "discuss",
        "explore",
        "faq",
        "forums",
        "graphql",
        "interview",
        "jobs",
        "kb",
        "library",
        "list",
        "notes",
        "playground",
        "points",
        "privacy",
        "problems",
        "problemset",
        "progress",
        "region",
        "session",
        "store",
        "studyplan",
        "subscribe",
        "subscription",
        "submissions",
        "support",
        "tag",
        "terms",
    }
)


class InvalidLeetCodeInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _check_username(name: str) -> str:
    if not USERNAME_RE.fullmatch(name) or not _HAS_ALNUM.search(name):
        raise InvalidLeetCodeInputError(
            "invalid_username",
            "Not a valid LeetCode username (up to 40 latin letters, digits, '_', '-' or '.')",
        )
    if name.lower() in RESERVED_PATHS:
        raise InvalidLeetCodeInputError(
            "invalid_username", "This is a LeetCode page name, not a username"
        )
    return name


def parse_leetcode_profile(raw: str) -> str:
    """Return the username for a username or profile URL; raise InvalidLeetCodeInputError."""
    value = unicodedata.normalize("NFKC", raw or "").strip()
    if not value:
        raise InvalidLeetCodeInputError("empty", "Enter a LeetCode username or profile URL")
    if len(value) > 300:
        raise InvalidLeetCodeInputError("too_long", "The value is too long")
    if any(ch.isspace() for ch in value) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InvalidLeetCodeInputError(
            "malformed", "The value must not contain spaces or control characters"
        )

    lowered = value.lower()
    looks_like_url = "://" in value or lowered.startswith(("leetcode.com/", "www.leetcode.com/"))
    if not looks_like_url:
        if any(ch in value for ch in "/:\\;") or "@" in value[1:]:
            raise InvalidLeetCodeInputError(
                "malformed", "Enter a username or a https://leetcode.com/u/{username}/ URL"
            )
        return _check_username(value.removeprefix("@"))

    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise InvalidLeetCodeInputError("malformed_url", "The URL is malformed") from exc

    if parts.scheme not in ("https", "http"):
        raise InvalidLeetCodeInputError(
            "unsupported_scheme", "Only https://leetcode.com profile URLs are accepted"
        )
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidLeetCodeInputError("malformed_url", "URLs with credentials are not accepted")
    if port is not None:
        raise InvalidLeetCodeInputError("unsupported_domain", "URLs with a port are not accepted")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidLeetCodeInputError(
            "unsupported_domain",
            "Only leetcode.com profile URLs are accepted (e.g. https://leetcode.com/u/username/)",
        )
    if "\\" in parts.path or "%" in parts.path:
        raise InvalidLeetCodeInputError("malformed_url", "The URL path is malformed")

    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        raise InvalidLeetCodeInputError("not_a_profile", "The URL does not contain a username")
    if segments[0].lower() == "u" and len(segments) == 2:
        return _check_username(segments[1])
    if len(segments) == 1 and segments[0].lower() not in RESERVED_PATHS:
        return _check_username(segments[0])  # legacy https://leetcode.com/{username}/
    raise InvalidLeetCodeInputError(
        "not_a_profile",
        "This is not a profile URL (problem, contest or other page); "
        "use https://leetcode.com/u/{username}/",
    )


def profile_url(username: str) -> str:
    return f"https://leetcode.com/u/{username}/"
