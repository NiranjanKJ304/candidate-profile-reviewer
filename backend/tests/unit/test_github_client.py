"""GitHub REST client: headers, pagination, redirects, rate limits, errors, secrets."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import pytest

from app.core.enums import AvailabilityStatus, CollectionErrorCode
from app.integrations.github import (
    API_VERSION,
    USER_AGENT,
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    GitHubUnavailableError,
    GitHubValidationError,
    segment,
)
from tests.github_fakes import rate_headers

TOKEN = "ghp_SyntheticTestToken000000000000000000"


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    token: str | None = None,
    retries: int = 2,
) -> tuple[GitHubClient, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    client = GitHubClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        token=token,
        timeout_s=5,
        max_retries=retries,
        sleep=sleep,
    )
    return client, slept


class TestRequests:
    async def test_headers_without_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True}, headers=rate_headers(41))

        client, _ = _client(handler)
        result = await client.get("/users/octocat")
        assert result.data == {"ok": True}
        assert result.url == "https://api.github.com/users/octocat"
        h = seen[0].headers
        assert h["accept"] == "application/vnd.github+json"
        assert h["x-github-api-version"] == API_VERSION
        assert h["user-agent"] == USER_AGENT
        assert "authorization" not in h
        assert client.authenticated is False
        assert client.rate_limits["core"].remaining == 41

    async def test_token_sent_only_as_bearer_header(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        client, _ = _client(handler, token=TOKEN)
        await client.get("/users/octocat")
        assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(seen[0].url)
        assert client.authenticated is True

    @pytest.mark.parametrize(
        "path", ["users/x", "https://evil.example/x", "/users/../admin", "/a\\b", "//evil.example"]
    )
    def test_unsafe_paths_are_refused(self, path: str) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            GitHubClient.build_url(path)

    def test_segment_encodes_separators(self) -> None:
        assert segment("a/b?c#d") == "a%2Fb%3Fc%23d"


class TestPagination:
    async def test_follows_next_links_until_done(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            headers = rate_headers()
            if page < 3:
                headers["link"] = (
                    f'<https://api.github.com/users/x/repos?per_page=1&page={page + 1}>; rel="next"'
                )
            return httpx.Response(200, json=[{"n": page}], headers=headers)

        client, _ = _client(handler)
        result = await client.get_paginated("/users/x/repos", {"per_page": 1}, max_pages=10)
        assert [i["n"] for i in result.items] == [1, 2, 3]
        assert result.pages == 3
        assert result.truncated is False

    async def test_page_cap_sets_truncated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(
                200,
                json=[{"n": page}],
                headers={
                    "link": f'<https://api.github.com/users/x/repos?page={page + 1}>; rel="next"'
                },
            )

        client, _ = _client(handler)
        result = await client.get_paginated("/users/x/repos", None, max_pages=2)
        assert result.pages == 2
        assert result.truncated is True

    async def test_foreign_next_link_is_ignored(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(
                200, json=[1], headers={"link": '<https://evil.example/steal?page=2>; rel="next"'}
            )

        client, _ = _client(handler)
        result = await client.get_paginated("/users/x/repos", None, max_pages=5)
        assert len(calls) == 1
        assert result.warnings
        assert all("evil.example" not in c for c in calls)

    async def test_non_list_payload_is_an_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"message": "odd"}))
        with pytest.raises(GitHubError, match="expected a list"):
            await client.get_paginated("/users/x/repos", None, max_pages=1)


class TestRedirects:
    async def test_redirect_within_api_host_is_followed_once(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repos/old/name/readme":
                return httpx.Response(
                    301, headers={"location": "https://api.github.com/repositories/9/readme"}
                )
            return httpx.Response(200, json={"moved": True})

        client, _ = _client(handler)
        assert (await client.get("/repos/old/name/readme")).data == {"moved": True}

    @pytest.mark.parametrize(
        "location",
        [
            "https://evil.example/x",
            "http://api.github.com/x",
            "https://api.github.com:444/x",
            "/relative",
        ],
    )
    async def test_redirect_elsewhere_is_refused(self, location: str) -> None:
        client, _ = _client(lambda r: httpx.Response(302, headers={"location": location}))
        with pytest.raises(GitHubError, match="unexpected redirect"):
            await client.get("/users/x")


class TestErrors:
    @pytest.mark.parametrize(
        ("status", "exc", "code"),
        [
            (404, GitHubNotFoundError, CollectionErrorCode.INVALID_PROFILE),
            (451, GitHubUnavailableError, CollectionErrorCode.INVALID_PROFILE),
            (401, GitHubAuthError, CollectionErrorCode.AUTH_FAILED),
            (422, GitHubValidationError, CollectionErrorCode.API_ERROR),
            (403, GitHubPermissionError, CollectionErrorCode.PERMISSION_DENIED),
        ],
    )
    async def test_status_mapping(
        self, status: int, exc: type[GitHubError], code: CollectionErrorCode
    ) -> None:
        client, _ = _client(lambda r: httpx.Response(status, json={"message": "x"}))
        with pytest.raises(exc) as info:
            await client.get("/users/x")
        assert info.value.code is code

    async def test_primary_rate_limit(self) -> None:
        client, slept = _client(
            lambda r: httpx.Response(
                403, json={"message": "API rate limit exceeded"}, headers=rate_headers(0)
            )
        )
        with pytest.raises(GitHubRateLimitError) as info:
            await client.get("/users/x")
        assert info.value.reset_at is not None
        assert info.value.availability is AvailabilityStatus.RATE_LIMITED
        assert slept == []  # primary limit is never waited out

    async def test_429_is_rate_limited(self) -> None:
        client, _ = _client(lambda r: httpx.Response(429, json={}))
        with pytest.raises(GitHubRateLimitError):
            await client.get("/users/x")

    async def test_rate_limit_message_without_headers(self) -> None:
        client, _ = _client(
            lambda r: httpx.Response(
                403, json={"message": "You have exceeded a secondary rate limit"}
            )
        )
        with pytest.raises(GitHubRateLimitError):
            await client.get("/users/x")

    async def test_short_secondary_limit_is_retried(self) -> None:
        responses = [
            httpx.Response(403, headers={"retry-after": "2"}),
            httpx.Response(200, json={"ok": 1}),
        ]
        client, slept = _client(lambda r: responses.pop(0))
        assert (await client.get("/users/x")).data == {"ok": 1}
        assert slept == [2.0]

    async def test_long_secondary_limit_is_not_waited(self) -> None:
        client, slept = _client(lambda r: httpx.Response(403, headers={"retry-after": "600"}))
        with pytest.raises(GitHubRateLimitError):
            await client.get("/users/x")
        assert slept == []

    async def test_5xx_retried_then_succeeds(self) -> None:
        responses = [httpx.Response(502), httpx.Response(503), httpx.Response(200, json=[])]
        client, slept = _client(lambda r: responses.pop(0))
        assert (await client.get("/users/x")).data == []
        assert slept == [0.5, 1.0]

    async def test_5xx_exhausted(self) -> None:
        client, _ = _client(lambda r: httpx.Response(500), retries=1)
        with pytest.raises(GitHubError, match="HTTP 500"):
            await client.get("/users/x")

    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        client, slept = _client(handler, retries=1)
        with pytest.raises(GitHubTimeoutError):
            await client.get("/users/x")
        assert len(slept) == 1

    async def test_network_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client, _ = _client(handler, retries=0)
        with pytest.raises(GitHubNetworkError, match="ConnectError"):
            await client.get("/users/x")

    async def test_malformed_json(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, text="<html>not json"))
        with pytest.raises(GitHubError, match="malformed"):
            await client.get("/users/x")


class TestSecrets:
    async def test_token_never_in_errors_or_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        for status in (401, 403, 404, 500):

            def handler(request: httpx.Request, code: int = status) -> httpx.Response:
                return httpx.Response(code, json={"message": "x"})

            client, _ = _client(handler, token=TOKEN, retries=0)
            with pytest.raises(GitHubError) as info:
                await client.get("/users/x")
            assert TOKEN not in str(info.value)
            assert TOKEN not in repr(info.value)
        assert TOKEN not in caplog.text


async def test_rate_limit_status_parses_core_and_search() -> None:
    payload = {
        "resources": {
            "core": {"limit": 5000, "remaining": 4999, "reset": 1893456000},
            "search": {"limit": 30, "remaining": 30, "reset": 1893456000},
            "graphql": {"limit": 1},
        }
    }
    client, _ = _client(lambda r: httpx.Response(200, json=payload))
    rates = await client.rate_limit_status()
    assert set(rates) == {"core", "search"}
    assert rates["core"].remaining == 4999
