"""JD HTTP layer with a fake session: validation responses, DB failures, capabilities."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api.deps import get_jd_processor, get_jd_storage
from app.db.session import get_session
from app.services.resume.storage import ResumeStorage
from tests import jd_fixtures as jf
from tests import resume_fixtures as rf
from tests.conftest import AppFactory, client_for

DB_DOWN = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused 10.0.0.9"))
URL = "/api/v1/job-descriptions/upload"


class FakeSession:
    def __init__(
        self, *, error: Exception | None = None, commit_error: Exception | None = None
    ) -> None:
        self.error = error
        self.commit_error = commit_error
        self.added: list[Any] = []

    async def get(self, _model: type, _ident: Any) -> Any:
        if self.error:
            raise self.error
        return None

    async def scalar(self, _stmt: Any) -> Any:
        if self.error:
            raise self.error
        return None

    async def execute(self, _stmt: Any) -> Any:
        if self.error:
            raise self.error
        raise AssertionError("not expected")

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self.commit_error:
            raise self.commit_error

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        obj.created_at = obj.updated_at = datetime.now(UTC)


class FakeProcessor:
    def __init__(self) -> None:
        self.scheduled: list[uuid.UUID] = []

    async def process(self, jd_id: uuid.UUID) -> None:
        self.scheduled.append(jd_id)


@pytest.fixture
def storage(tmp_path: Path) -> ResumeStorage:
    s = ResumeStorage(tmp_path / "jds", extensions=("pdf", "docx", "txt"))
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
    app.dependency_overrides[get_jd_storage] = lambda: storage
    app.dependency_overrides[get_jd_processor] = lambda: processor
    return app


def _file(data: bytes, name: str) -> dict[str, Any]:
    return {"files": {"file": (name, data, "application/octet-stream")}}


@pytest.mark.parametrize(
    ("data", "name", "ext"),
    [
        (jf.jd_txt(), "jd.txt", "txt"),
        (jf.jd_pdf(), "jd.pdf", "pdf"),
        (jf.jd_docx(), "jd.docx", "docx"),
    ],
)
async def test_successful_upload(
    make_app: AppFactory, storage: ResumeStorage, data: bytes, name: str, ext: str
) -> None:
    processor = FakeProcessor()
    async with client_for(_app(make_app, FakeSession(), storage, processor)) as client:
        resp = await client.post(URL, **_file(data, name))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["parse_status"] == "pending"
    assert body["title"] is None  # unknown until parsed - never a placeholder
    assert body["has_review"] is False
    assert processor.scheduled == [uuid.UUID(body["id"])]
    assert [p.name for p in storage.root.iterdir()] == [f"{uuid.UUID(body['id']).hex}.{ext}"]


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (b"", "jd.txt", 400, "empty_file"),
        (b"  \n ", "jd.txt", 400, "empty_file"),
        (b"{\\rtf1}", "jd.rtf", 415, "unsupported_type"),
        (b"MZ\x90\x00binary", "jd.txt", 415, "content_mismatch"),
        (rf.text_pdf(), "jd.docx", 415, "content_mismatch"),
        (b"\x00garbage" * 40, "jd.pdf", 422, "corrupted_file"),
        (rf.encrypted_pdf(), "jd.pdf", 422, "encrypted_file"),
        (rf.macro_docm_as_docx(), "jd.docx", 422, "unsafe_file"),
    ],
)
async def test_rejected_uploads_store_nothing(
    make_app: AppFactory, storage: ResumeStorage, data: bytes, name: str, status: int, code: str
) -> None:
    session, processor = FakeSession(), FakeProcessor()
    async with client_for(_app(make_app, session, storage, processor)) as client:
        resp = await client.post(URL, **_file(data, name))
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code
    assert session.added == []
    assert processor.scheduled == []
    assert list(storage.root.iterdir()) == []


async def test_too_many_pages(make_app: AppFactory, storage: ResumeStorage) -> None:
    app = _app(make_app, FakeSession(), storage, FakeProcessor(), job_description_max_pages=2)
    async with client_for(app) as client:
        resp = await client.post(URL, **_file(rf.text_pdf(pages=3), "jd.pdf"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "too_many_pages"


async def test_oversized_by_content_length(make_app: AppFactory, storage: ResumeStorage) -> None:
    app = _app(make_app, FakeSession(), storage, FakeProcessor(), job_description_max_bytes=2048)
    async with client_for(app) as client:
        resp = await client.post(URL, **_file(b"x" * 200_000, "jd.txt"))
    assert resp.status_code == 413


async def test_list_rejects_bad_paging(make_app: AppFactory, storage: ResumeStorage) -> None:
    async with client_for(_app(make_app, FakeSession(), storage, FakeProcessor())) as client:
        assert (await client.get("/api/v1/job-descriptions?limit=0")).status_code == 422
        assert (await client.get("/api/v1/job-descriptions?limit=201")).status_code == 422
        assert (await client.get("/api/v1/job-descriptions?offset=-1")).status_code == 422


class TestDatabaseFailures:
    async def test_duplicate_check_with_database_down(
        self, make_app: AppFactory, storage: ResumeStorage
    ) -> None:
        app = _app(make_app, FakeSession(error=DB_DOWN), storage, FakeProcessor())
        async with client_for(app) as client:
            resp = await client.post(URL, **_file(jf.jd_txt(), "jd.txt"))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "database_unavailable"
        assert "10.0.0.9" not in resp.text

    async def test_commit_failure_leaves_no_orphan(
        self, make_app: AppFactory, storage: ResumeStorage
    ) -> None:
        processor = FakeProcessor()
        app = _app(make_app, FakeSession(commit_error=DB_DOWN), storage, processor)
        async with client_for(app) as client:
            resp = await client.post(URL, **_file(jf.jd_txt(), "jd.txt"))
        assert resp.status_code == 503
        assert list(storage.root.iterdir()) == []
        assert processor.scheduled == []

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/v1/job-descriptions"),
            ("GET", f"/api/v1/job-descriptions/{uuid.uuid4()}"),
            ("GET", f"/api/v1/job-descriptions/{uuid.uuid4()}/parsed"),
            ("DELETE", f"/api/v1/job-descriptions/{uuid.uuid4()}"),
        ],
    )
    async def test_reads_with_database_down(
        self, make_app: AppFactory, storage: ResumeStorage, method: str, path: str
    ) -> None:
        app = _app(make_app, FakeSession(error=DB_DOWN), storage, FakeProcessor())
        async with client_for(app) as client:
            resp = await client.request(method, path)
        assert resp.status_code == 503


async def test_capabilities_include_job_descriptions(make_app: AppFactory) -> None:
    async with client_for(make_app()) as client:
        body = (await client.get("/api/v1/system/capabilities")).json()
    assert body["job_description"] == {
        "max_bytes": 5 * 1024 * 1024,
        "max_pages": 20,
        "accepted_extensions": [".pdf", ".docx", ".txt"],
    }
