"""JD background processor: success, DB failures mid-pipeline, review preservation."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.enums import ExtractionMethod, ParseStatus, ResumeErrorCode
from app.models import JobDescription
from app.services.job_description.processor import JobDescriptionProcessor
from app.services.job_description.structuring import JobDescriptionStructurer
from app.services.resume.extraction import OcrConfig
from app.services.resume.storage import ResumeStorage
from tests import jd_fixtures as jf
from tests.llm_fakes import FakeLLM

DB_DOWN = OperationalError("UPDATE job_descriptions", {}, ConnectionResetError("closed"))
OCR = OcrConfig(enabled=False, tessdata_dir=None, language="eng", dpi=300)


class Session:
    def __init__(
        self,
        jd: JobDescription | None,
        *,
        get_error: Exception | None = None,
        fail_commit_from: int | None = None,
    ) -> None:
        self.jd = jd
        self.get_error = get_error
        self.fail_commit_from = fail_commit_from
        self.commits = 0

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _model: type, _ident: Any) -> JobDescription | None:
        if self.get_error:
            raise self.get_error
        return self.jd

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit_from is not None and self.commits >= self.fail_commit_from:
            raise DB_DOWN


@pytest.fixture
def stored(tmp_path: Path) -> tuple[ResumeStorage, JobDescription]:
    storage = ResumeStorage(tmp_path, extensions=("pdf", "docx", "txt"))
    jd_id = uuid.uuid4()
    key = storage.key_for(jd_id, ".txt")
    storage.save(key, jf.jd_txt())
    return storage, JobDescription(id=jd_id, storage_path=key, parse_status=ParseStatus.PENDING)


def _processor(
    session: Session, storage: ResumeStorage, llm: FakeLLM | None = None
) -> JobDescriptionProcessor:
    return JobDescriptionProcessor(
        session_factory=lambda: session,  # type: ignore[arg-type]
        storage=storage,
        structurer=JobDescriptionStructurer(
            llm or FakeLLM(jf.llm_jd_json()), max_attempts=1, max_input_chars=60_000
        ),
        ocr=OCR,
    )


async def test_success_sets_title_company_and_text(
    stored: tuple[ResumeStorage, JobDescription],
) -> None:
    storage, jd = stored
    await _processor(Session(jd), storage).process(jd.id)
    assert jd.parse_status is ParseStatus.COMPLETED
    assert jd.extraction_method is ExtractionMethod.TXT
    assert jd.title == "Senior Backend Engineer"
    assert jd.company == "Example Analytics Ltd"
    assert jd.description_text == jd.extracted_text
    assert jd.analysis is not None
    assert jd.parse_warnings == []


async def test_existing_review_is_kept_and_flagged(
    stored: tuple[ResumeStorage, JobDescription],
) -> None:
    storage, jd = stored
    jd.reviewed_data = {"job_title": "Corrected"}
    await _processor(Session(jd), storage).process(jd.id)
    assert jd.reviewed_data == {"job_title": "Corrected"}
    assert jd.parse_warnings is not None
    assert any("reviewed" in w for w in jd.parse_warnings)


async def test_database_down_is_logged_not_raised(
    stored: tuple[ResumeStorage, JobDescription], caplog: pytest.LogCaptureFixture
) -> None:
    storage, jd = stored
    caplog.set_level(logging.ERROR, logger="app.job_description.processor")
    await _processor(Session(jd, get_error=DB_DOWN), storage).process(jd.id)
    assert any("crashed" in r.getMessage() for r in caplog.records)


async def test_database_lost_while_recording_failure(
    stored: tuple[ResumeStorage, JobDescription], caplog: pytest.LogCaptureFixture
) -> None:
    storage, jd = stored
    caplog.set_level(logging.ERROR, logger="app.job_description.processor")
    await _processor(Session(jd, fail_commit_from=3), storage, FakeLLM("invalid")).process(jd.id)
    assert jd.parse_status is ParseStatus.FAILED
    assert jd.parse_error_code is ResumeErrorCode.LLM_INVALID_RESPONSE
    assert any("could not record" in r.getMessage() for r in caplog.records)


async def test_missing_file(tmp_path: Path) -> None:
    storage = ResumeStorage(tmp_path, extensions=("pdf", "docx", "txt"))
    jd = JobDescription(
        id=uuid.uuid4(), storage_path=f"{uuid.uuid4().hex}.txt", parse_status=ParseStatus.PENDING
    )
    await _processor(Session(jd), storage).process(jd.id)
    assert jd.parse_error_code is ResumeErrorCode.STORAGE_ERROR


async def test_logs_never_contain_jd_text(
    stored: tuple[ResumeStorage, JobDescription], caplog: pytest.LogCaptureFixture
) -> None:
    storage, jd = stored
    caplog.set_level(logging.DEBUG)
    await _processor(Session(jd), storage).process(jd.id)
    joined = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "Example Analytics" not in joined
    assert "communication skills" not in joined
