"""JD upload-to-database workflow against live PostgreSQL (isolated temporary schema).

Real: HTTP API, validation, storage, extraction (PDF/DOCX/TXT), Tesseract OCR,
background processing, persistence. Test double: the LLM client only.
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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.integrations.llm import LLMResponseError, LLMTimeoutError
from app.main import create_app
from app.models import Candidate, Evaluation, JobDescription
from app.services.job_description.processor import JobDescriptionProcessor
from app.services.job_description.structuring import JobDescriptionStructurer
from app.services.resume.extraction import OcrConfig, ocr_available
from app.services.resume.storage import ResumeStorage
from tests import jd_fixtures as jf
from tests.conftest import SettingsFactory, client_for
from tests.llm_fakes import FakeLLM

pytestmark = pytest.mark.integration

OCR = OcrConfig(enabled=True, tessdata_dir=None, language="eng", dpi=300)
BASE = "/api/v1/job-descriptions"


class Harness:
    def __init__(
        self, app: FastAPI, storage: ResumeStorage, sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        self.app = app
        self.storage = storage
        self.sessions = sessions
        self.client: httpx.AsyncClient

    def use_llm(self, llm: FakeLLM) -> None:
        self.app.state.jd_processor = JobDescriptionProcessor(
            session_factory=self.sessions,
            storage=self.storage,
            structurer=JobDescriptionStructurer(llm, max_attempts=2, max_input_chars=60_000),
            ocr=OCR,
        )

    async def upload(self, data: bytes, name: str) -> httpx.Response:
        return await self.client.post(
            f"{BASE}/upload", files={"file": (name, data, "application/octet-stream")}
        )

    async def uploaded_id(self, data: bytes, name: str) -> str:
        resp = await self.upload(data, name)
        assert resp.status_code == 202, resp.text
        return str(resp.json()["id"])

    async def detail(self, jd_id: str) -> dict[str, Any]:
        resp = await self.client.get(f"{BASE}/{jd_id}")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    async def row(self, jd_id: str) -> JobDescription | None:
        async with self.sessions() as session:
            return await session.get(JobDescription, uuid.UUID(jd_id))


@pytest.fixture
async def h(
    migrated_engine: AsyncEngine, make_settings: SettingsFactory, tmp_path: Path
) -> AsyncIterator[Harness]:
    app = create_app(make_settings())
    sessions = async_sessionmaker(migrated_engine, expire_on_commit=False)
    storage = ResumeStorage(tmp_path / "jds", extensions=("pdf", "docx", "txt"))
    storage.ensure_ready()
    app.state.db_engine = migrated_engine
    app.state.session_factory = sessions
    app.state.jd_storage = storage
    harness = Harness(app, storage, sessions)
    harness.use_llm(FakeLLM(jf.llm_jd_json()))
    async with client_for(app) as client:
        harness.client = client
        yield harness


@pytest.mark.parametrize(
    ("data", "name", "method"),
    [
        (jf.jd_txt(), "role.txt", "txt"),
        (jf.jd_pdf(), "role.pdf", "pdf_text"),
        (jf.jd_docx(), "role.docx", "docx"),
    ],
)
async def test_upload_to_database(h: Harness, data: bytes, name: str, method: str) -> None:
    jd_id = await h.uploaded_id(data, name)
    detail = await h.detail(jd_id)
    assert detail["parse_status"] == "completed", detail
    assert detail["extraction_method"] == method
    assert detail["title"] == "Senior Backend Engineer"
    assert detail["company"] == "Example Analytics Ltd"
    assert detail["parser_model"] == "fake-model-served"
    assert detail["text_char_count"] > 300

    parsed = (await h.client.get(f"{BASE}/{jd_id}/parsed")).json()
    assert parsed["source"] == "extracted"
    assert [s["name"] for s in parsed["data"]["required_skills"]] == [
        "Python",
        "FastAPI",
        "PostgreSQL",
    ]
    assert [s["name"] for s in parsed["data"]["preferred_skills"]] == [
        "Docker",
        "Kubernetes",
        "AWS",
    ]
    assert parsed["technology_summary"]["databases"] == ["PostgreSQL"]
    assert parsed["data"]["minimum_experience"]["years_min"] == 5

    row = await h.row(jd_id)
    assert row is not None
    assert row.extracted_text == row.description_text
    assert row.analysis == parsed["extracted"]
    assert row.storage_path is not None
    assert h.storage.exists(row.storage_path)
    assert row.created_at is not None


@pytest.mark.skipif(not ocr_available(OCR), reason="Tesseract language data not installed")
async def test_scanned_pdf(h: Harness) -> None:
    seen: list[str] = []

    def reply(messages: list[Any]) -> str:
        seen.append(messages[1].content)
        return jf.llm_jd_json()

    h.use_llm(FakeLLM(reply))
    jd_id = await h.uploaded_id(jf.jd_scanned_pdf(), "scan.pdf")
    detail = await h.detail(jd_id)
    assert detail["parse_status"] == "completed"
    assert detail["extraction_method"] == "pdf_ocr"
    assert any("OCR" in w for w in detail["parse_warnings"])
    assert "PostgreSQL" in seen[0]


async def test_parsed_before_completion_is_409(h: Harness) -> None:
    h.use_llm(FakeLLM(jf.llm_jd_json(), configured=False))
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    resp = await h.client.get(f"{BASE}/{jd_id}/parsed")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "parse_not_completed"


@pytest.mark.parametrize(
    ("llm", "code"),
    [
        (FakeLLM(LLMTimeoutError("LLM did not respond within 60s")), "llm_timeout"),
        (FakeLLM(LLMResponseError("LLM provider returned HTTP 503")), "llm_error"),
        (FakeLLM("{not json"), "llm_invalid_response"),
        (FakeLLM(json.dumps({"required_skills": "Python"})), "llm_invalid_response"),
        (FakeLLM(jf.llm_jd_json(), configured=False), "llm_not_configured"),
    ],
)
async def test_llm_failures_are_recorded(h: Harness, llm: FakeLLM, code: str) -> None:
    h.use_llm(llm)
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    detail = await h.detail(jd_id)
    assert detail["parse_status"] == "failed"
    assert detail["parse_error_code"] == code
    assert detail["text_char_count"] > 300  # extraction kept for re-parse
    assert detail["title"] is None  # nothing invented when structuring fails


async def test_missing_requirements_are_stored_as_empty(h: Harness) -> None:
    h.use_llm(FakeLLM(json.dumps({"job_title": "Senior Backend Engineer"})))
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    data = (await h.client.get(f"{BASE}/{jd_id}/parsed")).json()["data"]
    assert data["required_skills"] == []
    assert data["preferred_skills"] == []
    assert data["minimum_experience"] is None
    assert data["education_requirements"] == []


async def test_reparse_after_failure(h: Harness) -> None:
    h.use_llm(FakeLLM(jf.llm_jd_json(), configured=False))
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    llm = FakeLLM(jf.llm_jd_json())
    h.use_llm(llm)
    assert (await h.client.post(f"{BASE}/{jd_id}/parse")).status_code == 202
    assert (await h.detail(jd_id))["parse_status"] == "completed"
    assert len(llm.calls) == 1


async def test_review_and_discard(h: Harness) -> None:
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    parsed = (await h.client.get(f"{BASE}/{jd_id}/parsed")).json()
    corrected = parsed["data"]
    corrected["job_title"] = "Senior Backend Engineer (Data Platform)"
    corrected["preferred_skills"].append(
        {"name": "Terraform", "category": "devops", "evidence": None}
    )
    corrected["minimum_experience"]["years_min"] = 6

    resp = await h.client.put(f"{BASE}/{jd_id}/parsed", json=corrected)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "reviewed"
    assert body["data"]["minimum_experience"]["years_min"] == 6
    assert body["extracted"]["minimum_experience"]["years_min"] == 5  # LLM output untouched
    assert "Terraform" in body["technology_summary"]["cloud_and_devops"]
    detail = await h.detail(jd_id)
    assert detail["has_review"] is True
    assert detail["title"] == "Senior Backend Engineer (Data Platform)"

    reverted = (await h.client.delete(f"{BASE}/{jd_id}/parsed/review")).json()
    assert reverted["source"] == "extracted"
    assert (await h.detail(jd_id))["title"] == "Senior Backend Engineer"


async def test_review_is_validated(h: Harness) -> None:
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    bad = {"job_title": "X", "minimum_experience": {"years_min": 9, "years_max": 2}}
    resp = await h.client.put(f"{BASE}/{jd_id}/parsed", json=bad)
    assert resp.status_code == 422
    resp = await h.client.put(f"{BASE}/{jd_id}/parsed", json={"job_title": "X", "salary": "1"})
    assert resp.status_code == 422


async def test_reparse_never_overwrites_review(h: Harness) -> None:
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    data = (await h.client.get(f"{BASE}/{jd_id}/parsed")).json()["data"]
    data["job_title"] = "Corrected Title"
    await h.client.put(f"{BASE}/{jd_id}/parsed", json=data)
    await h.client.post(f"{BASE}/{jd_id}/parse")
    after = (await h.client.get(f"{BASE}/{jd_id}/parsed")).json()
    assert after["source"] == "reviewed"
    assert after["data"]["job_title"] == "Corrected Title"
    assert any("reviewed" in w for w in after["warnings"])


async def test_list_pagination(h: Harness) -> None:
    ids = [
        await h.uploaded_id(jf.jd_txt(jf.JD_TEXT + f"\nRef {i}"), f"jd{i}.txt") for i in range(3)
    ]
    page = (await h.client.get(f"{BASE}?limit=2&offset=0")).json()
    assert page["total"] == 3
    assert page["limit"] == 2
    assert len(page["items"]) == 2
    assert page["items"][0]["id"] == ids[-1]  # newest first
    rest = (await h.client.get(f"{BASE}?limit=2&offset=2")).json()
    assert [i["id"] for i in rest["items"]] == [ids[0]]


async def test_duplicate_upload(h: Harness) -> None:
    first = await h.uploaded_id(jf.jd_txt(), "role.txt")
    resp = await h.upload(jf.jd_txt(), "renamed.txt")
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == [{"job_description_id": first}]


async def test_download_original(h: Harness) -> None:
    data = jf.jd_txt()
    jd_id = await h.uploaded_id(data, "role.txt")
    resp = await h.client.get(f"{BASE}/{jd_id}/file")
    assert resp.status_code == 200
    assert resp.content == data
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in resp.headers["content-security-policy"]


async def test_delete(h: Harness) -> None:
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    row = await h.row(jd_id)
    assert row is not None
    assert row.storage_path
    assert (await h.client.delete(f"{BASE}/{jd_id}")).status_code == 204
    assert await h.row(jd_id) is None
    assert not h.storage.exists(row.storage_path)
    assert (await h.client.get(f"{BASE}/{jd_id}")).status_code == 404


async def test_delete_blocked_while_evaluations_reference_it(h: Harness) -> None:
    jd_id = await h.uploaded_id(jf.jd_txt(), "role.txt")
    async with h.sessions() as session:
        candidate = Candidate(full_name="Jordan Sample")
        session.add(candidate)
        await session.flush()
        session.add(Evaluation(candidate_id=candidate.id, job_description_id=uuid.UUID(jd_id)))
        await session.commit()
    resp = await h.client.delete(f"{BASE}/{jd_id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "job_description_in_use"
    row = await h.row(jd_id)
    assert row is not None
    assert row.storage_path
    assert h.storage.exists(row.storage_path)


async def test_rejected_upload_stores_nothing(h: Harness) -> None:
    resp = await h.upload(b"MZ\x90\x00binary payload", "role.txt")
    assert resp.status_code == 415
    async with h.sessions() as session:
        count = (await session.execute(text("SELECT count(*) FROM job_descriptions"))).scalar_one()
    assert count == 0
    assert list(h.storage.root.iterdir()) == []


async def test_not_found(h: Harness) -> None:
    unknown = uuid.uuid4()
    for method, path in [
        ("GET", f"{BASE}/{unknown}"),
        ("GET", f"{BASE}/{unknown}/parsed"),
        ("PUT", f"{BASE}/{unknown}/parsed"),
        ("DELETE", f"{BASE}/{unknown}/parsed/review"),
        ("POST", f"{BASE}/{unknown}/parse"),
        ("GET", f"{BASE}/{unknown}/file"),
        ("DELETE", f"{BASE}/{unknown}"),
    ]:
        kwargs: dict[str, Any] = {"json": json.loads(jf.llm_jd_json())} if method == "PUT" else {}
        resp = await h.client.request(method, path, **kwargs)
        assert resp.status_code == 404, (method, path, resp.text)
