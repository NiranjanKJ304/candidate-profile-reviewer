"""GitHub profile input validation and normalisation.

Accepts a username (optionally ``@username``) or a profile URL
``https://github.com/{username}`` (``http``/``www.`` tolerated, trailing slash,
query and fragment ignored). Everything else is rejected, including repository
URLs, other github.com pages, other hosts/subdomains, credentials, ports and
non-http schemes. The result is only ever a username: the API destination is
fixed in the client, so submitted URLs are never fetched (no SSRF).
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

# GitHub: 1-39 chars, alphanumerics and single hyphens, no leading/trailing hyphen.
USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})

# First path segments that are github.com features, not user profiles.
RESERVED_PATHS = frozenset(
    {
        "about",
        "account",
        "apps",
        "blog",
        "codespaces",
        "collections",
        "contact",
        "copilot",
        "customer-stories",
        "dashboard",
        "enterprise",
        "events",
        "explore",
        "features",
        "login",
        "logout",
        "marketplace",
        "new",
        "notifications",
        "organizations",
        "orgs",
        "pricing",
        "pulls",
        "issues",
        "search",
        "security",
        "sessions",
        "settings",
        "site",
        "sponsors",
        "stars",
        "team",
        "topics",
        "trending",
        "watching",
        "join",
        "readme",
        "signup",
        "github",
        "users",
    }
)


class InvalidGitHubInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _check_username(name: str) -> str:
    if not USERNAME_RE.fullmatch(name):
        raise InvalidGitHubInputError(
            "invalid_username",
            "Not a valid GitHub username (1-39 letters, digits or single hyphens; "
            "cannot start or end with a hyphen)",
        )
    if name.lower() in RESERVED_PATHS:
        raise InvalidGitHubInputError(
            "invalid_username", f"'{name}' is a GitHub page, not a user profile"
        )
    return name


def parse_github_profile(raw: str) -> str:
    """Return the username for a username or profile URL; raise InvalidGitHubInputError."""
    value = unicodedata.normalize("NFKC", raw or "").strip()
    if not value:
        raise InvalidGitHubInputError("empty", "Enter a GitHub username or profile URL")
    if len(value) > 300:
        raise InvalidGitHubInputError("too_long", "The value is too long")
    if any(ch.isspace() for ch in value) or any(ord(ch) < 32 for ch in value):
        raise InvalidGitHubInputError(
            "malformed", "The value must not contain spaces or control characters"
        )

    lowered = value.lower()
    looks_like_url = "://" in value or lowered.startswith(("github.com/", "www.github.com/"))
    if not looks_like_url:
        if "/" in value or "." in value or ":" in value or "@" in value[1:]:
            raise InvalidGitHubInputError(
                "malformed", "Enter a username or a https://github.com/{username} URL"
            )
        return _check_username(value.removeprefix("@"))

    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise InvalidGitHubInputError("malformed_url", "The URL is malformed") from exc

    if parts.scheme not in ("https", "http"):
        raise InvalidGitHubInputError(
            "unsupported_scheme", "Only https://github.com profile URLs are accepted"
        )
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidGitHubInputError("malformed_url", "URLs with credentials are not accepted")
    if port is not None:
        raise InvalidGitHubInputError("unsupported_domain", "URLs with a port are not accepted")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidGitHubInputError(
            "unsupported_domain",
            "Only github.com profile URLs are accepted (e.g. https://github.com/octocat)",
        )

    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        raise InvalidGitHubInputError("not_a_profile", "The URL does not contain a username")
    if len(segments) > 1:
        raise InvalidGitHubInputError(
            "not_a_profile",
            "This is not a profile URL (repository or sub-page); use https://github.com/{username}",
        )
    return _check_username(segments[0])


def profile_url(username: str) -> str:
    return f"https://github.com/{username}"
