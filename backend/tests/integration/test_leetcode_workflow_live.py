"""LeetCode self-reported workflow against live PostgreSQL (isolated temporary schema).

Real: HTTP API, validation, persistence, candidate association, migrations 0001-0005.
No network: the LeetCode integration never contacts LeetCode (asserted below).
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
from app.services.codeforces.collector import CodeforcesCollector
from app.services.codeforces.collector import CollectorLimits as CodeforcesLimits
from app.services.codeforces.sync import CodeforcesSyncService
from app.services.github.collector import CollectorLimits as GitHubLimits
from app.services.github.collector import GitHubCollector
from app.services.github.sync import GitHubSyncService
from tests import codeforces_fakes as cf
from tests import github_fakes as gf
from tests.conftest import SettingsFactory, client_for

pytestmark = pytest.mark.integration

PRIVATE_NOTE = "private-phone-555-0100"
EVIDENCE: dict[str, Any] = {
    "reported_by": "candidate",
    "as_of": "2026-11-01",
    "total_solved": 300,
    "easy_solved": 150,
    "medium_solved": 120,
    "hard_solved": 30,
    "contest_rating": 1750,
    "contests_attended": 12,
    "contest_history": [{"contest_name": "Weekly Contest 400", "rank": 1234, "rating_after": 1750}],
    "languages": [{"name": "Python3", "problems_solved": 250}],
    "topics": [{"name": "Dynamic Programming", "problems_solved": 60}],
}


class Harness:
    def __init__(self, app: FastAPI, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.app = app
        self.sessions = sessions
        self.client: httpx.AsyncClient

    async def candidate(self, name: str = "Jordan Sample") -> str:
        resp = await self.client.post("/api/v1/candidates", json={"full_name": name})
        assert resp.status_code == 201
        return str(resp.json()["id"])

    def url(self, cid: str, suffix: str = "") -> str:
        return f"/api/v1/candidates/{cid}/leetcode{suffix}"

    async def link(
        self, cid: str, value: str = "https://leetcode.com/u/jane_doe/"
    ) -> httpx.Response:
        return await self.client.post(self.url(cid), json={"profile": value})

    async def row(self, cid: str) -> DeveloperProfile:
        async with self.sessions() as session:
            from sqlalchemy import select

            row = await session.scalar(
                select(DeveloperProfile).where(
                    DeveloperProfile.candidate_id == uuid.UUID(cid),
                    DeveloperProfile.platform == "leetcode",
                )
            )
            assert row is not None
            return row


@pytest.fixture
async def h(
    migrated_engine: AsyncEngine, make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Harness]:
    original = httpx.AsyncClient.send

    async def guarded(self: httpx.AsyncClient, request: httpx.Request, **kw: Any) -> Any:
        assert "leetcode" not in (request.url.host or ""), f"LeetCode contacted: {request.url}"
        return await original(self, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded)
    app = create_app(make_settings(enabled_platforms="github,codeforces,leetcode"))
    sessions = async_sessionmaker(migrated_engine, expire_on_commit=False)
    app.state.db_engine = migrated_engine
    app.state.session_factory = sessions
    harness = Harness(app, sessions)
    async with client_for(app) as client:
        harness.client = client
        yield harness


async def test_link_and_persist_without_platform_data(h: Harness) -> None:
    cid = await h.candidate()
    resp = await h.link(cid)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sync_status"] == "not_applicable"
    assert body["collection_status"] == "unavailable"
    assert body["evidence"] is None
    row = await h.row(cid)
    assert row.username == "jane_doe"
    assert row.profile_url == "https://leetcode.com/u/jane_doe/"
    assert row.ownership_status == "unverified"
    assert row.collected_data is None
    assert row.collected_at is None
    assert row.self_reported_data is None


async def test_submit_replace_and_clear_evidence(h: Harness) -> None:
    cid = await h.candidate()
    await h.link(cid)
    put = await h.client.put(h.url(cid, "/evidence"), json=EVIDENCE)
    assert put.status_code == 200, put.text
    ev = put.json()["evidence"]
    assert ev["verification_status"] == "not_verified"
    assert ev["solved"]["total"] == 300

    row = await h.row(cid)
    assert row.self_reported_data is not None
    assert row.self_reported_data["source_type"] == "self_reported"
    assert row.self_reported_data["solved"]["provenance"]["verification_status"] == "not_verified"
    assert row.self_reported_at is not None
    assert row.collected_data is None  # never stored as platform evidence

    second = await h.client.put(
        h.url(cid, "/evidence"), json={"reported_by": "recruiter", "total_solved": 310}
    )
    ev2 = second.json()["evidence"]
    assert ev2["revision"] == 2
    assert ev2["contest"]["status"] == "not_collected"  # replaced, not merged; unknown, not zero
    assert ev2["contest"]["rating"] is None
    assert ev2["solved"]["provenance"]["reported_by"] == "recruiter"

    assert (await h.client.delete(h.url(cid, "/evidence"))).status_code == 204
    body = (await h.client.get(h.url(cid))).json()
    assert body["evidence"] is None
    assert body["username"] == "jane_doe"  # association kept


async def test_invalid_evidence_is_not_persisted(h: Harness) -> None:
    cid = await h.candidate()
    await h.link(cid)
    resp = await h.client.put(
        h.url(cid, "/evidence"), json={**EVIDENCE, "hard_solved": 99, "notes": PRIVATE_NOTE}
    )
    assert resp.status_code == 422
    assert PRIVATE_NOTE not in resp.text
    assert (await h.row(cid)).self_reported_data is None


async def test_duplicate_and_reassociation(h: Harness) -> None:
    cid = await h.candidate()
    await h.link(cid)
    dup = await h.link(cid, "JANE_DOE")
    assert dup.status_code == 409
    assert (await h.client.delete(h.url(cid))).status_code == 204
    assert (await h.client.get(h.url(cid))).status_code == 404
    assert (await h.link(cid, "https://leetcode.com/other_user/")).status_code == 201


async def test_candidate_cascade_removes_evidence(h: Harness) -> None:
    cid = await h.candidate()
    await h.link(cid)
    await h.client.put(h.url(cid, "/evidence"), json=EVIDENCE)
    async with h.sessions() as session:
        await session.execute(text("DELETE FROM candidates WHERE id = :id"), {"id": uuid.UUID(cid)})
        await session.commit()
        count = (
            await session.execute(text("SELECT count(*) FROM developer_profiles"))
        ).scalar_one()
    assert count == 0


async def test_three_platforms_coexist_and_syncs_ignore_leetcode(h: Harness) -> None:
    gh = gf.FakeGitHub().client()
    h.app.state.github_client = gh
    github_sync = GitHubSyncService(
        session_factory=h.sessions,
        collector=GitHubCollector(gh, GitHubLimits()),
        enabled=lambda: True,
    )
    h.app.state.github_sync = github_sync
    cf_client = cf.FakeCodeforces().client()
    h.app.state.codeforces_client = cf_client
    codeforces_sync = CodeforcesSyncService(
        session_factory=h.sessions,
        collector=CodeforcesCollector(cf_client, CodeforcesLimits()),
        enabled=lambda: True,
    )
    h.app.state.codeforces_sync = codeforces_sync

    cid = await h.candidate()
    assert (await h.link(cid)).status_code == 201
    await h.client.put(h.url(cid, "/evidence"), json=EVIDENCE)
    gh_resp = await h.client.post(f"/api/v1/candidates/{cid}/github", json={"profile": gf.LOGIN})
    cf_resp = await h.client.post(
        f"/api/v1/candidates/{cid}/codeforces", json={"profile": cf.HANDLE}
    )
    assert gh_resp.status_code == cf_resp.status_code == 202
    assert (await h.client.get(f"/api/v1/candidates/{cid}/github")).json()[
        "sync_status"
    ] == "completed"
    assert (await h.client.get(f"/api/v1/candidates/{cid}/codeforces")).json()[
        "sync_status"
    ] == "completed"

    # Running the other platforms' sync services on the LeetCode row changes nothing.
    row = await h.row(cid)
    await github_sync.sync(row.id)
    await codeforces_sync.sync(row.id)
    after = await h.row(cid)
    assert after.sync_status == "not_applicable"
    assert after.collected_data is None
    assert after.self_reported_data == row.self_reported_data

    assert (await h.client.delete(h.url(cid))).status_code == 204
    assert (await h.client.get(f"/api/v1/candidates/{cid}/github")).status_code == 200
    assert (await h.client.get(f"/api/v1/candidates/{cid}/codeforces")).status_code == 200


async def test_logs_contain_no_evidence_content(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cid = await h.candidate()
    await h.link(cid)
    await h.client.put(h.url(cid, "/evidence"), json={**EVIDENCE, "notes": PRIVATE_NOTE})
    assert PRIVATE_NOTE not in caplog.text
    assert "Dynamic Programming" not in caplog.text
