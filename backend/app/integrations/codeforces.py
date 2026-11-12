"""Codeforces public API client (transport only - no candidate logic).

Security properties:

* The base URL is a constant (``https://codeforces.com/api/``) and only an
  allow-list of documented public methods can be called. Validated handles are
  passed as query parameters (URL-encoded by httpx); user input never becomes a
  host, scheme or path, so submitted URLs are never fetched (no SSRF).
* No API key is used: only public methods, no private account data. Source code
  is never requested (the API does not return it for these methods).
* Redirects are not followed except once to the same ``https://codeforces.com/api/`` prefix.

Reliability and API etiquette: Codeforces asks clients to make at most about one
call every 2 seconds. Every request goes through one shared pacer (an asyncio
lock plus a minimum interval), so concurrent syncs never exceed that rate.
Transient failures (5xx, timeouts, network errors, "Call limit exceeded") are
retried a bounded number of times with backoff. Responses with
``status: "FAILED"`` are mapped to typed errors.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.enums import AvailabilityStatus, CollectionErrorCode

logger = logging.getLogger("app.codeforces")

API_BASE = "https://codeforces.com/api/"
API_HOST = "codeforces.com"
USER_AGENT = "candidate-intelligence-platform"
# Documented public methods used by this integration (nothing else can be called).
ALLOWED_METHODS = frozenset({"user.info", "user.rating", "user.status"})
MAX_COMMENT_CHARS = 300


# --------------------------------------------------------------------------- errors
class CodeforcesError(Exception):
    code: CollectionErrorCode = CollectionErrorCode.API_ERROR
    availability: AvailabilityStatus = AvailabilityStatus.ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CodeforcesNotFoundError(CodeforcesError):
    code = CollectionErrorCode.INVALID_PROFILE
    availability = AvailabilityStatus.UNAVAILABLE


class CodeforcesRateLimitError(CodeforcesError):
    code = CollectionErrorCode.RATE_LIMITED
    availability = AvailabilityStatus.RATE_LIMITED


class CodeforcesTimeoutError(CodeforcesError):
    code = CollectionErrorCode.TIMEOUT
    availability = AvailabilityStatus.NETWORK_ERROR


class CodeforcesNetworkError(CodeforcesError):
    code = CollectionErrorCode.NETWORK_ERROR
    availability = AvailabilityStatus.NETWORK_ERROR


class CodeforcesUnavailableError(CodeforcesError):
    """Codeforces itself is down or under maintenance (5xx / non-JSON after retries)."""

    code = CollectionErrorCode.NETWORK_ERROR
    availability = AvailabilityStatus.NETWORK_ERROR


# --------------------------------------------------------------------------- results
@dataclass
class CodeforcesResponse:
    result: Any
    url: str  # API URL actually served (provenance; contains only public parameters)


def _is_api_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == API_HOST
        and parts.port in (None, 443)
        and parts.path.startswith("/api/")
    )


def _comment(body: Any) -> str:
    if isinstance(body, dict):
        return str(body.get("comment") or "")[:MAX_COMMENT_CHARS]
    return ""


def _is_rate_limit(comment: str) -> bool:
    return "limit exceeded" in comment.lower()


def _is_not_found(comment: str) -> bool:
    lowered = comment.lower()
    return lowered.startswith(("handle", "handles")) and "not found" in lowered


class CodeforcesClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        timeout_s: float,
        max_retries: int,
        min_interval_s: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http_client
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._min_interval_s = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self.requests_made = 0
        # Observed state for the status endpoint (no probe calls are made).
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None

    @property
    def min_interval_s(self) -> float:
        return self._min_interval_s

    @staticmethod
    def build_url(method: str) -> str:
        if method not in ALLOWED_METHODS:
            raise ValueError("unsupported Codeforces API method")
        return f"{API_BASE}{method}"

    async def _pace(self) -> None:
        """Wait until at least ``min_interval_s`` has passed since the last request."""
        if self._last_request is not None:
            wait = self._min_interval_s - (self._clock() - self._last_request)
            if wait > 0:
                await self._sleep(wait)
        self._last_request = self._clock()

    def _backoff(self, attempt: int) -> float:
        return max(self._min_interval_s, 1.0) * float(2 ** (attempt - 1))

    def _record_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_at = datetime.now(UTC)

    async def call(self, method: str, params: dict[str, Any]) -> CodeforcesResponse:
        """Call one public API method; return its ``result`` or raise CodeforcesError."""
        url = self.build_url(method)
        async with self._lock:  # one request at a time across all syncs
            try:
                response = await self._call(url, params)
            except CodeforcesError as exc:
                self._record_error(exc.message)
                raise
        self.last_success_at = datetime.now(UTC)
        return response

    async def _call(self, url: str, params: dict[str, Any] | None) -> CodeforcesResponse:
        attempt = 0
        redirected = False
        while True:
            attempt += 1
            retry = attempt <= self._max_retries
            await self._pace()
            try:
                self.requests_made += 1
                response = await self._http.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                    timeout=self._timeout_s,
                )
            except httpx.TimeoutException as exc:
                if retry:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise CodeforcesTimeoutError(
                    f"Codeforces did not respond within {self._timeout_s}s"
                ) from exc
            except httpx.HTTPError as exc:
                if retry:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise CodeforcesNetworkError(
                    f"Could not reach Codeforces ({type(exc).__name__})"
                ) from exc

            status = response.status_code
            if status in (301, 302, 307, 308):
                location = response.headers.get("location", "")
                if redirected or not _is_api_url(location):
                    raise CodeforcesError("Codeforces returned an unexpected redirect")
                url, params, redirected = location, None, True
                continue

            try:
                body = response.json() if response.content else None
            except ValueError:
                body = None
            comment = _comment(body)

            if status == 429 or _is_rate_limit(comment):
                if retry:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise CodeforcesRateLimitError("Codeforces API call limit exceeded")
            if status >= 500 and not (isinstance(body, dict) and body.get("status") == "FAILED"):
                if retry:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise CodeforcesUnavailableError(
                    f"Codeforces is temporarily unavailable (HTTP {status})"
                )
            if not isinstance(body, dict) or body.get("status") not in ("OK", "FAILED"):
                raise CodeforcesError(f"Codeforces returned a malformed response (HTTP {status})")
            if body["status"] == "FAILED":
                if _is_not_found(comment):
                    raise CodeforcesNotFoundError("Codeforces user not found")
                raise CodeforcesError(
                    f"Codeforces API error: {comment}" if comment else "Codeforces API error"
                )
            if "result" not in body:
                raise CodeforcesError("Codeforces returned a response without a result")
            return CodeforcesResponse(result=body["result"], url=str(response.url))
