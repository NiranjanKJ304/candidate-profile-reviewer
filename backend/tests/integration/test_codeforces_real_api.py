"""Optional live tests against the REAL Codeforces API (network required).

Opt in with ``INTEGRATION_CODEFORCES_LIVE=1``. Optional ``INTEGRATION_CODEFORCES_HANDLE``
(default ``tourist``, a well-known public competitive programmer). The run makes
4 paced requests (one call per 2 s, as Codeforces asks).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.enums import AvailabilityStatus
from app.integrations.codeforces import CodeforcesClient, CodeforcesNotFoundError
from app.services.codeforces.collector import CodeforcesCollector, CollectorLimits
from tests.conftest import integration_env

pytestmark = pytest.mark.integration

VALID = set(AvailabilityStatus)


@pytest.fixture(scope="module")
def handle() -> str:
    return integration_env("INTEGRATION_CODEFORCES_HANDLE") or "tourist"


@pytest.fixture
async def client() -> AsyncIterator[CodeforcesClient]:
    if integration_env("INTEGRATION_CODEFORCES_LIVE") != "1":
        pytest.skip("INTEGRATION_CODEFORCES_LIVE=1 not set (real Codeforces API test)")
    http = httpx.AsyncClient(follow_redirects=False)
    yield CodeforcesClient(http, timeout_s=20, max_retries=1, min_interval_s=2.0)
    await http.aclose()


async def test_real_public_profile(client: CodeforcesClient, handle: str) -> None:
    snap = await CodeforcesCollector(client, CollectorLimits(submission_limit=20)).collect(handle)

    assert snap.profile.status is AvailabilityStatus.AVAILABLE
    assert snap.profile.data is not None
    assert snap.profile.data.handle.lower() == handle.lower()
    assert (
        snap.profile.data.profile_url
        == f"https://codeforces.com/profile/{snap.profile.data.handle}"
    )
    for name in ("rating_history", "submissions", "analytics"):
        section = getattr(snap, name)
        assert section.status in VALID
        assert section.provenance.source_platform == "codeforces"
    assert snap.submissions.requested_limit == 20
    assert len(snap.submissions.items) <= 20
    if snap.submissions.status is AvailabilityStatus.AVAILABLE:
        assert snap.analytics.submissions is not None
        stats = snap.analytics.submissions
        assert stats.total_submissions == len(snap.submissions.items)
        assert stats.accepted_unique_problems <= stats.accepted_submissions
    if snap.rating_history.status is AvailabilityStatus.AVAILABLE:
        assert snap.analytics.lifetime.rated_contests == len(snap.rating_history.items)
    dumped = snap.model_dump_json()
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", dumped)
    assert '"members"' not in dumped
    assert '"source"' not in dumped
    assert snap.api_requests <= 4


async def test_real_unknown_handle(client: CodeforcesClient) -> None:
    with pytest.raises(CodeforcesNotFoundError):
        await CodeforcesCollector(client, CollectorLimits()).collect("no_such_user_9z7x5q")
