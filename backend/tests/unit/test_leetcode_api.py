"""LeetCode HTTP layer with a fake session: association, self-reported evidence,
labels, freshness, disabled integration, status and the no-outbound-request guarantee."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.core.enums import CollectionStatus, OwnershipStatus, Platform, SyncStatus
from app.db.session import get_session
from app.models import Candidate, DeveloperProfile
from app.schemas.leetcode import LeetCodeEvidenceInput
from app.services.leetcode.evidence import build_evidence
from tests.conftest import AppFactory, client_for

DB_DOWN = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused 10.0.0.7"))
EVIDENCE = {
    "reported_by": "candidate",
    "as_of": "2026-11-01",
    "total_solved": 300,
    "easy_solved": 150,
    "medium_solved": 120,
    "hard_solved": 30,
    "languages": [{"name": "Python3", "problems_solved": 250}],
}


@pytest.fixture(autouse=True)
def outbound_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any real outbound request from the app would fail the test.

    The test client itself goes through ASGITransport, which is allowed.
    """
    attempts: list[str] = []
    original = httpx.AsyncClient.send

    async def guarded(self: httpx.AsyncClient, request: httpx.Request, **kw: Any) -> Any:
        if request.url.host != "testserver":
            attempts.append(str(request.url))
            raise AssertionError(f"unexpected outbound request: {request.url}")
        return await original(self, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded)
    return attempts


class FakeSession:
    def __init__(
        self,
        *,
        candidate: bool = True,
        existing: DeveloperProfile | None = None,
        error: Exception | None = None,
    ) -> None:
        self.candidate = (
            Candidate(id=uuid.uuid4(), full_name="Jordan Sample") if candidate else None
        )
        self.existing = existing
        self.error = error
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0

    async def get(self, _model: type, _ident: Any) -> Any:
        if self.error:
            raise self.error
        return self.candidate

    async def scalar(self, _stmt: Any) -> Any:
        if self.error:
            raise self.error
        return self.existing

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        obj.id = obj.id or uuid.uuid4()
        obj.created_at = obj.created_at or datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)


def _app(make_app: AppFactory, session: FakeSession, **settings: Any) -> FastAPI:
    settings.setdefault("enabled_platforms", "leetcode")
    app = make_app(**settings)

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    return app


def _url(cid: uuid.UUID | str | None = None) -> str:
    return f"/api/v1/candidates/{cid or uuid.uuid4()}/leetcode"


def _profile(evidence: dict[str, Any] | None = None, **kw: Any) -> DeveloperProfile:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "platform": Platform.LEETCODE,
        "username": "jane_doe",
        "profile_url": "https://leetcode.com/u/jane_doe/",
        "ownership_status": OwnershipStatus.UNVERIFIED,
        "collection_status": CollectionStatus.UNAVAILABLE,
        "sync_status": SyncStatus.NOT_APPLICABLE,
        "self_reported_data": evidence,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(kw)
    return DeveloperProfile(**values)


def _stored(**overrides: Any) -> dict[str, Any]:
    payload = LeetCodeEvidenceInput(**{**EVIDENCE, **overrides})
    return build_evidence(
        payload, claimed_profile_url="https://leetcode.com/u/jane_doe/"
    ).model_dump(mode="json")


class TestAssociation:
    async def test_link_from_url(self, make_app: AppFactory, outbound_attempts: list[str]) -> None:
        session = FakeSession()
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post(_url(), json={"profile": "https://leetcode.com/u/Jane_Doe/"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["platform"] == "leetcode"
        assert body["username"] == "Jane_Doe"
        assert body["profile_url"] == "https://leetcode.com/u/Jane_Doe/"
        assert body["ownership_status"] == "unverified"
        assert body["collection_status"] == "unavailable"
        assert body["sync_status"] == "not_applicable"
        assert body["integration_mode"] == "manual_self_reported"
        assert body["platform_data"]["status"] == "unavailable"
        assert "https://leetcode.com/terms/" in body["platform_data"]["sources"]
        assert body["evidence"] is None
        assert body["freshness"]["stale"] is True
        assert any("not checked against LeetCode" in w for w in body["warnings"])
        assert any("Ownership not verified" in w for w in body["warnings"])
        row = session.added[0]
        assert row.collected_data is None  # nothing is ever "collected" from LeetCode
        assert outbound_attempts == []

    @pytest.mark.parametrize(
        ("value", "code"),
        [
            ("https://leetcode.cn/u/jane/", "unsupported_domain"),
            ("http://169.254.169.254/latest", "unsupported_domain"),
            ("https://leetcode.com/problems/two-sum/", "not_a_profile"),
            ("bad!name", "invalid_username"),
            ("file:///etc/passwd", "unsupported_scheme"),
        ],
    )
    async def test_invalid_inputs(self, make_app: AppFactory, value: str, code: str) -> None:
        session = FakeSession()
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post(_url(), json={"profile": value})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == code
        assert session.added == []

    async def test_schema(self, make_app: AppFactory) -> None:
        async with client_for(_app(make_app, FakeSession())) as client:
            assert (await client.post(_url(), json={})).status_code == 422
            assert (
                await client.post(_url(), json={"profile": "x", "cookie": "y"})
            ).status_code == 422

    @pytest.mark.parametrize(
        ("existing", "message"), [("JANE_DOE", "already linked"), ("other", "remove it")]
    )
    async def test_duplicates(self, make_app: AppFactory, existing: str, message: str) -> None:
        session = FakeSession(existing=_profile(username=existing))
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post(_url(), json={"profile": "jane_doe"})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "duplicate_profile"
        assert message in resp.json()["error"]["message"]

    async def test_unknown_candidate(self, make_app: AppFactory) -> None:
        async with client_for(_app(make_app, FakeSession(candidate=False))) as client:
            assert (await client.post(_url(), json={"profile": "jane"})).status_code == 404


class TestEvidence:
    async def test_submit_labels_everything_self_reported(self, make_app: AppFactory) -> None:
        profile = _profile()
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            resp = await client.put(_url() + "/evidence", json=EVIDENCE)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ev = body["evidence"]
        assert ev["source_type"] == "self_reported"
        assert ev["verification_status"] == "not_verified"
        assert ev["revision"] == 1
        assert ev["solved"]["total"] == 300
        assert ev["solved"]["provenance"]["source_type"] == "self_reported"
        assert ev["contest"]["status"] == "not_collected"  # not provided != zero
        assert ev["contest"]["rating"] is None
        assert body["collection_status"] == "unavailable"  # still not platform evidence
        assert body["freshness"]["as_of"] == "2026-11-01"
        assert any("Self-reported and not verified" in w for w in body["warnings"])
        assert profile.self_reported_data is not None
        assert profile.self_reported_at is not None
        assert profile.collected_data is None
        assert profile.collected_at is None

    async def test_replacing_increments_revision(self, make_app: AppFactory) -> None:
        profile = _profile(evidence=_stored())
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            body = (
                await client.put(_url() + "/evidence", json={**EVIDENCE, "total_solved": 300})
            ).json()
        assert body["evidence"]["revision"] == 2

    @pytest.mark.parametrize(
        "payload",
        [
            {"reported_by": "candidate"},
            {"reported_by": "verified", "total_solved": 1},
            {**EVIDENCE, "hard_solved": 99},
            {**EVIDENCE, "session_cookie": "abc"},
            {**EVIDENCE, "total_solved": True},
        ],
    )
    async def test_invalid_evidence(self, make_app: AppFactory, payload: dict[str, Any]) -> None:
        profile = _profile()
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            resp = await client.put(_url() + "/evidence", json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"
        assert "abc" not in resp.text  # submitted values are not echoed
        assert profile.self_reported_data is None

    async def test_stale_evidence(self, make_app: AppFactory) -> None:
        old = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
        profile = _profile(evidence=_stored(as_of=old))
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            body = (await client.get(_url())).json()
        assert body["freshness"]["stale"] is True
        assert body["freshness"]["age_days"] >= 60
        assert body["freshness"]["stale_after_days"] == 30

    async def test_fresh_evidence(self, make_app: AppFactory) -> None:
        recent = datetime.now(UTC).date().isoformat()
        profile = _profile(evidence=_stored(as_of=recent))
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            resp = await client.get(_url())
        assert resp.headers["cache-control"] == "no-store"
        assert resp.json()["freshness"]["stale"] is False

    async def test_unreadable_evidence(self, make_app: AppFactory) -> None:
        profile = _profile(evidence={"verification_status": "verified"})
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            body = (await client.get(_url())).json()
        assert body["evidence"] is None
        assert any("could not be read" in w for w in body["warnings"])

    async def test_clear_evidence_keeps_association(self, make_app: AppFactory) -> None:
        profile = _profile(evidence=_stored())
        session = FakeSession(existing=profile)
        async with client_for(_app(make_app, session)) as client:
            resp = await client.delete(_url() + "/evidence")
        assert resp.status_code == 204
        assert profile.self_reported_data is None
        assert profile.self_reported_at is None
        assert session.deleted == []

    async def test_remove_profile(self, make_app: AppFactory) -> None:
        profile = _profile(evidence=_stored())
        session = FakeSession(existing=profile)
        async with client_for(_app(make_app, session)) as client:
            assert (await client.delete(_url())).status_code == 204
        assert session.deleted == [profile]

    async def test_no_sync_or_retry_endpoints(self, make_app: AppFactory) -> None:
        async with client_for(_app(make_app, FakeSession(existing=_profile()))) as client:
            for suffix in ("/sync", "/retry"):
                assert (await client.post(_url() + suffix)).status_code in (404, 405)


async def test_disabled_integration(make_app: AppFactory) -> None:
    profile = _profile(evidence=_stored())
    app = _app(make_app, FakeSession(existing=profile), enabled_platforms="github,codeforces")
    async with client_for(app) as client:
        add = await client.post(_url(), json={"profile": "jane"})
        put = await client.put(_url() + "/evidence", json=EVIDENCE)
        read = await client.get(_url())
        status = (await client.get("/api/v1/integrations/leetcode/status")).json()
    assert add.status_code == put.status_code == 503
    assert add.json()["error"]["code"] == "integration_disabled"
    assert read.status_code == 200  # stored evidence stays readable
    assert status["enabled"] is False


async def test_not_found_paths(make_app: AppFactory) -> None:
    async with client_for(_app(make_app, FakeSession())) as client:
        for method, suffix, body in (
            ("GET", "", None),
            ("PUT", "/evidence", EVIDENCE),
            ("DELETE", "/evidence", None),
            ("DELETE", "", None),
        ):
            assert (await client.request(method, _url() + suffix, json=body)).status_code == 404


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "", {"profile": "jane"}),
        ("GET", "", None),
        ("PUT", "/evidence", EVIDENCE),
        ("DELETE", "/evidence", None),
        ("DELETE", "", None),
    ],
)
async def test_database_down(make_app: AppFactory, method: str, suffix: str, body: Any) -> None:
    async with client_for(_app(make_app, FakeSession(error=DB_DOWN))) as client:
        resp = await client.request(method, _url() + suffix, json=body)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "database_unavailable"
    assert "10.0.0.7" not in resp.text


class TestStatusAndCapabilities:
    async def test_status_explains_the_mode(self, make_app: AppFactory) -> None:
        async with client_for(_app(make_app, FakeSession())) as client:
            body = (await client.get("/api/v1/integrations/leetcode/status")).json()
        assert body["enabled"] is True
        assert body["integration_mode"] == "manual_self_reported"
        assert body["platform_api"] == "unavailable"
        assert body["sync_supported"] is False
        assert body["contacts_leetcode"] is False
        assert body["sources"] == ["https://leetcode.com/terms/", "https://leetcode.com/robots.txt"]
        assert len(body["findings"]) >= 4

    async def test_capabilities(self, make_app: AppFactory) -> None:
        async with client_for(make_app(enabled_platforms="leetcode")) as client:
            caps = (await client.get("/api/v1/system/capabilities")).json()
        assert caps["leetcode"] == {
            "enabled": True,
            "integration_mode": "manual_self_reported",
            "sync_supported": False,
            "accepted_inputs": ["username", "https://leetcode.com/u/{username}/"],
        }
        assert caps["codeforces"]["enabled"] is False
