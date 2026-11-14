"""Resume/candidate HTTP layer with a fake session: validation responses, DB failures."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api.deps import get_resume_processor, get_resume_storage
from app.db.session import get_session
from app.models import Candidate
from app.services.resume.storage import ResumeStorage
from tests import resume_fixtures as fx
from tests.conftest import AppFactory, client_for

DB_DOWN = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused 10.0.0.5"))


class FakeSession:
    def __init__(
        self,
        *,
        candidate_exists: bool = True,
        get_error: Exception | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.candidate = (
            Candidate(id=uuid.uuid4(), full_name="Jordan Sample") if candidate_exists else None
        )
        self.get_error = get_error
        self.commit_error = commit_error
        self.added: list[Any] = []
        self.rolled_back = False

    async def get(self, model: type, ident: Any) -> Any:
        if self.get_error:
            raise self.get_error
        return self.candidate if model is Candidate else None

    async def scalar(self, _stmt: Any) -> Any:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self.commit_error:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, obj: Any) -> None:
        now = datetime.now(UTC)
        obj.created_at = obj.updated_at = now
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class FakeProcessor:
    def __init__(self) -> None:
        self.scheduled: list[uuid.UUID] = []

    async def process(self, resume_id: uuid.UUID) -> None:
        self.scheduled.append(resume_id)


@pytest.fixture
def storage(tmp_path: Path) -> ResumeStorage:
    s = ResumeStorage(tmp_path / "resumes")
    s.ensure_ready()
    return s


def _app(
    make_app: AppFactory,
    session: FakeSession,
    storage: ResumeStorage,
    processor: FakeProcessor,
    **settings: Any,
) -> FastAPI:
    app = make_app(**settings)

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_resume_storage] = lambda: storage
    app.dependency_overrides[get_resume_processor] = lambda: processor
    return app


def _upload(
    data: bytes, name: str, content_type: str = "application/octet-stream"
) -> dict[str, Any]:
    return {"files": {"file": (name, data, content_type)}}


URL = "/api/v1/candidates/{cid}/resumes"


async def test_successful_upload_schedules_processing(
    make_app: AppFactory, storage: ResumeStorage
) -> None:
    session, processor = FakeSession(), FakeProcessor()
    async with client_for(_app(make_app, session, storage, processor)) as client:
        resp = await client.post(URL.format(cid=uuid.uuid4()), **_upload(fx.text_pdf(), "cv.pdf"))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["parse_status"] == "pending"
    assert body["content_type"] == "application/pdf"
    assert body["original_filename"] == "cv.pdf"
    assert processor.scheduled == [uuid.UUID(body["id"])]
    stored = list(storage.root.iterdir())
    assert [p.name for p in stored] == [f"{uuid.UUID(body['id']).hex}.pdf"]


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (b"", "cv.pdf", 400, "empty_file"),
        (b"plain text resume", "cv.txt", 415, "unsupported_type"),
        (fx.make_docx(), "cv.pdf", 415, "content_mismatch"),
        (b"\x00garbage" * 50, "cv.pdf", 422, "corrupted_file"),
        (fx.encrypted_pdf(), "cv.pdf", 422, "encrypted_file"),
        (fx.macro_docm_as_docx(), "cv.docx", 422, "unsafe_file"),
    ],
)
async def test_invalid_uploads_are_rejected_and_nothing_is_stored(
    make_app: AppFactory, storage: ResumeStorage, data: bytes, name: str, status: int, code: str
) -> None:
    session, processor = FakeSession(), FakeProcessor()
    async with client_for(_app(make_app, session, storage, processor)) as client:
        resp = await client.post(URL.format(cid=uuid.uuid4()), **_upload(data, name))
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code
    assert processor.scheduled == []
    assert session.added == []
    assert list(storage.root.iterdir()) == []


async def test_oversized_upload_rejected_from_content_length(
    make_app: AppFactory, storage: ResumeStorage
) -> None:
    session, processor = FakeSession(), FakeProcessor()
    app = _app(make_app, session, storage, processor, resume_max_bytes=2048)
    async with client_for(app) as client:
        resp = await client.post(
            URL.format(cid=uuid.uuid4()), **_upload(b"%PDF-" + b"x" * 200_000, "cv.pdf")
        )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


async def test_missing_file_field(make_app: AppFactory, storage: ResumeStorage) -> None:
    async with client_for(_app(make_app, FakeSession(), storage, FakeProcessor())) as client:
        resp = await client.post(URL.format(cid=uuid.uuid4()), data={"other": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_unknown_candidate(make_app: AppFactory, storage: ResumeStorage) -> None:
    app = _app(make_app, FakeSession(candidate_exists=False), storage, FakeProcessor())
    async with client_for(app) as client:
        resp = await client.post(URL.format(cid=uuid.uuid4()), **_upload(fx.text_pdf(), "cv.pdf"))
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "Candidate not found"


async def test_invalid_candidate_id(make_app: AppFactory, storage: ResumeStorage) -> None:
    async with client_for(_app(make_app, FakeSession(), storage, FakeProcessor())) as client:
        resp = await client.post(URL.format(cid="not-a-uuid"), **_upload(fx.text_pdf(), "cv.pdf"))
    assert resp.status_code == 422


class TestDatabaseFailures:
    async def test_database_down_on_lookup_returns_503(
        self, make_app: AppFactory, storage: ResumeStorage
    ) -> None:
        app = _app(make_app, FakeSession(get_error=DB_DOWN), storage, FakeProcessor())
        async with client_for(app) as client:
            resp = await client.post(
                URL.format(cid=uuid.uuid4()), **_upload(fx.text_pdf(), "cv.pdf")
            )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "database_unavailable"
        assert "10.0.0.5" not in resp.text  # driver details never reach the client

    async def test_database_failure_on_commit_leaves_no_orphan_file(
        self, make_app: AppFactory, storage: ResumeStorage
    ) -> None:
        processor = FakeProcessor()
        app = _app(make_app, FakeSession(commit_error=DB_DOWN), storage, processor)
        async with client_for(app) as client:
            resp = await client.post(
                URL.format(cid=uuid.uuid4()), **_upload(fx.text_pdf(), "cv.pdf")
            )
        assert resp.status_code == 503
        assert list(storage.root.iterdir()) == []
        assert processor.scheduled == []

    async def test_candidate_create_with_database_down(
        self, make_app: AppFactory, storage: ResumeStorage
    ) -> None:
        app = _app(make_app, FakeSession(commit_error=DB_DOWN), storage, FakeProcessor())
        async with client_for(app) as client:
            resp = await client.post("/api/v1/candidates", json={"full_name": "Jordan Sample"})
        assert resp.status_code == 503

    async def test_resume_get_with_database_down(
        self, make_app: AppFactory, storage: ResumeStorage
    ) -> None:
        app = _app(make_app, FakeSession(get_error=DB_DOWN), storage, FakeProcessor())
        async with client_for(app) as client:
            resp = await client.get(f"/api/v1/resumes/{uuid.uuid4()}")
        assert resp.status_code == 503


class TestCapabilities:
    async def test_reports_llm_not_configured_without_leaking(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            body = (await client.get("/api/v1/system/capabilities")).json()
        assert body["llm"] == {
            "configured": False,
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
        }
        assert body["resume"]["accepted_extensions"] == [".pdf", ".docx"]
        assert body["resume"]["max_bytes"] == 10 * 1024 * 1024

    async def test_reports_configured_without_exposing_key(self, make_app: AppFactory) -> None:
        async with client_for(make_app(groq_api_key="gsk_secret_value_1234567890")) as client:
            resp = await client.get("/api/v1/system/capabilities")
        assert resp.json()["llm"]["configured"] is True
        assert "gsk_secret" not in resp.text

    async def test_ocr_disabled(self, make_app: AppFactory) -> None:
        async with client_for(make_app(ocr_enabled=False)) as client:
            body = (await client.get("/api/v1/system/capabilities")).json()
        assert body["ocr"]["enabled"] is False
        assert body["ocr"]["available"] is False
