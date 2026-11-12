"""GitHub profile workflow against live PostgreSQL (isolated temporary schema).

Real: HTTP API, validation, background sync, normalisation, persistence.
Deterministic: GitHub responses come from tests/github_fakes.py (no network).
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
from app.services.github.collector import CollectorLimits, GitHubCollector
from app.services.github.sync import GitHubSyncService
from tests import github_fakes as gf
from tests.conftest import SettingsFactory, client_for

pytestmark = pytest.mark.integration


class Harness:
    def __init__(self, app: FastAPI, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.app = app
        self.sessions = sessions
        self.client: httpx.AsyncClient
        self.fake = gf.FakeGitHub()
        self.enabled = True

    def use(self, fake: gf.FakeGitHub) -> None:
        self.fake = fake
        gh = fake.client()
        self.app.state.github_client = gh
        self.app.state.github_sync = GitHubSyncService(
            session_factory=self.sessions,
            collector=GitHubCollector(gh, CollectorLimits()),
            enabled=lambda: self.enabled,
        )

    async def candidate(self, name: str = "Jordan Sample") -> str:
        resp = await self.client.post("/api/v1/candidates", json={"full_name": name})
        assert resp.status_code == 201
        return str(resp.json()["id"])

    async def add(self, cid: str, value: str = gf.LOGIN) -> httpx.Response:
        return await self.client.post(f"/api/v1/candidates/{cid}/github", json={"profile": value})

    async def get(self, cid: str) -> dict[str, Any]:
        resp = await self.client.get(f"/api/v1/candidates/{cid}/github")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body


@pytest.fixture
async def h(migrated_engine: AsyncEngine, make_settings: SettingsFactory) -> AsyncIterator[Harness]:
    app = create_app(make_settings(enabled_platforms="github"))
    sessions = async_sessionmaker(migrated_engine, expire_on_commit=False)
    app.state.db_engine = migrated_engine
    app.state.session_factory = sessions
    harness = Harness(app, sessions)
    harness.use(gf.FakeGitHub())
    async with client_for(app) as client:
        harness.client = client
        yield harness


async def test_add_sync_and_retrieve(h: Harness) -> None:
    cid = await h.candidate()
    resp = await h.add(cid, f"https://github.com/{gf.LOGIN}")
    assert resp.status_code == 202, resp.text

    body = await h.get(cid)  # background sync completed during the request
    assert body["sync_status"] == "completed", body
    assert body["collection_status"] == "collected"
    assert body["ownership_status"] == "unverified"
    assert body["username"] == gf.LOGIN
    assert body["collected_at"] is not None
    data = body["data"]
    assert data["profile"]["data"]["public_repos"] == 3
    assert data["profile"]["provenance"]["source_platform"] == "github"
    assert [r["name"] for r in data["repositories"]["items"]][:2] == ["data-api", "ml-notebooks"]
    assert data["activity"]["commits_reported"] == 2
    assert data["pull_requests"]["total_count"] == 1
    assert data["contribution_calendar"]["status"] == "unavailable"

    async with h.sessions() as session:
        row = await session.get(DeveloperProfile, uuid.UUID(body["id"]))
        assert row is not None
        assert row.external_id == str(gf.USER_ID)
        assert row.profile_url == f"https://github.com/{gf.LOGIN}"
        assert row.last_sync_attempt_at is not None


async def test_private_data_and_token_never_persisted_or_returned(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    resp = await h.client.get(f"/api/v1/candidates/{cid}/github")
    assert gf.PRIVATE_EMAIL not in resp.text
    assert gf.COMMIT_MESSAGE not in resp.text
    async with h.sessions() as session:
        raw = (
            await session.execute(text("SELECT collected_data::text FROM developer_profiles"))
        ).scalar_one()
    assert gf.PRIVATE_EMAIL not in raw
    assert gf.COMMIT_MESSAGE not in raw


async def test_repositories_listing_filters_and_paging(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    base = f"/api/v1/candidates/{cid}/github/repositories"
    all_repos = (await h.client.get(base)).json()
    assert all_repos["total"] == 4
    assert all_repos["status"] == "available"
    no_forks = (await h.client.get(base, params={"include_forks": "false"})).json()
    assert "forked-lib" not in [r["name"] for r in no_forks["items"]]
    active = (
        await h.client.get(base, params={"include_forks": "false", "include_archived": "false"})
    ).json()
    assert [r["name"] for r in active["items"]] == ["data-api", "ml-notebooks"]
    python = (await h.client.get(base, params={"language": "python"})).json()
    assert [r["name"] for r in python["items"]] == ["data-api"]
    page = (await h.client.get(base, params={"limit": 1, "offset": 1})).json()
    assert len(page["items"]) == 1
    assert page["total"] == 4


async def test_repositories_before_any_collection(h: Harness) -> None:
    h.enabled = False  # sync will fail: nothing collected
    cid = await h.candidate()
    await h.add(cid)
    page = (await h.client.get(f"/api/v1/candidates/{cid}/github/repositories")).json()
    assert page == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "status": "not_collected",
        "collected_at": None,
    }


async def test_duplicate_and_case_insensitive(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    resp = await h.add(cid, gf.LOGIN.upper())
    assert resp.status_code == 409
    assert "already linked" in resp.json()["error"]["message"]


async def test_unknown_user_is_unavailable_not_failed(h: Harness) -> None:
    fake = gf.FakeGitHub()
    fake.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(404, json={})
    h.use(fake)
    cid = await h.candidate()
    await h.add(cid)
    body = await h.get(cid)
    assert body["sync_status"] == "failed"
    assert body["sync_error_code"] == "invalid_profile"
    assert body["collection_status"] == "unavailable"
    assert body["data"] is None


async def test_rate_limited_sync_and_retry(h: Harness) -> None:
    limited = gf.FakeGitHub()
    limited.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(
        403, json={}, headers=gf.rate_headers(0)
    )
    h.use(limited)
    cid = await h.candidate()
    await h.add(cid)
    body = await h.get(cid)
    assert body["sync_error_code"] == "rate_limited"
    assert body["collection_status"] == "failed"

    h.use(gf.FakeGitHub())
    resp = await h.client.post(f"/api/v1/candidates/{cid}/github/sync")
    assert resp.status_code == 202
    body = await h.get(cid)
    assert body["sync_status"] == "completed"
    assert body["sync_error_code"] is None
    assert body["collection_status"] == "collected"


async def test_failed_retry_keeps_previous_snapshot(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    first = await h.get(cid)
    broken = gf.FakeGitHub()
    broken.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(500)
    h.use(broken)
    await h.client.post(f"/api/v1/candidates/{cid}/github/sync")
    after = await h.get(cid)
    assert after["sync_status"] == "failed"
    assert after["sync_error_code"] == "api_error"
    assert after["collection_status"] == "collected"  # previous evidence still valid
    assert after["collected_at"] == first["collected_at"]
    assert after["data"]["profile"]["data"]["login"] == gf.LOGIN


async def test_partial_sync_records_warnings(h: Harness) -> None:
    partial = gf.FakeGitHub()
    partial.routes[f"/users/{gf.LOGIN}/events/public"] = lambda r: httpx.Response(
        403, json={}, headers=gf.rate_headers(0)
    )
    h.use(partial)
    cid = await h.candidate()
    await h.add(cid)
    body = await h.get(cid)
    assert body["sync_status"] == "completed"
    assert body["data"]["activity"]["status"] == "rate_limited"
    assert any("activity" in w.lower() for w in body["sync_warnings"])


async def test_sync_conflict_while_in_progress(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    async with h.sessions() as session:
        await session.execute(
            text("UPDATE developer_profiles SET sync_status='processing', updated_at=now()")
        )
        await session.commit()
    assert (await h.client.post(f"/api/v1/candidates/{cid}/github/sync")).status_code == 409


async def test_username_rename_detected_via_stable_id(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid, gf.LOGIN.upper())  # submitted with different case
    body = await h.get(cid)
    assert body["username"] == gf.LOGIN  # canonical login from the API


async def test_same_account_on_two_candidates_is_reported(h: Harness) -> None:
    first, second = await h.candidate("A"), await h.candidate("B")
    await h.add(first)
    await h.add(second)
    body = await h.get(second)
    assert any("another candidate" in w for w in body["sync_warnings"])


async def test_delete_and_candidate_cascade(h: Harness) -> None:
    cid = await h.candidate()
    await h.add(cid)
    assert (await h.client.delete(f"/api/v1/candidates/{cid}/github")).status_code == 204
    assert (await h.client.get(f"/api/v1/candidates/{cid}/github")).status_code == 404
    # re-adding after removal works
    assert (await h.add(cid)).status_code == 202
    async with h.sessions() as session:
        await session.execute(text("DELETE FROM candidates WHERE id = :id"), {"id": uuid.UUID(cid)})
        await session.commit()
        count = (
            await session.execute(text("SELECT count(*) FROM developer_profiles"))
        ).scalar_one()
    assert count == 0


async def test_candidates_list(h: Harness) -> None:
    ids = [await h.candidate(f"C{i}") for i in range(3)]
    page = (await h.client.get("/api/v1/candidates", params={"limit": 2})).json()
    assert page["total"] == 3
    assert [c["id"] for c in page["items"]] == ids[::-1][:2]


async def test_logs_contain_no_private_data(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cid = await h.candidate()
    await h.add(cid)
    assert gf.PRIVATE_EMAIL not in caplog.text
    assert gf.COMMIT_MESSAGE not in caplog.text
