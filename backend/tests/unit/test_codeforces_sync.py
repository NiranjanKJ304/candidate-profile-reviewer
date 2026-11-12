"""Codeforces sync service outcomes with a fake session (no database)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.enums import CollectionErrorCode, CollectionStatus, Platform, SyncStatus
from app.models import DeveloperProfile
from app.services.codeforces.collector import CodeforcesCollector, CollectorLimits
from app.services.codeforces.sync import CodeforcesSyncService
from tests import codeforces_fakes as cf

DB_DOWN = OperationalError("UPDATE developer_profiles", {}, ConnectionResetError("closed"))
PREVIOUS = {"previous": "snapshot"}
PREVIOUS_AT = datetime(2026, 1, 1, tzinfo=UTC)


class Session:
    def __init__(
        self,
        profile: DeveloperProfile | None,
        *,
        fail_commit_from: int | None = None,
        other_candidate: bool = False,
    ) -> None:
        self.profile = profile
        self.fail_commit_from = fail_commit_from
        self.other_candidate = other_candidate
        self.commits = 0

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _model: type, _ident: Any) -> DeveloperProfile | None:
        return self.profile

    async def scalar(self, _stmt: Any) -> Any:
        return uuid.uuid4() if self.other_candidate else None

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit_from is not None and self.commits >= self.fail_commit_from:
            raise DB_DOWN


def _profile(**kw: Any) -> DeveloperProfile:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "platform": Platform.CODEFORCES,
        "username": cf.HANDLE,
        "sync_status": SyncStatus.PENDING,
        "collection_status": CollectionStatus.NOT_COLLECTED,
    }
    values.update(kw)
    return DeveloperProfile(**values)


def _service(
    session: Session, fake: cf.FakeCodeforces | None = None, enabled: bool = True
) -> CodeforcesSyncService:
    return CodeforcesSyncService(
        session_factory=lambda: session,  # type: ignore[arg-type]
        collector=CodeforcesCollector((fake or cf.FakeCodeforces()).client(), CollectorLimits()),
        enabled=lambda: enabled,
    )


def _collected_profile() -> DeveloperProfile:
    return _profile(
        collected_data=PREVIOUS,
        collected_at=PREVIOUS_AT,
        collection_status=CollectionStatus.COLLECTED,
        sync_status=SyncStatus.COMPLETED,
    )


async def test_success() -> None:
    profile = _profile(username="ALGO_DEV")
    await _service(Session(profile)).sync(profile.id)
    assert profile.sync_status is SyncStatus.COMPLETED
    assert profile.collection_status is CollectionStatus.COLLECTED
    assert profile.username == cf.HANDLE  # canonical capitalisation
    assert profile.external_id == cf.HANDLE.lower()
    assert profile.profile_url == f"https://codeforces.com/profile/{cf.HANDLE}"
    assert profile.collected_data is not None
    assert profile.collected_data["source_platform"] == "codeforces"
    assert profile.last_sync_attempt_at is not None
    assert profile.sync_warnings == []


async def test_handle_change_is_reported() -> None:
    fake = cf.FakeCodeforces()
    fake.routes["user.info"] = lambda r: cf.ok([cf.user_payload(handle="renamed_dev")])
    profile = _profile()
    await _service(Session(profile), fake).sync(profile.id)
    assert profile.username == "renamed_dev"
    assert any("handle changed" in w for w in profile.sync_warnings or [])


async def test_account_linked_to_another_candidate_is_reported() -> None:
    profile = _profile()
    await _service(Session(profile, other_candidate=True)).sync(profile.id)
    assert any("another candidate" in w for w in profile.sync_warnings or [])


async def test_disabled_integration_fails_without_network() -> None:
    fake = cf.FakeCodeforces()
    profile = _profile()
    await _service(Session(profile), fake, enabled=False).sync(profile.id)
    assert profile.sync_error_code is CollectionErrorCode.INTEGRATION_DISABLED
    assert profile.collection_status is CollectionStatus.FAILED
    assert fake.requests == []


async def test_unknown_user_is_unavailable() -> None:
    profile = _profile(username="nobody_here")
    await _service(Session(profile)).sync(profile.id)
    assert profile.sync_status is SyncStatus.FAILED
    assert profile.sync_error_code is CollectionErrorCode.INVALID_PROFILE
    assert profile.collection_status is CollectionStatus.UNAVAILABLE


async def test_failure_without_previous_data() -> None:
    fake = cf.FakeCodeforces()
    fake.routes["user.info"] = lambda r: cf.failed("Call limit exceeded", 503)
    profile = _profile()
    await _service(Session(profile), fake).sync(profile.id)
    assert profile.sync_error_code is CollectionErrorCode.RATE_LIMITED
    assert profile.collection_status is CollectionStatus.FAILED
    assert profile.collected_data is None


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (cf.failed("Call limit exceeded", 503), CollectionErrorCode.RATE_LIMITED),
        (cf.failed("Internal problem", 500), CollectionErrorCode.API_ERROR),
    ],
)
async def test_failed_retry_preserves_previous_snapshot(response: Any, code: Any) -> None:
    fake = cf.FakeCodeforces()
    fake.routes["user.info"] = lambda r: response
    profile = _collected_profile()
    await _service(Session(profile), fake).sync(profile.id)
    assert profile.sync_status is SyncStatus.FAILED
    assert profile.sync_error_code is code
    assert profile.collected_data == PREVIOUS  # last good evidence kept
    assert profile.collected_at == PREVIOUS_AT
    assert profile.collection_status is CollectionStatus.COLLECTED


async def test_unexpected_error_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _collected_profile()
    service = _service(Session(profile))

    async def boom(_handle: str) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service._collector, "collect", boom)
    await service.sync(profile.id)
    assert profile.sync_error_code is CollectionErrorCode.INTERNAL_ERROR
    assert "RuntimeError" in (profile.sync_error or "")
    assert profile.collected_data == PREVIOUS


async def test_database_lost_while_recording_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="app.codeforces.sync")
    profile = _profile(username="nobody_here")
    await _service(Session(profile, fail_commit_from=2)).sync(profile.id)
    assert profile.sync_status is SyncStatus.FAILED
    assert any("could not record" in r.getMessage() for r in caplog.records)


async def test_crash_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="app.codeforces.sync")
    profile = _profile()
    await _service(Session(profile, fail_commit_from=1)).sync(profile.id)
    assert any("crashed" in r.getMessage() for r in caplog.records)


async def test_missing_or_other_platform_profile_is_ignored() -> None:
    await _service(Session(None)).sync(uuid.uuid4())
    other = _profile(platform=Platform.GITHUB)
    await _service(Session(other)).sync(other.id)
    assert other.sync_status is SyncStatus.PENDING
