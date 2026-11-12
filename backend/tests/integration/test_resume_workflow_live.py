"""Upload-to-database workflow against live PostgreSQL (isolated temporary schema).

Real: HTTP API, validation, file storage, PyMuPDF/python-docx extraction, Tesseract
OCR, background processing, SQLAlchemy persistence.
Test double: the LLM client only (scripted replies), so results are deterministic
and no API key is required.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.integrations.llm import LLMTimeoutError
from app.main import create_app
from app.models import Resume
from app.services.resume.extraction import OcrConfig, ocr_available
from app.services.resume.processor import ResumeProcessor
from app.services.resume.storage import ResumeStorage
from app.services.resume.structuring import ResumeStructurer
from tests import resume_fixtures as fx
from tests.conftest import SettingsFactory, client_for
from tests.llm_fakes import FakeLLM

pytestmark = pytest.mark.integration

OCR = OcrConfig(enabled=True, tessdata_dir=None, language="eng", dpi=300)


class Harness:
    def __init__(
        self,
        app: FastAPI,
        client: httpx.AsyncClient,
        storage: ResumeStorage,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self.app = app
        self.client = client
        self.storage = storage
        self.sessions = sessions

    def use_llm(self, llm: FakeLLM, ocr: OcrConfig = OCR) -> None:
        self.app.state.resume_processor = ResumeProcessor(
            session_factory=self.sessions,
            storage=self.storage,
            structurer=ResumeStructurer(llm, max_attempts=2, max_input_chars=60_000),
            ocr=ocr,
        )

    async def candidate(self, name: str = "Jordan Sample") -> str:
        resp = await self.client.post("/api/v1/candidates", json={"full_name": name})
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def upload(self, cid: str, data: bytes, name: str) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/candidates/{cid}/resumes",
            files={"file": (name, data, "application/octet-stream")},
        )

    async def detail(self, rid: str) -> dict[str, Any]:
        resp = await self.client.get(f"/api/v1/resumes/{rid}")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    async def row(self, rid: str) -> Resume | None:
        async with self.sessions() as session:
            return await session.get(Resume, uuid.UUID(rid))


@pytest.fixture
async def h(
    migrated_engine: AsyncEngine, make_settings: SettingsFactory, tmp_path: Path
) -> AsyncIterator[Harness]:
    app = create_app(make_settings())
    sessions = async_sessionmaker(migrated_engine, expire_on_commit=False)
    storage = ResumeStorage(tmp_path / "resumes")
    storage.ensure_ready()
    app.state.db_engine = migrated_engine
    app.state.session_factory = sessions
    app.state.resume_storage = storage
    harness = Harness(app, None, storage, sessions)  # type: ignore[arg-type]
    harness.use_llm(FakeLLM(fx.llm_resume_json()))
    async with client_for(app) as client:
        harness.client = client
        yield harness


# --------------------------------------------------------------------------- happy paths
async def test_pdf_upload_to_database(h: Harness) -> None:
    cid = await h.candidate()
    resp = await h.upload(cid, fx.text_pdf(), "Jordan Sample CV.pdf")
    assert resp.status_code == 202, resp.text
    assert resp.headers["x-request-id"]
    rid = resp.json()["id"]

    body = await h.detail(rid)  # background task has completed by now
    assert body["parse_status"] == "completed", body
    assert body["extraction_method"] == "pdf_text"
    assert body["page_count"] == 1
    assert body["parser_model"] == "fake-model-served"
    assert body["parse_error_code"] is None
    assert body["text_char_count"] > 100
    parsed = body["parsed_data"]
    assert parsed["personal_details"]["email"] == "jordan.sample@example.org"
    assert [s["name"] for s in parsed["technical_skills"]] == [
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Docker",
    ]
    assert parsed["internships"][0]["company"] == "Sample Labs"

    row = await h.row(rid)
    assert row is not None
    assert row.storage_path == f"{uuid.UUID(rid).hex}.pdf"
    assert h.storage.exists(row.storage_path)
    assert row.extracted_text
    assert "Example Corp" in row.extracted_text
    assert row.parsed_data == parsed
    assert row.parsed_at is not None


async def test_docx_upload_to_database(h: Harness) -> None:
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.make_docx(header="Jordan Sample"), "resume.docx")).json()["id"]
    body = await h.detail(rid)
    assert body["parse_status"] == "completed"
    assert body["extraction_method"] == "docx"
    assert body["page_count"] is None
    assert body["content_type"].endswith("wordprocessingml.document")


@pytest.mark.skipif(not ocr_available(OCR), reason="Tesseract language data not installed")
async def test_scanned_pdf_is_ocrd_and_parsed(h: Harness) -> None:
    seen_text: list[str] = []

    def reply(messages: list[Any]) -> str:
        seen_text.append(messages[1].content)
        return fx.llm_resume_json()

    h.use_llm(FakeLLM(reply))
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.scanned_pdf(), "scan.pdf")).json()["id"]
    body = await h.detail(rid)
    assert body["parse_status"] == "completed", body
    assert body["extraction_method"] == "pdf_ocr"
    assert any("OCR was used" in w for w in body["parse_warnings"])
    assert "jordan.sample@example.org" in seen_text[0]  # OCR text reached the LLM


async def test_list_and_download(h: Harness) -> None:
    cid = await h.candidate()
    pdf = fx.text_pdf()
    rid = (await h.upload(cid, pdf, "cv.pdf")).json()["id"]
    listing = (await h.client.get(f"/api/v1/candidates/{cid}/resumes")).json()
    assert [r["id"] for r in listing] == [rid]

    resp = await h.client.get(f"/api/v1/resumes/{rid}/file")
    assert resp.status_code == 200
    assert resp.content == pdf
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert resp.headers["x-content-type-options"] == "nosniff"


# --------------------------------------------------------------------------- failures
@pytest.mark.parametrize(
    ("llm", "code"),
    [
        (FakeLLM(LLMTimeoutError("LLM did not respond within 60s")), "llm_timeout"),
        (FakeLLM("not json at all"), "llm_invalid_response"),
        (FakeLLM(json.dumps({"education": "wrong type"})), "llm_invalid_response"),
        (FakeLLM(fx.llm_resume_json(), configured=False), "llm_not_configured"),
    ],
)
async def test_llm_failures_are_recorded_and_text_is_kept(
    h: Harness, llm: FakeLLM, code: str
) -> None:
    h.use_llm(llm)
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    body = await h.detail(rid)
    assert body["parse_status"] == "failed"
    assert body["parse_error_code"] == code
    assert body["parse_error"]
    assert body["parsed_data"] is None
    # Extraction succeeded, so its result is preserved for a later re-parse.
    assert body["extraction_method"] == "pdf_text"
    assert body["text_char_count"] > 100


async def test_reparse_after_failure_reuses_extracted_text(h: Harness) -> None:
    h.use_llm(FakeLLM(fx.llm_resume_json(), configured=False))
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    assert (await h.detail(rid))["parse_error_code"] == "llm_not_configured"

    llm = FakeLLM(fx.llm_resume_json())
    h.use_llm(llm)
    resp = await h.client.post(f"/api/v1/resumes/{rid}/parse")
    assert resp.status_code == 202
    body = await h.detail(rid)
    assert body["parse_status"] == "completed"
    assert body["parse_error_code"] is None
    assert len(llm.calls) == 1


async def test_scanned_pdf_without_ocr_fails_clearly(h: Harness) -> None:
    h.use_llm(FakeLLM(fx.llm_resume_json()), ocr=OcrConfig(False, None, "eng", 300))
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.scanned_pdf(), "scan.pdf")).json()["id"]
    body = await h.detail(rid)
    assert body["parse_status"] == "failed"
    assert body["parse_error_code"] == "ocr_unavailable"


async def test_missing_stored_file_is_recorded(h: Harness) -> None:
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    row = await h.row(rid)
    assert row is not None
    assert row.storage_path
    h.storage.delete(row.storage_path)
    async with h.sessions() as session:  # force re-extraction
        await session.execute(
            text("UPDATE resumes SET extracted_text = NULL WHERE id = :id"), {"id": row.id}
        )
        await session.commit()
    await h.client.post(f"/api/v1/resumes/{rid}/parse")
    body = await h.detail(rid)
    assert body["parse_error_code"] == "storage_error"


async def test_duplicate_upload_is_rejected(h: Harness) -> None:
    cid = await h.candidate()
    pdf = fx.text_pdf()
    first = (await h.upload(cid, pdf, "cv.pdf")).json()["id"]
    resp = await h.upload(cid, pdf, "renamed.pdf")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "duplicate_resume"
    assert err["details"] == [{"resume_id": first}]
    # A different candidate may upload the same file.
    other = await h.candidate("Alex Example")
    assert (await h.upload(other, pdf, "cv.pdf")).status_code == 202


async def test_reparse_conflicts_while_processing(h: Harness) -> None:
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    async with h.sessions() as session:
        await session.execute(
            text("UPDATE resumes SET parse_status='processing', updated_at=now() WHERE id=:id"),
            {"id": uuid.UUID(rid)},
        )
        await session.commit()
    resp = await h.client.post(f"/api/v1/resumes/{rid}/parse")
    assert resp.status_code == 409


async def test_stale_processing_can_be_reparsed(h: Harness) -> None:
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    async with h.sessions() as session:
        await session.execute(
            text(
                "UPDATE resumes SET parse_status='processing', "
                "updated_at=now() - interval '1 hour' WHERE id=:id"
            ),
            {"id": uuid.UUID(rid)},
        )
        await session.commit()
    assert (await h.client.post(f"/api/v1/resumes/{rid}/parse")).status_code == 202
    assert (await h.detail(rid))["parse_status"] == "completed"


# --------------------------------------------------------------------------- deletion
async def test_delete_removes_row_parsed_data_and_file(h: Harness) -> None:
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    row = await h.row(rid)
    assert row is not None
    assert row.storage_path
    assert h.storage.exists(row.storage_path)

    resp = await h.client.delete(f"/api/v1/resumes/{rid}")
    assert resp.status_code == 204
    assert await h.row(rid) is None
    assert not h.storage.exists(row.storage_path)
    assert (await h.client.get(f"/api/v1/resumes/{rid}")).status_code == 404
    assert (await h.client.delete(f"/api/v1/resumes/{rid}")).status_code == 404


async def test_resume_deleted_before_processing_is_ignored(h: Harness) -> None:
    cid = await h.candidate()
    rid = (await h.upload(cid, fx.text_pdf(), "cv.pdf")).json()["id"]
    await h.client.delete(f"/api/v1/resumes/{rid}")
    processor: ResumeProcessor = h.app.state.resume_processor
    await processor.process(uuid.UUID(rid))  # must not raise


# --------------------------------------------------------------------------- not found / validation
async def test_unknown_ids(h: Harness) -> None:
    unknown = uuid.uuid4()
    for method, url in [
        ("GET", f"/api/v1/resumes/{unknown}"),
        ("GET", f"/api/v1/resumes/{unknown}/file"),
        ("POST", f"/api/v1/resumes/{unknown}/parse"),
        ("DELETE", f"/api/v1/resumes/{unknown}"),
        ("GET", f"/api/v1/candidates/{unknown}"),
        ("GET", f"/api/v1/candidates/{unknown}/resumes"),
    ]:
        resp = await h.client.request(method, url)
        assert resp.status_code == 404, (method, url)
        assert resp.json()["error"]["code"] == "not_found"


async def test_rejected_upload_stores_nothing(h: Harness) -> None:
    cid = await h.candidate()
    resp = await h.upload(cid, b"%PDF-1.4 truncated", "cv.pdf")
    assert resp.status_code == 422
    async with h.sessions() as session:
        assert (await session.execute(select(Resume))).first() is None
    assert list(h.storage.root.iterdir()) == []


async def test_candidate_duplicate_email(h: Harness) -> None:
    payload = {"full_name": "Jordan Sample", "email": "jordan.sample@example.org"}
    assert (await h.client.post("/api/v1/candidates", json=payload)).status_code == 201
    resp = await h.client.post("/api/v1/candidates", json=payload)
    assert resp.status_code == 409
