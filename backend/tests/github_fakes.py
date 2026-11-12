"""Deterministic fake of the GitHub REST API (httpx.MockTransport) for tests.

Payloads deliberately contain an e-mail address, commit messages and author
e-mails so tests can prove they never reach snapshots, responses or logs.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.github import GitHubClient

LOGIN = "octo-dev"
USER_ID = 4242
PRIVATE_EMAIL = "octo.private@example.com"
COMMIT_MESSAGE = "secret-ish commit message"
README_TEXT = "# Data API\nA FastAPI service with PostgreSQL."


def user_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "login": LOGIN,
        "id": USER_ID,
        "type": "User",
        "name": "Octo Dev",
        "bio": "Backend developer",
        "company": "Example Corp",
        "location": "Springfield",
        "blog": "https://example.org",
        "email": PRIVATE_EMAIL,
        "public_repos": 3,
        "followers": 10,
        "following": 2,
        "created_at": "2018-04-01T10:00:00Z",
        "updated_at": "2026-10-01T10:00:00Z",
        "html_url": f"https://github.com/{LOGIN}",
    }
    data.update(overrides)
    return data


def repo(name: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name,
        "full_name": f"{LOGIN}/{name}",
        "private": False,
        "description": f"{name} description",
        "language": "Python",
        "html_url": f"https://github.com/{LOGIN}/{name}",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
        "pushed_at": "2026-09-01T00:00:00Z",
        "stargazers_count": 3,
        "forks_count": 1,
        "fork": False,
        "archived": False,
        "topics": ["fastapi", "postgresql"],
        "owner": {"login": LOGIN},
    }
    data.update(overrides)
    return data


DEFAULT_REPOS = [
    repo("data-api"),
    repo(
        "ml-notebooks",
        language="Jupyter Notebook",
        topics=["machine-learning"],
        pushed_at="2026-08-01T00:00:00Z",
    ),
    repo("forked-lib", fork=True, language="Go", topics=["go"]),
    repo(
        "old-site",
        archived=True,
        language="JavaScript",
        topics=[],
        pushed_at="2020-01-01T00:00:00Z",
    ),
]


def readme_payload(text: str = README_TEXT, **overrides: Any) -> dict[str, Any]:
    raw = text.encode()
    data: dict[str, Any] = {
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
        "size": len(raw),
        "html_url": f"https://github.com/{LOGIN}/data-api/blob/main/README.md",
    }
    data.update(overrides)
    return data


def events_payload() -> list[dict[str, Any]]:
    return [
        {
            "type": "PushEvent",
            "created_at": "2026-10-10T12:00:00Z",
            "repo": {"name": f"{LOGIN}/data-api"},
            "payload": {
                "size": 2,
                "commits": [
                    {"message": COMMIT_MESSAGE, "author": {"email": PRIVATE_EMAIL, "name": "Octo"}},
                    {"message": "another", "author": {"email": PRIVATE_EMAIL, "name": "Octo"}},
                ],
            },
        },
        {
            "type": "PullRequestEvent",
            "created_at": "2026-10-09T12:00:00Z",
            "repo": {"name": "other/project"},
        },
        {
            "type": "IssuesEvent",
            "created_at": "2026-10-01T12:00:00Z",
            "repo": {"name": "other/project"},
        },
    ]


def search_payload(kind: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": f"Fix bug ({kind})",
        "html_url": f"https://github.com/other/project/{'pull' if kind == 'pr' else 'issues'}/7",
        "repository_url": "https://api.github.com/repos/other/project",
        "state": "closed",
        "created_at": "2026-09-20T00:00:00Z",
        "closed_at": "2026-09-22T00:00:00Z",
        "user": {"login": LOGIN, "email": PRIVATE_EMAIL},
    }
    if kind == "pr":
        item["pull_request"] = {"merged_at": "2026-09-22T00:00:00Z"}
    return {"total_count": 1, "incomplete_results": False, "items": [item]}


Handler = Callable[[httpx.Request], httpx.Response]


def rate_headers(remaining: int = 59, resource: str = "core") -> dict[str, str]:
    return {
        "x-ratelimit-limit": "60",
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": "1893456000",
        "x-ratelimit-resource": resource,
    }


class FakeGitHub:
    """Routes by path; override individual routes with ``routes[path] = handler``."""

    def __init__(self, repos: list[Any] | None = None, user: dict[str, Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        repos = DEFAULT_REPOS if repos is None else repos
        self.routes: dict[str, Handler] = {
            f"/users/{LOGIN}": lambda r: httpx.Response(
                200, json=user or user_payload(), headers=rate_headers()
            ),
            f"/users/{LOGIN}/repos": lambda r: httpx.Response(
                200, json=repos, headers=rate_headers()
            ),
            f"/users/{LOGIN}/events/public": lambda r: httpx.Response(
                200, json=events_payload(), headers=rate_headers()
            ),
            "/search/issues": lambda r: httpx.Response(
                200,
                json=search_payload("pr" if "type:pr" in r.url.params.get("q", "") else "issue"),
                headers=rate_headers(9, "search"),
            ),
            "/rate_limit": lambda r: httpx.Response(
                200,
                json={
                    "resources": {
                        "core": {"limit": 60, "remaining": 55, "reset": 1893456000},
                        "search": {"limit": 10, "remaining": 10, "reset": 1893456000},
                    }
                },
            ),
        }
        for r in repos:
            if isinstance(r, dict) and isinstance(r.get("full_name"), str):
                path = f"/repos/{r['full_name']}/readme"
                self.routes.setdefault(
                    path,
                    lambda req: httpx.Response(200, json=readme_payload(), headers=rate_headers()),
                )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "api.github.com", request.url
        route = self.routes.get(request.url.path)
        if route is None:  # like the real API, logins in paths are case-insensitive
            lowered = request.url.path.lower()
            route = next((h for p, h in self.routes.items() if p.lower() == lowered), None)
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"}, headers=rate_headers())
        return route(request)

    def client(self, token: str | None = None, max_retries: int = 1) -> GitHubClient:
        async def no_sleep(_: float) -> None:
            return None

        return GitHubClient(
            httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
            token=token,
            timeout_s=5,
            max_retries=max_retries,
            sleep=no_sleep,
        )

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]
