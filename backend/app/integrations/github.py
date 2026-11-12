"""GitHub REST API client (transport only - no candidate logic).

Security properties:

* The base URL is a constant (``https://api.github.com``). Callers pass a path
  built from *validated* identifiers; user input never becomes a host or scheme,
  so arbitrary URLs cannot be used as API destinations (SSRF).
* Redirects and pagination links are followed only when they point to
  ``https://api.github.com``.
* The optional token is sent only in the Authorization header and is never
  included in exceptions, logs or return values.

Reliability: timeouts, bounded retries with backoff for 5xx / network errors /
short secondary rate limits, rate-limit header tracking, Link-header pagination
with a page cap.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.enums import AvailabilityStatus, CollectionErrorCode

logger = logging.getLogger("app.github")

API_BASE = "https://api.github.com"
API_HOST = "api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "candidate-intelligence-platform"
MAX_SECONDARY_WAIT_S = 10.0

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


# --------------------------------------------------------------------------- errors
class GitHubError(Exception):
    code: CollectionErrorCode = CollectionErrorCode.API_ERROR
    availability: AvailabilityStatus = AvailabilityStatus.ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GitHubNotFoundError(GitHubError):
    code = CollectionErrorCode.INVALID_PROFILE
    availability = AvailabilityStatus.UNAVAILABLE


class GitHubUnavailableError(GitHubError):
    """HTTP 451 / blocked content."""

    code = CollectionErrorCode.INVALID_PROFILE
    availability = AvailabilityStatus.UNAVAILABLE


class GitHubRateLimitError(GitHubError):
    code = CollectionErrorCode.RATE_LIMITED
    availability = AvailabilityStatus.RATE_LIMITED

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GitHubPermissionError(GitHubError):
    code = CollectionErrorCode.PERMISSION_DENIED
    availability = AvailabilityStatus.PERMISSION_DENIED


class GitHubAuthError(GitHubError):
    code = CollectionErrorCode.AUTH_FAILED
    availability = AvailabilityStatus.PERMISSION_DENIED


class GitHubTimeoutError(GitHubError):
    code = CollectionErrorCode.TIMEOUT
    availability = AvailabilityStatus.NETWORK_ERROR


class GitHubNetworkError(GitHubError):
    code = CollectionErrorCode.NETWORK_ERROR
    availability = AvailabilityStatus.NETWORK_ERROR


class GitHubValidationError(GitHubError):
    """HTTP 422 (e.g. search cannot run for this account)."""

    code = CollectionErrorCode.API_ERROR
    availability = AvailabilityStatus.UNAVAILABLE


# --------------------------------------------------------------------------- results
@dataclass(frozen=True)
class RateLimitInfo:
    resource: str
    limit: int
    remaining: int
    reset_at: datetime | None


@dataclass
class GitHubResponse:
    data: Any
    status: int
    url: str  # API URL actually served (provenance)
    link: str | None = None  # raw Link header (pagination)


@dataclass
class PageResult:
    items: list[Any]
    pages: int
    truncated: bool  # more pages existed than were fetched
    first_url: str
    warnings: list[str] = field(default_factory=list)


def segment(value: str) -> str:
    """URL-encode one path segment (validated identifiers only)."""
    return quote(value, safe="")


def _is_api_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == API_HOST and parts.port in (None, 443)


def _parse_reset(value: str | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (ValueError, OverflowError, OSError):
        return None


class GitHubClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        token: str | None,
        timeout_s: float,
        max_retries: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._token = token
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._sleep = sleep
        self.rate_limits: dict[str, RateLimitInfo] = {}
        self.requests_made = 0

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def build_url(path: str) -> str:
        if (
            not path.startswith("/")
            or "//" in path  # empty segments / scheme-relative tricks
            or "/../" in f"{path}/"
            or "\\" in path
        ):
            raise ValueError("unsafe GitHub API path")
        return f"{API_BASE}{path}"

    def _record_rate_limit(self, response: httpx.Response) -> None:
        limit = response.headers.get("x-ratelimit-limit")
        remaining = response.headers.get("x-ratelimit-remaining")
        if limit is None or remaining is None:
            return
        resource = response.headers.get("x-ratelimit-resource", "core")
        try:
            self.rate_limits[resource] = RateLimitInfo(
                resource=resource,
                limit=int(limit),
                remaining=int(remaining),
                reset_at=_parse_reset(response.headers.get("x-ratelimit-reset")),
            )
        except ValueError:
            return

    async def get(self, path: str, params: dict[str, Any] | None = None) -> GitHubResponse:
        return await self._get_url(self.build_url(path), params)

    async def _get_url(self, url: str, params: dict[str, Any] | None) -> GitHubResponse:
        attempt = 0
        redirected = False
        while True:
            attempt += 1
            try:
                self.requests_made += 1
                response = await self._http.get(
                    url, params=params, headers=self._headers(), timeout=self._timeout_s
                )
            except httpx.TimeoutException as exc:
                if attempt <= self._max_retries:
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise GitHubTimeoutError(
                    f"GitHub did not respond within {self._timeout_s}s"
                ) from exc
            except httpx.HTTPError as exc:
                if attempt <= self._max_retries:
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise GitHubNetworkError(f"Could not reach GitHub ({type(exc).__name__})") from exc

            self._record_rate_limit(response)
            status = response.status_code
            served = str(response.url)

            if status in (301, 302, 307, 308):
                location = response.headers.get("location", "")
                if redirected or not _is_api_url(location):
                    raise GitHubError("GitHub returned an unexpected redirect")
                url, params, redirected = location, None, True
                continue
            if 200 <= status < 300:
                try:
                    data = response.json() if response.content else None
                except ValueError as exc:
                    raise GitHubError("GitHub returned a malformed response") from exc
                return GitHubResponse(
                    data=data, status=status, url=served, link=response.headers.get("link")
                )
            if status == 404:
                raise GitHubNotFoundError("Not found on GitHub")
            if status == 451:
                raise GitHubUnavailableError("This resource is unavailable on GitHub (HTTP 451)")
            if status == 401:
                raise GitHubAuthError("GitHub rejected the configured GITHUB_TOKEN (HTTP 401)")
            if status in (403, 429):
                retry = self._rate_limit_or_permission(response)
                if isinstance(retry, float):
                    if attempt <= self._max_retries:
                        await self._sleep(retry)
                        continue
                    raise GitHubRateLimitError("GitHub secondary rate limit reached")
                raise retry
            if status == 422:
                raise GitHubValidationError("GitHub could not process this request (HTTP 422)")
            if status >= 500 and attempt <= self._max_retries:
                await self._sleep(0.5 * 2 ** (attempt - 1))
                continue
            raise GitHubError(f"GitHub returned HTTP {status}")

    def _rate_limit_or_permission(self, response: httpx.Response) -> GitHubError | float:
        """Classify 403/429: primary limit, short secondary limit (retry delay) or denial."""
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = _parse_reset(response.headers.get("x-ratelimit-reset"))
            when = f" until {reset.isoformat()}" if reset else ""
            return GitHubRateLimitError(f"GitHub API rate limit exhausted{when}", reset)
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = MAX_SECONDARY_WAIT_S + 1
            if 0 <= delay <= MAX_SECONDARY_WAIT_S:
                return delay
            return GitHubRateLimitError("GitHub secondary rate limit reached")
        message = ""
        try:
            body = response.json()
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
        except ValueError:
            pass
        if response.status_code == 429 or "rate limit" in message.lower():
            return GitHubRateLimitError("GitHub API rate limit reached")
        return GitHubPermissionError("GitHub denied access to this resource (HTTP 403)")

    async def get_paginated(
        self, path: str, params: dict[str, Any] | None, *, max_pages: int
    ) -> PageResult:
        """Follow ``Link: rel="next"`` up to ``max_pages`` (only to api.github.com)."""
        url = self.build_url(path)
        current_params = params
        items: list[Any] = []
        warnings: list[str] = []
        pages = 0
        first_url = url
        truncated = False
        while url and pages < max_pages:
            response = await self._get_url(url, current_params)
            pages += 1
            if pages == 1:
                first_url = response.url
            if not isinstance(response.data, list):
                raise GitHubError("GitHub returned an unexpected payload (expected a list)")
            items.extend(response.data)
            next_url = self._next_link(response)
            if next_url and not _is_api_url(next_url):
                warnings.append("Ignored a pagination link that did not point to api.github.com")
                next_url = None
            url = next_url or ""
            current_params = None  # the next link already carries the query
        if url and pages >= max_pages:
            truncated = True
        return PageResult(
            items=items, pages=pages, truncated=truncated, first_url=first_url, warnings=warnings
        )

    @staticmethod
    def _next_link(response: GitHubResponse) -> str | None:
        if not response.link:
            return None
        match = _LINK_NEXT.search(response.link)
        return match.group(1) if match else None

    async def rate_limit_status(self) -> dict[str, RateLimitInfo]:
        """GET /rate_limit (does not count against the limit)."""
        response = await self.get("/rate_limit")
        resources = (
            (response.data or {}).get("resources", {}) if isinstance(response.data, dict) else {}
        )
        out: dict[str, RateLimitInfo] = {}
        for name in ("core", "search"):
            raw = resources.get(name)
            if isinstance(raw, dict):
                try:
                    out[name] = RateLimitInfo(
                        resource=name,
                        limit=int(raw["limit"]),
                        remaining=int(raw["remaining"]),
                        reset_at=_parse_reset(str(raw.get("reset", ""))),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return out
