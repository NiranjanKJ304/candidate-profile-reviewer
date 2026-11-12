"""Codeforces profile input validation and normalisation.

Accepts a handle or a profile URL ``https://codeforces.com/profile/{handle}``
(``http``/``www.`` tolerated, trailing slash, query and fragment ignored).
Everything else is rejected: contest, problem, blog and submission URLs, other
hosts and subdomains, credentials, ports and non-http schemes. The result is
only ever a handle; the API destination is fixed in the client, so submitted
URLs are never fetched (no SSRF).
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

# Codeforces handles: latin letters, digits, '_', '-' and (older accounts) '.',
# at most 24 characters, and at least one letter or digit.
HANDLE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,24}$")
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")
ALLOWED_HOSTS = frozenset({"codeforces.com", "www.codeforces.com"})


class InvalidCodeforcesInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _check_handle(handle: str) -> str:
    if not HANDLE_RE.fullmatch(handle) or not _HAS_ALNUM.search(handle):
        raise InvalidCodeforcesInputError(
            "invalid_handle",
            "Not a valid Codeforces handle (up to 24 latin letters, digits, '_', '-' or '.')",
        )
    return handle


def parse_codeforces_profile(raw: str) -> str:
    """Return the handle for a handle or profile URL; raise InvalidCodeforcesInputError."""
    value = unicodedata.normalize("NFKC", raw or "").strip()
    if not value:
        raise InvalidCodeforcesInputError("empty", "Enter a Codeforces handle or profile URL")
    if len(value) > 300:
        raise InvalidCodeforcesInputError("too_long", "The value is too long")
    if any(ch.isspace() for ch in value) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InvalidCodeforcesInputError(
            "malformed", "The value must not contain spaces or control characters"
        )

    lowered = value.lower()
    looks_like_url = "://" in value or lowered.startswith(
        ("codeforces.com/", "www.codeforces.com/")
    )
    if not looks_like_url:
        if "/" in value or ":" in value or "@" in value or "\\" in value:
            raise InvalidCodeforcesInputError(
                "malformed", "Enter a handle or a https://codeforces.com/profile/{handle} URL"
            )
        return _check_handle(value)

    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise InvalidCodeforcesInputError("malformed_url", "The URL is malformed") from exc

    if parts.scheme not in ("https", "http"):
        raise InvalidCodeforcesInputError(
            "unsupported_scheme", "Only https://codeforces.com profile URLs are accepted"
        )
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidCodeforcesInputError("malformed_url", "URLs with credentials are not accepted")
    if port is not None:
        raise InvalidCodeforcesInputError("unsupported_domain", "URLs with a port are not accepted")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidCodeforcesInputError(
            "unsupported_domain",
            "Only codeforces.com profile URLs are accepted "
            "(e.g. https://codeforces.com/profile/tourist)",
        )
    if "\\" in parts.path or "%" in parts.path:
        raise InvalidCodeforcesInputError("malformed_url", "The URL path is malformed")

    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        raise InvalidCodeforcesInputError("not_a_profile", "The URL does not contain a handle")
    if segments[0].lower() != "profile" or len(segments) != 2:
        raise InvalidCodeforcesInputError(
            "not_a_profile",
            "This is not a profile URL (contest, problem, blog or other page); "
            "use https://codeforces.com/profile/{handle}",
        )
    return _check_handle(segments[1])


def profile_url(handle: str) -> str:
    return f"https://codeforces.com/profile/{handle}"
