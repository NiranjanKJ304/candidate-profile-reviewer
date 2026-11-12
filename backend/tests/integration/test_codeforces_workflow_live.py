"""Codeforces profile workflow against live PostgreSQL (isolated temporary schema).

Real: HTTP API, validation, background sync, normalisation, analytics, persistence.
Deterministic: Codeforces responses come from tests/codeforces_fakes.py (no network).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.main import create_app
from app.models import DeveloperProfile
from app.services.codeforces.collector import CodeforcesCollector, CollectorLimits
from app.services.codeforces.sync import CodeforcesSyncService
from app.services.github.collector import CollectorLimits as GitHubLimits
from app.services.github.collector import GitHubCollector
from app.services.github.sync import GitHubSyncService
from tests import codeforces_fakes as cf
from tests import github_fakes as gf
from tests.conftest import SettingsFactory, client_for

pytestmark = pytest.mark.integration


class Harness:
    def __init__(self, app: FastAPI, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.app = app
        self.sessions = sessions
        self.client: httpx.AsyncClient
        self.enabled = True

    def use(self, fake: cf.FakeCodeforces, **limits: Any) -> None:
        client = fake.client()
        self.app.state.codeforces_client = client
        self.app.state.codeforces_sync = CodeforcesSyncService(
            session_factory=self.sessions,
            collector=CodeforcesCollector(client, CollectorLimits(**limits)),
            enabled=lambda: self.enabled,
        )

    async def candidate(self, name: str = "Jordan Sample") -> str:
        resp = await self.client.post("/api/v1/candidates", json={"full_name": name})
        assert resp.status_code == 201
        return str(resp.json()["id"])

    def url(self, cid: str, suffix: str = "") -> str:
        return f"/api/v1/candidates/{cid}/codeforces{suffix}"

    async def add(self, cid: str, value: str = cf.HANDLE) -> httpx.Response:
        return await self.client.post(self.url(cid), json={"profile": value})

    async def get(self, cid: str) -> dict[str, Any]:
        resp = await self.client.get(self.url(cid))
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body


@pytest.fixture
async def h(migrated_engine: AsyncEngine, make_settings: SettingsFactory) -> AsyncIterator[Harness]:
    app = create_app(make_settings(enabled_platforms="github,codeforces"))
    sessions = async_sessionmaker(migrated_engine, expire_on_commit=False)
    app.state.db_engine = migrated_engine
    app.state.session_factory = sessions
    harness = Harness(app, sessions)
    harness.use(cf.FakeCodeforces())
    async with client_for(app) as client:
        harness.client = client
        yield harness


async def test_add_sync_and_retrieve(h: Harness) -> None:
    cid = await h.candidate()
    resp = await h.add(cid, f"https://codeforces.com/profile/{cf.HANDLE.upper()}")
    assert resp.status_code == 202, resp.text

    body = await h.get(cid)  # background sync completed during the request
    assert body["sync_status"] == "completed", body
    assert body["collection_status"] == "collected"
    assert body["ownership_status"] == "unverified"
    assert body["handle"] == cf.HANDLE  # canonical handle from user.info
    assert body["freshness"]["stale"] is False
    data = body["data"]
    assert data["profile"]["data"]["rating"] == 1650
    assert data["profile"]["provenance"] == {
        "source_platform": "codeforces",
        "source_url": f"https://codeforces.com/profile/{cf.HANDLE}",
        "api_method": "user.info",
        "collected_at": data["profile"]["provenance"]["collected_at"],
    }
    assert len(data["rating_history"]["items"]) == 2
    stats = data["analytics"]["submissions"]
    assert stats["total_submissions"] == 6
    assert stats["accepted_submissions"] == 4
    assert stats["accepted_unique_problems"] == 3
    assert data["analytics"]["lifetime"]["rated_contests"] == 2

    async with h.sessions() as session:
        row = await session.get(DeveloperProfile, uuid.UUID(body["id"]))
        assert row is not None
        assert row.platform == "codeforces"
        assert row.external_id == cf.HANDLE.lower()
        assert row.username == cf.HANDLE
        assert row.source_url == f"https://codeforces.com/profile/{cf.HANDLE}"
        assert row.last_sync_attempt_at is not None
        assert row.collected_at is not None


async def test_private_data_never_persisted_or_returned(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    resp = await h.client.get(h.url(cid))
    subs = await h.client.get(h.url(cid, "/submissions"))
    for text_ in (resp.text, subs.text):
        assert cf.PRIVATE_EMAIL not in text_
        assert cf.TEAMMATE not in text_
    async with h.sessions() as session:
        raw = (
            await session.execute(text("SELECT collected_data::text FROM developer_profiles"))
        ).scalar_one()
    for private in (cf.PRIVATE_EMAIL, cf.TEAMMATE, "vk-123", "openid-456"):
        assert private not in raw


async def test_submission_and_rating_listings(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    page = (await h.client.get(h.url(cid, "/submissions"), params={"limit": 2})).json()
    assert page["total"] == 6
    assert [s["id"] for s in page["items"]] == [1, 2]
    accepted = (await h.client.get(h.url(cid, "/submissions"), params={"verdict": "OK"})).json()
    assert accepted["total"] == 4
    rating = (await h.client.get(h.url(cid, "/rating-history"))).json()
    assert [c["contest_id"] for c in rating["items"]] == [1000, 1010]
    assert rating["status"] == "available"


async def test_duplicate_is_case_insensitive(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    resp = await h.add(cid, cf.HANDLE.upper())
    assert resp.status_code == 409
    assert "already linked" in resp.json()["error"]["message"]


async def test_unknown_user_is_unavailable(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid, "nobody_here")
    body = await h.get(cid)
    assert body["sync_status"] == "failed"
    assert body["sync_error_code"] == "invalid_profile"
    assert body["collection_status"] == "unavailable"
    assert body["data"] is None


async def test_rate_limited_then_retry_succeeds(h: Harness) -> None:
    limited = cf.FakeCodeforces()
    limited.routes["user.info"] = lambda r: cf.failed("Call limit exceeded", 503)
    h.use(limited)
    cid = await h.candidate()
    await h.add(cid)
    body = await h.get(cid)
    assert body["sync_error_code"] == "rate_limited"
    assert body["collection_status"] == "failed"
    # retry is accepted only after a failure
    h.use(cf.FakeCodeforces())
    resp = await h.client.post(h.url(cid, "/retry"))
    assert resp.status_code == 202
    body = await h.get(cid)
    assert body["sync_status"] == "completed"
    assert body["sync_error_code"] is None
    assert (await h.client.post(h.url(cid, "/retry"))).status_code == 409


async def test_failed_sync_preserves_previous_snapshot(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    first = await h.get(cid)
    broken = cf.FakeCodeforces()
    broken.routes["user.info"] = lambda r: httpx.Response(503, text="maintenance")
    h.use(broken)
    assert (await h.client.post(h.url(cid, "/sync"))).status_code == 202
    after = await h.get(cid)
    assert after["sync_status"] == "failed"
    assert after["sync_error_code"] == "network_error"
    assert after["collection_status"] == "collected"  # previous evidence still valid
    assert after["collected_at"] == first["collected_at"]
    assert after["data"] == first["data"]
    async with h.sessions() as session:
        raw = (
            await session.execute(text("SELECT collected_data FROM developer_profiles"))
        ).scalar_one()
    assert raw["analytics"]["submissions"]["total_submissions"] == 6


async def test_partial_sync_keeps_unknowns_as_null(h: Harness) -> None:
    partial = cf.FakeCodeforces()
    partial.routes["user.status"] = lambda r: cf.failed("Internal problem", 500)
    h.use(partial)
    cid = await h.candidate()
    await h.add(cid)
    body = await h.get(cid)
    assert body["sync_status"] == "completed"
    assert body["data"]["submissions"]["status"] == "error"
    assert body["data"]["analytics"]["submissions"] is None
    assert body["data"]["analytics"]["lifetime"]["current_rating"] == 1650


async def test_truncated_window_is_flagged(h: Harness) -> None:
    h.use(cf.FakeCodeforces(), submission_limit=3)
    cid = await h.candidate()
    await h.add(cid)
    body = await h.get(cid)
    assert body["data"]["submissions"]["truncated"] is True
    assert body["data"]["analytics"]["submissions"]["truncated"] is True
    assert any("most recent 3 submissions" in w for w in body["sync_warnings"])


async def test_sync_conflict_while_in_progress(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    async with h.sessions() as session:
        await session.execute(
            text("UPDATE developer_profiles SET sync_status='processing', updated_at=now()")
        )
        await session.commit()
    assert (await h.client.post(h.url(cid, "/sync"))).status_code == 409


async def test_same_account_on_two_candidates_is_reported(h: Harness) -> None:
    first, second = await h.candidate("A"), await h.candidate("B")
    await h.add(first)
    await h.add(second)
    body = await h.get(second)
    assert any("another candidate" in w for w in body["sync_warnings"])


async def test_github_and_codeforces_coexist(h: Harness) -> None:
    gh = gf.FakeGitHub().client()
    h.app.state.github_client = gh
    h.app.state.github_sync = GitHubSyncService(
        session_factory=h.sessions,
        collector=GitHubCollector(gh, GitHubLimits()),
        enabled=lambda: True,
    )
    cid = await h.candidate()
    assert (await h.add(cid)).status_code == 202
    gh_resp = await h.client.post(f"/api/v1/candidates/{cid}/github", json={"profile": gf.LOGIN})
    assert gh_resp.status_code == 202
    assert (await h.get(cid))["sync_status"] == "completed"
    github = (await h.client.get(f"/api/v1/candidates/{cid}/github")).json()
    assert github["sync_status"] == "completed"
    assert github["data"]["source_platform"] == "github"
    # removing one platform leaves the other intact
    assert (await h.client.delete(h.url(cid))).status_code == 204
    assert (await h.client.get(f"/api/v1/candidates/{cid}/github")).status_code == 200


async def test_delete_and_candidate_cascade(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    assert (await h.client.delete(h.url(cid))).status_code == 204
    assert (await h.client.get(h.url(cid))).status_code == 404
    assert (await h.add(cid)).status_code == 202  # re-adding after removal works
    async with h.sessions() as session:
        await session.execute(text("DELETE FROM candidates WHERE id = :id"), {"id": uuid.UUID(cid)})
        await session.commit()
        count = (
            await session.execute(text("SELECT count(*) FROM developer_profiles"))
        ).scalar_one()
    assert count == 0


async def test_logs_contain_no_private_data(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cid = await h.candidate()
    await h.add(cid)
    assert cf.PRIVATE_EMAIL not in caplog.text
    assert cf.TEAMMATE not in caplog.text
