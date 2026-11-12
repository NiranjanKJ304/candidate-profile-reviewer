"""Codeforces HTTP layer with fakes: validation, disabled integration, duplicates,
retry rules, snapshot listings, status and DB failures."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api.deps import get_codeforces_client, get_codeforces_sync
from app.core.enums import CollectionStatus, OwnershipStatus, Platform, SyncStatus
from app.db.session import get_session
from app.models import Candidate, DeveloperProfile
from app.services.codeforces.collector import CodeforcesCollector, CollectorLimits
from tests import codeforces_fakes as cf
from tests.conftest import AppFactory, client_for

DB_DOWN = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused 10.0.0.7"))


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
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        obj.id = obj.id or uuid.uuid4()
        obj.created_at = obj.created_at or datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)


class FakeSync:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def sync(self, profile_id: uuid.UUID) -> None:
        self.calls.append(profile_id)


def _app(
    make_app: AppFactory,
    session: FakeSession,
    syncer: FakeSync | None = None,
    fake: cf.FakeCodeforces | None = None,
    **settings: Any,
) -> FastAPI:
    settings.setdefault("enabled_platforms", "codeforces")
    app = make_app(**settings)

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_codeforces_sync] = lambda: syncer or FakeSync()
    app.dependency_overrides[get_codeforces_client] = lambda: (fake or cf.FakeCodeforces()).client()
    return app


def _url(cid: uuid.UUID | str | None = None) -> str:
    return f"/api/v1/candidates/{cid or uuid.uuid4()}/codeforces"


async def _stored_profile(**kw: Any) -> DeveloperProfile:
    snap = await CodeforcesCollector(cf.FakeCodeforces().client(), CollectorLimits()).collect(
        cf.HANDLE
    )
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "platform": Platform.CODEFORCES,
        "username": cf.HANDLE,
        "profile_url": f"https://codeforces.com/profile/{cf.HANDLE}",
        "ownership_status": OwnershipStatus.UNVERIFIED,
        "collection_status": CollectionStatus.COLLECTED,
        "sync_status": SyncStatus.COMPLETED,
        "collected_data": snap.model_dump(mode="json"),
        "collected_at": snap.collected_at,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC) - timedelta(hours=1),
    }
    values.update(kw)
    return DeveloperProfile(**values)


async def test_add_profile_from_url_schedules_sync(make_app: AppFactory) -> None:
    session, syncer = FakeSession(), FakeSync()
    async with client_for(_app(make_app, session, syncer)) as client:
        resp = await client.post(
            _url(), json={"profile": "https://codeforces.com/profile/Algo_Dev/"}
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["platform"] == "codeforces"
    assert body["handle"] == "Algo_Dev"
    assert body["profile_url"] == "https://codeforces.com/profile/Algo_Dev"
    assert body["sync_status"] == "pending"
    assert body["collection_status"] == "not_collected"
    assert body["ownership_status"] == "unverified"  # never assumed from a submission
    assert body["data"] is None
    assert body["freshness"]["stale"] is True
    assert body["freshness"]["collected_at"] is None
    assert syncer.calls == [uuid.UUID(body["id"])]


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("https://codeforces.ru/profile/tourist", "unsupported_domain"),
        ("http://169.254.169.254/latest/meta-data", "unsupported_domain"),
        ("https://codeforces.com/contest/1/problem/A", "not_a_profile"),
        ("bad!name", "invalid_handle"),
        ("a;b", "invalid_handle"),
        ("file:///etc/passwd", "unsupported_scheme"),
    ],
)
async def test_invalid_inputs_rejected_before_any_network_call(
    make_app: AppFactory, value: str, code: str
) -> None:
    session, syncer, fake = FakeSession(), FakeSync(), cf.FakeCodeforces()
    async with client_for(_app(make_app, session, syncer, fake)) as client:
        resp = await client.post(_url(), json={"profile": value})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == code
    assert session.added == []
    assert syncer.calls == []
    assert fake.requests == []


async def test_schema_validation(make_app: AppFactory) -> None:
    async with client_for(_app(make_app, FakeSession())) as client:
        assert (await client.post(_url(), json={})).status_code == 422
        assert (
            await client.post(_url(), json={"profile": "tourist", "extra": 1})
        ).status_code == 422
        assert (await client.post(_url(), json={"profile": ""})).status_code == 422


async def test_disabled_integration(make_app: AppFactory) -> None:
    session, syncer = FakeSession(existing=await _stored_profile()), FakeSync()
    app = _app(make_app, session, syncer, enabled_platforms="github")
    async with client_for(app) as client:
        add = await client.post(_url(), json={"profile": "tourist"})
        sync = await client.post(_url() + "/sync")
        retry = await client.post(_url() + "/retry")
        read = await client.get(_url())
        status = (await client.get("/api/v1/integrations/codeforces/status")).json()
    assert add.status_code == sync.status_code == retry.status_code == 503
    assert add.json()["error"]["code"] == "integration_disabled"
    assert read.status_code == 200  # stored evidence stays readable
    assert status["enabled"] is False
    assert syncer.calls == []


async def test_unknown_candidate(make_app: AppFactory) -> None:
    async with client_for(_app(make_app, FakeSession(candidate=False))) as client:
        resp = await client.post(_url(), json={"profile": "tourist"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("existing_handle", "message"), [("ALGO_DEV", "already linked"), ("someone", "remove it")]
)
async def test_duplicate_detection(
    make_app: AppFactory, existing_handle: str, message: str
) -> None:
    existing = DeveloperProfile(
        id=uuid.uuid4(), platform=Platform.CODEFORCES, username=existing_handle
    )
    syncer = FakeSync()
    async with client_for(_app(make_app, FakeSession(existing=existing), syncer)) as client:
        resp = await client.post(_url(), json={"profile": cf.HANDLE})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "duplicate_profile"
    assert message in err["message"]
    assert err["details"] == [{"profile_id": str(existing.id), "handle": existing_handle}]
    assert syncer.calls == []


async def test_get_profile_with_analytics_and_freshness(make_app: AppFactory) -> None:
    profile = await _stored_profile()
    async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
        resp = await client.get(_url(profile.candidate_id))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert body["handle"] == cf.HANDLE
    assert body["freshness"]["stale"] is False
    assert body["freshness"]["age_seconds"] >= 0
    analytics = body["data"]["analytics"]
    assert analytics["lifetime"]["scope"] == "lifetime"
    assert analytics["submissions"]["scope"] == "collected_window"
    assert analytics["submissions"]["accepted_unique_problems"] == 3


async def test_old_snapshot_is_stale(make_app: AppFactory) -> None:
    profile = await _stored_profile(collected_at=datetime.now(UTC) - timedelta(days=30))
    async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
        body = (await client.get(_url())).json()
    assert body["freshness"]["stale"] is True


async def test_unreadable_snapshot_is_reported_as_missing(make_app: AppFactory) -> None:
    profile = await _stored_profile(collected_data={"unexpected": True})
    async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
        body = (await client.get(_url())).json()
        subs = (await client.get(_url() + "/submissions")).json()
    assert body["data"] is None
    assert subs["status"] == "not_collected"


class TestSyncAndRetry:
    async def test_sync_schedules(self, make_app: AppFactory) -> None:
        profile, syncer = await _stored_profile(), FakeSync()
        async with client_for(_app(make_app, FakeSession(existing=profile), syncer)) as client:
            resp = await client.post(_url() + "/sync")
        assert resp.status_code == 202
        assert resp.json()["sync_status"] == "pending"
        assert syncer.calls == [profile.id]

    async def test_sync_conflict_while_running(self, make_app: AppFactory) -> None:
        profile = await _stored_profile(
            sync_status=SyncStatus.PROCESSING, updated_at=datetime.now(UTC)
        )
        syncer = FakeSync()
        async with client_for(_app(make_app, FakeSession(existing=profile), syncer)) as client:
            resp = await client.post(_url() + "/sync")
        assert resp.status_code == 409
        assert syncer.calls == []

    async def test_stale_running_sync_can_be_restarted(self, make_app: AppFactory) -> None:
        profile = await _stored_profile(
            sync_status=SyncStatus.PROCESSING, updated_at=datetime.now(UTC) - timedelta(hours=1)
        )
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            assert (await client.post(_url() + "/sync")).status_code == 202

    async def test_retry_requires_failed_sync(self, make_app: AppFactory) -> None:
        profile, syncer = await _stored_profile(), FakeSync()
        async with client_for(_app(make_app, FakeSession(existing=profile), syncer)) as client:
            resp = await client.post(_url() + "/retry")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "not_failed"
        assert syncer.calls == []

    async def test_retry_failed_sync(self, make_app: AppFactory) -> None:
        profile = await _stored_profile(
            sync_status=SyncStatus.FAILED, sync_error_code="rate_limited", sync_error="limit"
        )
        syncer = FakeSync()
        async with client_for(_app(make_app, FakeSession(existing=profile), syncer)) as client:
            resp = await client.post(_url() + "/retry")
        assert resp.status_code == 202
        body = resp.json()
        assert body["sync_status"] == "pending"
        assert body["sync_error_code"] is None
        assert body["data"] is not None  # previous snapshot still served
        assert syncer.calls == [profile.id]


class TestListings:
    async def test_submissions_paging_and_filters(self, make_app: AppFactory) -> None:
        profile = await _stored_profile()
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            page = (await client.get(_url() + "/submissions?limit=2&offset=1")).json()
            ok = (await client.get(_url() + "/submissions?verdict=ok")).json()
            cpp = (await client.get(_url() + "/submissions?language=C%2B%2B17")).json()
            dp = (await client.get(_url() + "/submissions?tag=DP")).json()
            bad = await client.get(_url() + "/submissions?limit=1001")
        assert page["total"] == 6
        assert [s["id"] for s in page["items"]] == [2, 3]
        assert page["status"] == "available"
        assert page["truncated"] is False
        assert ok["total"] == 4
        assert cpp["total"] == 2
        assert dp["total"] == 2
        assert bad.status_code == 422

    async def test_rating_history(self, make_app: AppFactory) -> None:
        profile = await _stored_profile()
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            body = (await client.get(_url() + "/rating-history?limit=1&offset=1")).json()
        assert body["total"] == 2
        assert [c["contest_id"] for c in body["items"]] == [1010]
        assert body["items"][0]["delta"] == 50

    async def test_listings_before_collection(self, make_app: AppFactory) -> None:
        profile = DeveloperProfile(
            id=uuid.uuid4(), platform=Platform.CODEFORCES, username="x", collected_data=None
        )
        async with client_for(_app(make_app, FakeSession(existing=profile))) as client:
            subs = (await client.get(_url() + "/submissions")).json()
            rating = (await client.get(_url() + "/rating-history")).json()
        assert subs == {
            "items": [],
            "total": 0,
            "limit": 50,
            "offset": 0,
            "status": "not_collected",
            "truncated": False,
            "collected_at": None,
        }
        assert rating["status"] == "not_collected"


async def test_delete(make_app: AppFactory) -> None:
    profile = await _stored_profile()
    session = FakeSession(existing=profile)
    async with client_for(_app(make_app, session)) as client:
        resp = await client.delete(_url())
    assert resp.status_code == 204
    assert session.deleted == [profile]


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "", {"profile": "tourist"}),
        ("GET", "", None),
        ("POST", "/sync", None),
        ("POST", "/retry", None),
        ("GET", "/submissions", None),
        ("GET", "/rating-history", None),
        ("DELETE", "", None),
    ],
)
async def test_database_down(make_app: AppFactory, method: str, suffix: str, body: Any) -> None:
    async with client_for(_app(make_app, FakeSession(error=DB_DOWN))) as client:
        resp = await client.request(method, _url() + suffix, json=body)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "database_unavailable"
    assert "10.0.0.7" not in resp.text


async def test_no_profile_yet(make_app: AppFactory) -> None:
    async with client_for(_app(make_app, FakeSession())) as client:
        for method, suffix in (
            ("GET", ""),
            ("GET", "/submissions"),
            ("GET", "/rating-history"),
            ("POST", "/sync"),
            ("POST", "/retry"),
            ("DELETE", ""),
        ):
            resp = await client.request(method, _url() + suffix)
            assert resp.status_code == 404, (method, suffix)


class TestStatus:
    async def test_status_reports_configuration_without_probing(self, make_app: AppFactory) -> None:
        fake = cf.FakeCodeforces()
        async with client_for(_app(make_app, FakeSession(), fake=fake)) as client:
            resp = await client.get("/api/v1/integrations/codeforces/status")
        body = resp.json()
        assert body["enabled"] is True
        assert body["api_base_url"] == "https://codeforces.com/api/"
        assert body["api_methods"] == ["user.info", "user.rating", "user.status"]
        assert body["submission_limit"] == 500
        assert body["submission_hard_max"] == 1000
        assert body["requests_made"] == 0
        assert fake.requests == []  # no probe call

    async def test_status_reports_last_error(self, make_app: AppFactory) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.info"] = lambda r: cf.failed("Call limit exceeded", 503)
        client_obj = fake.client(max_retries=0)
        with pytest.raises(Exception, match="call limit"):
            await client_obj.call("user.info", {"handles": "a"})
        app = _app(make_app, FakeSession())
        app.dependency_overrides[get_codeforces_client] = lambda: client_obj
        async with client_for(app) as client:
            body = (await client.get("/api/v1/integrations/codeforces/status")).json()
        assert body["last_error"] == "Codeforces API call limit exceeded"
        assert body["requests_made"] == 1

    async def test_capabilities(self, make_app: AppFactory) -> None:
        async with client_for(make_app(enabled_platforms="codeforces")) as client:
            resp = await client.get("/api/v1/system/capabilities")
        assert resp.json()["codeforces"] == {
            "enabled": True,
            "accepted_inputs": ["handle", "https://codeforces.com/profile/{handle}"],
            "submission_limit": 500,
        }
        assert resp.json()["github"]["enabled"] is False
