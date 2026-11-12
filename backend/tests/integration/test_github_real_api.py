"""Optional live tests against the REAL GitHub REST API (network required).

Opt in with ``INTEGRATION_GITHUB_LIVE=1``. Optional: ``INTEGRATION_GITHUB_USER``
(default ``octocat``, GitHub's public demo account) and ``INTEGRATION_GITHUB_TOKEN``.
Small limits keep the run to about 6 core + 2 search requests, well within the
unauthenticated quota (60/hour, 10 searches/minute).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.enums import AvailabilityStatus
from app.integrations.github import GitHubClient, GitHubNotFoundError
from app.services.github.collector import CollectorLimits, GitHubCollector
from tests.conftest import integration_env

pytestmark = pytest.mark.integration

VALID = set(AvailabilityStatus)


@pytest.fixture
async def client() -> AsyncIterator[GitHubClient]:
    if integration_env("INTEGRATION_GITHUB_LIVE") != "1":
        pytest.skip("INTEGRATION_GITHUB_LIVE=1 not set (real GitHub API test)")
    http = httpx.AsyncClient(follow_redirects=False)
    yield GitHubClient(
        http, token=integration_env("INTEGRATION_GITHUB_TOKEN"), timeout_s=20, max_retries=1
    )
    await http.aclose()


async def test_real_public_profile(client: GitHubClient) -> None:
    user = integration_env("INTEGRATION_GITHUB_USER") or "octocat"
    limits = CollectorLimits(max_repo_pages=1, readme_limit=2, max_event_pages=1, search_items=3)
    snap = await GitHubCollector(client, limits).collect(user)

    assert snap.profile.status is AvailabilityStatus.AVAILABLE
    assert snap.profile.data is not None
    assert snap.profile.data.login.lower() == user.lower()
    assert snap.profile.data.html_url == f"https://github.com/{snap.profile.data.login}"
    for name in ("repositories", "languages", "topics", "activity", "pull_requests", "issues"):
        section = getattr(snap, name)
        assert section.status in VALID
        assert section.provenance.source_platform == "github"
    if snap.repositories.status is AvailabilityStatus.AVAILABLE:
        repo = snap.repositories.items[0]
        assert repo.html_url.startswith(f"https://github.com/{snap.profile.data.login}/")
    dumped = snap.model_dump_json()
    assert "email" not in snap.profile.data.model_dump()
    assert not re.search(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", dumped
    )  # no e-mail kept
    assert snap.api_requests <= 8
    assert "core" in client.rate_limits


async def test_real_unknown_user(client: GitHubClient) -> None:
    with pytest.raises(GitHubNotFoundError):
        await GitHubCollector(client, CollectorLimits()).collect(
            "this-user-should-not-exist-9z7x5q"
        )
