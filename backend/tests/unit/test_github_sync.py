"""GitHub sync service outcomes with a fake session (no database)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app.core.enums import CollectionErrorCode, CollectionStatus, Platform, SyncStatus
from app.models import DeveloperProfile
from app.services.github.collector import CollectorLimits, GitHubCollector
from app.services.github.sync import GitHubSyncService
from tests import github_fakes as gf

DB_DOWN = OperationalError("UPDATE developer_profiles", {}, ConnectionResetError("closed"))


class Session:
    def __init__(
        self, profile: DeveloperProfile | None, *, fail_commit_from: int | None = None
    ) -> None:
        self.profile = profile
        self.fail_commit_from = fail_commit_from
        self.commits = 0

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _model: type, _ident: Any) -> DeveloperProfile | None:
        return self.profile

    async def scalar(self, _stmt: Any) -> Any:
        return None

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit_from is not None and self.commits >= self.fail_commit_from:
            raise DB_DOWN


def _profile(**kw: Any) -> DeveloperProfile:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "platform": Platform.GITHUB,
        "username": gf.LOGIN,
        "sync_status": SyncStatus.PENDING,
        "collection_status": CollectionStatus.NOT_COLLECTED,
    }
    values.update(kw)
    return DeveloperProfile(**values)


def _service(
    session: Session, fake: gf.FakeGitHub | None = None, enabled: bool = True
) -> GitHubSyncService:
    return GitHubSyncService(
        session_factory=lambda: session,  # type: ignore[arg-type]
        collector=GitHubCollector((fake or gf.FakeGitHub()).client(), CollectorLimits()),
        enabled=lambda: enabled,
    )


async def test_success() -> None:
    profile = _profile()
    await _service(Session(profile)).sync(profile.id)
    assert profile.sync_status is SyncStatus.COMPLETED
    assert profile.collection_status is CollectionStatus.COLLECTED
    assert profile.external_id == str(gf.USER_ID)
    assert profile.collected_data is not None


async def test_disabled_integration_fails_without_network() -> None:
    fake = gf.FakeGitHub()
    profile = _profile()
    await _service(Session(profile), fake, enabled=False).sync(profile.id)
    assert profile.sync_error_code is CollectionErrorCode.INTEGRATION_DISABLED
    assert profile.collection_status is CollectionStatus.FAILED
    assert fake.requests == []


async def test_organisation_is_unavailable() -> None:
    profile = _profile()
    await _service(Session(profile), gf.FakeGitHub(user=gf.user_payload(type="Organization"))).sync(
        profile.id
    )
    assert profile.sync_error_code is CollectionErrorCode.NOT_A_USER
    assert profile.collection_status is CollectionStatus.UNAVAILABLE


async def test_external_id_change_is_flagged() -> None:
    profile = _profile(external_id="999")
    await _service(Session(profile)).sync(profile.id)
    assert any("account behind this username changed" in w for w in profile.sync_warnings or [])


async def test_unexpected_error_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile()
    service = _service(Session(profile))

    async def boom(_username: str) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service._collector, "collect", boom)
    await service.sync(profile.id)
    assert profile.sync_error_code is CollectionErrorCode.INTERNAL_ERROR
    assert "RuntimeError" in (profile.sync_error or "")


async def test_database_lost_while_recording_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="app.github.sync")
    fake = gf.FakeGitHub()
    fake.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(404, json={})
    profile = _profile()
    await _service(Session(profile, fail_commit_from=2), fake).sync(profile.id)
    assert profile.sync_status is SyncStatus.FAILED
    assert any("could not record" in r.getMessage() for r in caplog.records)


async def test_missing_or_non_github_profile_is_ignored() -> None:
    await _service(Session(None)).sync(uuid.uuid4())
    other = _profile(platform=Platform.LEETCODE)
    await _service(Session(other)).sync(other.id)
    assert other.sync_status is SyncStatus.PENDING
