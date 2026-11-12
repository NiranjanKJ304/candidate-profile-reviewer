"""Deterministic fake of the Codeforces public API (httpx.MockTransport) for tests.

Payloads deliberately contain an e-mail address, VK/OpenID ids and team-mate
handles so tests can prove they never reach snapshots, responses or logs.
``user.status`` honours ``from`` / ``count`` like the real API.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.codeforces import CodeforcesClient

HANDLE = "algo_dev"
PRIVATE_EMAIL = "algo.private@example.com"
TEAMMATE = "secret_teammate"
T0 = 1_760_000_000  # 2025-10-09


def user_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "handle": HANDLE,
        "email": PRIVATE_EMAIL,
        "vkId": "vk-123",
        "openId": "openid-456",
        "firstName": "Algo",
        "lastName": "Dev",
        "country": "Exampleland",
        "city": "Springfield",
        "organization": "Example University",
        "contribution": 5,
        "rank": "expert",
        "rating": 1650,
        "maxRank": "candidate master",
        "maxRating": 1920,
        "lastOnlineTimeSeconds": T0 + 86_400 * 30,
        "registrationTimeSeconds": 1_500_000_000,
        "friendOfCount": 12,
        "avatar": "https://userpic.codeforces.org/no-avatar.jpg",
    }
    data.update(overrides)
    return data


def rating_payload() -> list[dict[str, Any]]:
    return [
        {
            "contestId": 1000,
            "contestName": "Codeforces Round 1 (Div. 2)",
            "handle": HANDLE,
            "rank": 900,
            "ratingUpdateTimeSeconds": T0 - 86_400 * 60,
            "oldRating": 1500,
            "newRating": 1600,
        },
        {
            "contestId": 1010,
            "contestName": "Codeforces Round 2 (Div. 2)",
            "handle": HANDLE,
            "rank": 1200,
            "ratingUpdateTimeSeconds": T0 - 86_400 * 30,
            "oldRating": 1600,
            "newRating": 1650,
        },
    ]


def submission(
    sub_id: int,
    *,
    contest: int = 1010,
    index: str = "A",
    verdict: str | None = "OK",
    rating: int | None = 1200,
    tags: list[str] | None = None,
    language: str = "Python 3",
    at: int | None = None,
    participant: str = "PRACTICE",
    relative: int = 2_147_483_647,
) -> dict[str, Any]:
    problem: dict[str, Any] = {
        "contestId": contest,
        "index": index,
        "name": f"Problem {contest}{index}",
        "type": "PROGRAMMING",
        "tags": ["math"] if tags is None else tags,
    }
    if rating is not None:
        problem["rating"] = rating
    data: dict[str, Any] = {
        "id": sub_id,
        "contestId": contest,
        "creationTimeSeconds": at if at is not None else T0 - sub_id * 3600,
        "relativeTimeSeconds": relative,
        "problem": problem,
        "author": {
            "contestId": contest,
            "members": [{"handle": HANDLE}, {"handle": TEAMMATE}],
            "participantType": participant,
            "ghost": False,
        },
        "programmingLanguage": language,
        "testset": "TESTS",
        "passedTestCount": 12,
        "timeConsumedMillis": 46,
        "memoryConsumedBytes": 102400,
    }
    if verdict is not None:
        data["verdict"] = verdict
    return data


def default_submissions() -> list[dict[str, Any]]:
    """Newest first. 6 submissions, 4 unique problems, 3 accepted unique problems."""
    return [
        submission(1, index="C", verdict="OK", rating=1600, tags=["dp", "greedy"],
                   language="C++17", participant="CONTESTANT", relative=3600),
        submission(2, index="C", verdict="WRONG_ANSWER", rating=1600, tags=["dp", "greedy"],
                   language="C++17", participant="CONTESTANT", relative=1800),
        submission(3, index="B", verdict="OK", rating=1200, tags=["math"]),
        submission(4, index="B", verdict="OK", rating=1200, tags=["math"]),
        submission(5, contest=1000, index="A", verdict="OK", rating=None, tags=["implementation"]),
        submission(6, contest=1000, index="D", verdict="TIME_LIMIT_EXCEEDED", rating=2000,
                   tags=["graphs"]),
    ]  # fmt: skip


Handler = Callable[[httpx.Request], httpx.Response]


def ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"status": "OK", "result": result})


def failed(comment: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"status": "FAILED", "comment": comment})


class FakeCodeforces:
    """Routes by API method; override with ``routes[method] = handler``."""

    def __init__(
        self,
        *,
        user: dict[str, Any] | None = None,
        rating: list[Any] | None = None,
        submissions: list[Any] | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.user = user or user_payload()
        self.rating = rating_payload() if rating is None else rating
        self.submissions = default_submissions() if submissions is None else submissions
        self.routes: dict[str, Handler] = {
            "user.info": self._user_info,
            "user.rating": lambda r: ok(self.rating),
            "user.status": self._status,
        }

    def _user_info(self, request: httpx.Request) -> httpx.Response:
        handles = request.url.params.get("handles", "")
        if handles.lower() != str(self.user["handle"]).lower():
            return failed(f"handles: User with handle {handles} not found")
        return ok([self.user])

    def _status(self, request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("from", "1"))
        count = int(request.url.params.get("count", "1000000"))
        return ok(self.submissions[start - 1 : start - 1 + count])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "codeforces.com", request.url
        assert request.url.path.startswith("/api/"), request.url
        route = self.routes.get(request.url.path.removeprefix("/api/"))
        if route is None:
            return failed("Unknown method", 404)
        return route(request)

    def client(self, max_retries: int = 1, min_interval_s: float = 0.0) -> CodeforcesClient:
        async def no_sleep(_: float) -> None:
            return None

        return CodeforcesClient(
            httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
            timeout_s=5,
            max_retries=max_retries,
            min_interval_s=min_interval_s,
            sleep=no_sleep,
        )

    def methods(self) -> list[str]:
        return [r.url.path.removeprefix("/api/") for r in self.requests]
