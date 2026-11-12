"""Codeforces API client: fixed host, method allow-list, FAILED handling, retries, pacing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from app.core.enums import AvailabilityStatus, CollectionErrorCode
from app.integrations.codeforces import (
    CodeforcesClient,
    CodeforcesError,
    CodeforcesNetworkError,
    CodeforcesNotFoundError,
    CodeforcesRateLimitError,
    CodeforcesTimeoutError,
    CodeforcesUnavailableError,
)
from tests import codeforces_fakes as cf

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(
    handler: Handler, *, retries: int = 2, interval: float = 0.0, clock: Clock | None = None
) -> tuple[CodeforcesClient, Clock]:
    clock = clock or Clock()
    return (
        CodeforcesClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            timeout_s=5,
            max_retries=retries,
            min_interval_s=interval,
            sleep=clock.sleep,
            clock=clock,
        ),
        clock,
    )


class TestRequests:
    async def test_fixed_host_method_and_params(self) -> None:
        rec = Recorder(cf.ok([{"handle": "x"}]))
        client, _ = _client(rec)
        response = await client.call("user.info", {"handles": "x"})
        assert response.result == [{"handle": "x"}]
        req = rec.requests[0]
        assert str(req.url) == "https://codeforces.com/api/user.info?handles=x"
        assert req.headers["user-agent"] == "candidate-intelligence-platform"
        assert "authorization" not in req.headers
        assert response.url == str(req.url)
        assert client.requests_made == 1
        assert client.last_success_at is not None

    @pytest.mark.parametrize("method", ["contest.list", "user.friends", "../user.info", ""])
    async def test_only_allow_listed_methods(self, method: str) -> None:
        rec = Recorder(cf.ok([]))
        client, _ = _client(rec)
        with pytest.raises(ValueError, match="unsupported"):
            await client.call(method, {})
        assert rec.requests == []

    async def test_parameters_are_encoded(self) -> None:
        rec = Recorder(cf.ok([]))
        client, _ = _client(rec)
        await client.call("user.status", {"handle": "a&b=c", "from": 1, "count": 2})
        assert rec.requests[0].url.params["handle"] == "a&b=c"
        assert rec.requests[0].url.host == "codeforces.com"


class TestFailedResponses:
    async def test_handle_not_found(self) -> None:
        client, _ = _client(Recorder(cf.failed("handles: User with handle zz not found")))
        with pytest.raises(CodeforcesNotFoundError) as info:
            await client.call("user.info", {"handles": "zz"})
        assert info.value.code is CollectionErrorCode.INVALID_PROFILE
        assert info.value.availability is AvailabilityStatus.UNAVAILABLE
        assert client.last_error == "Codeforces user not found"

    async def test_other_failure_is_api_error_with_bounded_comment(self) -> None:
        client, _ = _client(Recorder(cf.failed("contestId: bad " + "x" * 1000)))
        with pytest.raises(CodeforcesError) as info:
            await client.call("user.rating", {"handle": "a"})
        assert type(info.value) is CodeforcesError
        assert info.value.code is CollectionErrorCode.API_ERROR
        assert len(info.value.message) < 400

    async def test_failed_without_comment(self) -> None:
        client, _ = _client(Recorder(httpx.Response(400, json={"status": "FAILED"})))
        with pytest.raises(CodeforcesError, match="Codeforces API error"):
            await client.call("user.rating", {"handle": "a"})

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="<html>maintenance</html>"),
            httpx.Response(200, json={"result": []}),
            httpx.Response(200, json=["not", "an", "object"]),
            httpx.Response(200, json={"status": "OK"}),
            httpx.Response(403, text="forbidden"),
            httpx.Response(200),
        ],
    )
    async def test_malformed_responses(self, response: httpx.Response) -> None:
        client, _ = _client(Recorder(response))
        with pytest.raises(CodeforcesError):
            await client.call("user.info", {"handles": "a"})


class TestRetries:
    async def test_call_limit_is_retried_with_backoff(self) -> None:
        rec = Recorder(cf.failed("Call limit exceeded", 503), cf.ok([]))
        client, clock = _client(rec, interval=2.0)
        assert (await client.call("user.rating", {"handle": "a"})).result == []
        assert len(rec.requests) == 2
        assert clock.sleeps
        assert max(clock.sleeps) >= 2.0

    async def test_call_limit_exhausted(self) -> None:
        rec = Recorder(cf.failed("Call limit exceeded"))
        client, _ = _client(rec, retries=2)
        with pytest.raises(CodeforcesRateLimitError) as info:
            await client.call("user.rating", {"handle": "a"})
        assert len(rec.requests) == 3
        assert info.value.availability is AvailabilityStatus.RATE_LIMITED

    async def test_http_429(self) -> None:
        client, _ = _client(Recorder(httpx.Response(429, text="slow down")), retries=0)
        with pytest.raises(CodeforcesRateLimitError):
            await client.call("user.rating", {"handle": "a"})

    async def test_5xx_retried_then_success(self) -> None:
        rec = Recorder(httpx.Response(502, text="bad gateway"), cf.ok([1]))
        client, _ = _client(rec)
        assert (await client.call("user.rating", {"handle": "a"})).result == [1]

    async def test_5xx_exhausted(self) -> None:
        rec = Recorder(httpx.Response(503, text="Codeforces is temporarily unavailable"))
        client, _ = _client(rec, retries=1)
        with pytest.raises(CodeforcesUnavailableError):
            await client.call("user.rating", {"handle": "a"})
        assert len(rec.requests) == 2

    async def test_5xx_with_failed_body_is_not_retried(self) -> None:
        rec = Recorder(cf.failed("internal problem", 500))
        client, _ = _client(rec)
        with pytest.raises(CodeforcesError, match="internal problem"):
            await client.call("user.rating", {"handle": "a"})
        assert len(rec.requests) == 1

    async def test_timeout(self) -> None:
        rec = Recorder(httpx.ReadTimeout("slow"))
        client, _ = _client(rec, retries=1)
        with pytest.raises(CodeforcesTimeoutError):
            await client.call("user.info", {"handles": "a"})
        assert len(rec.requests) == 2

    async def test_timeout_then_success(self) -> None:
        rec = Recorder(httpx.ConnectTimeout("slow"), cf.ok([]))
        client, _ = _client(rec)
        assert (await client.call("user.info", {"handles": "a"})).result == []

    async def test_network_error(self) -> None:
        rec = Recorder(httpx.ConnectError("refused"))
        client, _ = _client(rec, retries=0)
        with pytest.raises(CodeforcesNetworkError) as info:
            await client.call("user.info", {"handles": "a"})
        assert info.value.code is CollectionErrorCode.NETWORK_ERROR
        assert client.last_error_at is not None


class TestRedirects:
    async def test_same_api_redirect_followed_once(self) -> None:
        rec = Recorder(
            httpx.Response(
                302, headers={"location": "https://codeforces.com/api/user.info?handles=a"}
            ),
            cf.ok([]),
        )
        client, _ = _client(rec)
        assert (await client.call("user.info", {"handles": "a"})).result == []
        assert len(rec.requests) == 2

    @pytest.mark.parametrize(
        "location",
        [
            "https://evil.example/api/user.info",
            "http://codeforces.com/api/user.info",
            "https://codeforces.com:8443/api/user.info",
            "https://codeforces.com/profile/a",
            "/api/user.info",
        ],
    )
    async def test_foreign_redirect_refused(self, location: str) -> None:
        rec = Recorder(httpx.Response(302, headers={"location": location}))
        client, _ = _client(rec)
        with pytest.raises(CodeforcesError, match="redirect"):
            await client.call("user.info", {"handles": "a"})
        assert len(rec.requests) == 1

    async def test_second_redirect_refused(self) -> None:
        rec = Recorder(
            httpx.Response(302, headers={"location": "https://codeforces.com/api/user.info"})
        )
        client, _ = _client(rec)
        with pytest.raises(CodeforcesError, match="redirect"):
            await client.call("user.info", {"handles": "a"})
        assert len(rec.requests) == 2


class TestPacing:
    async def test_consecutive_calls_respect_min_interval(self) -> None:
        clock = Clock()
        client, _ = _client(Recorder(cf.ok([])), interval=2.0, clock=clock)
        for _ in range(3):
            await client.call("user.rating", {"handle": "a"})
        assert clock.sleeps == [2.0, 2.0]

    async def test_no_wait_when_interval_already_passed(self) -> None:
        clock = Clock()
        client, _ = _client(Recorder(cf.ok([])), interval=2.0, clock=clock)
        await client.call("user.rating", {"handle": "a"})
        clock.now += 5
        await client.call("user.rating", {"handle": "a"})
        assert clock.sleeps == []

    async def test_concurrent_calls_are_serialised(self) -> None:
        active = 0
        peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return cf.ok([])

        client = CodeforcesClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            timeout_s=5,
            max_retries=0,
            min_interval_s=0.0,
        )
        await asyncio.gather(*(client.call("user.rating", {"handle": "a"}) for _ in range(5)))
        assert peak == 1
        assert client.requests_made == 5
