"""GitHub HTTP layer with fakes: validation, disabled integration, duplicates, DB failures."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api.deps import get_github_client, get_github_sync
from app.core.enums import Platform
from app.db.session import get_session
from app.models import Candidate, DeveloperProfile
from tests import github_fakes as gf
from tests.conftest import AppFactory, client_for

DB_DOWN = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused 10.0.0.7"))
TOKEN = "ghp_SyntheticTestToken000000000000000000"


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

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        obj.id = obj.id or uuid.uuid4()
        obj.created_at = obj.updated_at = datetime.now(UTC)


class FakeSync:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def sync(self, profile_id: uuid.UUID) -> None:
        self.calls.append(profile_id)


def _app(
    make_app: AppFactory,
    session: FakeSession,
    syncer: FakeSync | None = None,
    fake: gf.FakeGitHub | None = None,
    **settings: Any,
) -> FastAPI:
    settings.setdefault("enabled_platforms", "github")
    app = make_app(**settings)

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_github_sync] = lambda: syncer or FakeSync()
    app.dependency_overrides[get_github_client] = lambda: (fake or gf.FakeGitHub()).client()
    return app


def _url(cid: uuid.UUID | str | None = None) -> str:
    return f"/api/v1/candidates/{cid or uuid.uuid4()}/github"


async def test_add_profile_from_url_schedules_sync(make_app: AppFactory) -> None:
    session, syncer = FakeSession(), FakeSync()
    async with client_for(_app(make_app, session, syncer)) as client:
        resp = await client.post(_url(), json={"profile": "https://github.com/Octo-Dev/"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["username"] == "Octo-Dev"
    assert body["profile_url"] == "https://github.com/Octo-Dev"
    assert body["sync_status"] == "pending"
    assert body["collection_status"] == "not_collected"
    assert body["ownership_status"] == "unverified"  # never assumed from a submitted URL
    assert body["data"] is None
    assert syncer.calls == [uuid.UUID(body["id"])]


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("https://gitlab.com/octocat", "unsupported_domain"),
        ("http://169.254.169.254/latest/meta-data", "unsupported_domain"),
        ("https://github.com/octocat/hello-world", "not_a_profile"),
        ("bad_name!", "invalid_username"),
        ("-octocat", "invalid_username"),
        ("file:///etc/passwd", "unsupported_scheme"),
    ],
)
async def test_invalid_inputs_rejected_before_any_network_call(
    make_app: AppFactory, value: str, code: str
) -> None:
    session, syncer, fake = FakeSession(), FakeSync(), gf.FakeGitHub()
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
            await client.post(_url(), json={"profile": "octocat", "extra": 1})
        ).status_code == 422
        assert (await client.post(_url(), json={"profile": ""})).status_code == 422


async def test_disabled_integration(make_app: AppFactory) -> None:
    session, syncer = FakeSession(), FakeSync()
    app = _app(make_app, session, syncer, enabled_platforms="")
    async with client_for(app) as client:
        add = await client.post(_url(), json={"profile": "octocat"})
        sync = await client.post(_url() + "/sync")
        status = (await client.get("/api/v1/integrations/github/status")).json()
    assert add.status_code == 503
    assert add.json()["error"]["code"] == "integration_disabled"
    assert sync.status_code == 503
    assert status["enabled"] is False
    assert status["reachable"] is None
    assert syncer.calls == []


async def test_unknown_candidate(make_app: AppFactory) -> None:
    async with client_for(_app(make_app, FakeSession(candidate=False))) as client:
        resp = await client.post(_url(), json={"profile": "octocat"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("existing_name", "message"), [("OCTOCAT", "already linked"), ("someone", "remove it")]
)
async def test_duplicate_detection(make_app: AppFactory, existing_name: str, message: str) -> None:
    existing = DeveloperProfile(id=uuid.uuid4(), platform=Platform.GITHUB, username=existing_name)
    syncer = FakeSync()
    async with client_for(_app(make_app, FakeSession(existing=existing), syncer)) as client:
        resp = await client.post(_url(), json={"profile": "octocat"})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "duplicate_profile"
    assert message in err["message"]
    assert err["details"] == [{"profile_id": str(existing.id), "username": existing_name}]
    assert syncer.calls == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "", {"profile": "octocat"}),
        ("GET", "", None),
        ("POST", "/sync", None),
        ("GET", "/repositories", None),
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
            ("GET", "/repositories"),
            ("POST", "/sync"),
            ("DELETE", ""),
        ):
            resp = await client.request(method, _url() + suffix)
            assert resp.status_code == 404, (method, suffix)


class TestStatus:
    async def test_enabled_reports_rate_limits_without_token_value(
        self, make_app: AppFactory
    ) -> None:
        app = _app(make_app, FakeSession(), github_token=TOKEN)
        app.dependency_overrides[get_github_client] = lambda: gf.FakeGitHub().client(token=TOKEN)
        async with client_for(app) as client:
            resp = await client.get("/api/v1/integrations/github/status")
        body = resp.json()
        assert body["enabled"] is True
        assert body["reachable"] is True
        assert body["authenticated"] is True
        assert {r["resource"] for r in body["rate_limits"]} == {"core", "search"}
        assert body["limits"]["max_repositories"] == 300
        assert TOKEN not in resp.text

    async def test_unreachable(self, make_app: AppFactory) -> None:
        fake = gf.FakeGitHub()
        fake.routes["/rate_limit"] = lambda r: __import__("httpx").Response(503)
        async with client_for(_app(make_app, FakeSession(), fake=fake)) as client:
            body = (await client.get("/api/v1/integrations/github/status")).json()
        assert body["reachable"] is False
        assert body["error"]

    async def test_capabilities(self, make_app: AppFactory) -> None:
        async with client_for(make_app(github_token=TOKEN)) as client:
            resp = await client.get("/api/v1/system/capabilities")
        assert resp.json()["github"] == {
            "enabled": False,
            "authenticated": True,
            "accepted_inputs": ["username", "https://github.com/{username}"],
        }
        assert TOKEN not in resp.text
