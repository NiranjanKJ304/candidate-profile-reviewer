"""Background processor resilience when the database fails mid-pipeline."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.enums import ParseStatus, ResumeErrorCode
from app.models import Resume
from app.services.resume.extraction import OcrConfig
from app.services.resume.processor import ResumeProcessor
from app.services.resume.storage import ResumeStorage
from app.services.resume.structuring import ResumeStructurer
from tests import resume_fixtures as fx
from tests.llm_fakes import FakeLLM

DB_DOWN = OperationalError(
    "UPDATE resumes", {}, ConnectionResetError("server closed the connection")
)
OCR = OcrConfig(enabled=False, tessdata_dir=None, language="eng", dpi=300)


class Session:
    def __init__(
        self,
        resume: Resume | None,
        *,
        get_error: Exception | None = None,
        fail_commit_from: int | None = None,
    ) -> None:
        self.resume = resume
        self.get_error = get_error
        self.fail_commit_from = fail_commit_from
        self.commits = 0

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _model: type, _ident: Any) -> Resume | None:
        if self.get_error:
            raise self.get_error
        return self.resume

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit_from is not None and self.commits >= self.fail_commit_from:
            raise DB_DOWN


def _processor(
    session: Session, storage: ResumeStorage, llm: FakeLLM | None = None
) -> ResumeProcessor:
    return ResumeProcessor(
        session_factory=lambda: session,  # type: ignore[arg-type]
        storage=storage,
        structurer=ResumeStructurer(
            llm or FakeLLM(fx.llm_resume_json()), max_attempts=1, max_input_chars=60_000
        ),
        ocr=OCR,
    )


@pytest.fixture
def stored(tmp_path: Path) -> tuple[ResumeStorage, Resume]:
    storage = ResumeStorage(tmp_path)
    rid = uuid.uuid4()
    key = storage.key_for(rid, ".pdf")
    storage.save(key, fx.text_pdf())
    resume = Resume(
        id=rid, candidate_id=uuid.uuid4(), storage_path=key, parse_status=ParseStatus.PENDING
    )
    return storage, resume


async def test_happy_path_without_database(stored: tuple[ResumeStorage, Resume]) -> None:
    storage, resume = stored
    await _processor(Session(resume), storage).process(resume.id)
    assert resume.parse_status is ParseStatus.COMPLETED
    assert resume.parsed_data is not None


async def test_database_down_at_start_is_logged_not_raised(
    stored: tuple[ResumeStorage, Resume], caplog: pytest.LogCaptureFixture
) -> None:
    storage, resume = stored
    caplog.set_level(logging.ERROR, logger="app.resume.processor")
    await _processor(Session(resume, get_error=DB_DOWN), storage).process(resume.id)  # no exception
    assert any("crashed" in r.getMessage() for r in caplog.records)


async def test_database_lost_while_saving_result_is_logged(
    stored: tuple[ResumeStorage, Resume], caplog: pytest.LogCaptureFixture
) -> None:
    storage, resume = stored
    caplog.set_level(logging.ERROR, logger="app.resume.processor")
    session = Session(resume, fail_commit_from=3)  # processing + extraction commit, then fail
    await _processor(session, storage).process(resume.id)
    assert session.commits == 3
    assert any("crashed" in r.getMessage() for r in caplog.records)


async def test_database_lost_while_recording_failure(
    stored: tuple[ResumeStorage, Resume], caplog: pytest.LogCaptureFixture
) -> None:
    storage, resume = stored
    caplog.set_level(logging.ERROR, logger="app.resume.processor")
    session = Session(resume, fail_commit_from=3)
    await _processor(session, storage, FakeLLM("invalid")).process(resume.id)
    assert resume.parse_status is ParseStatus.FAILED
    assert resume.parse_error_code is ResumeErrorCode.LLM_INVALID_RESPONSE
    assert any("could not record resume failure" in r.getMessage() for r in caplog.records)


async def test_unexpected_exception_is_recorded_as_internal_error(
    stored: tuple[ResumeStorage, Resume],
) -> None:
    storage, resume = stored

    def boom(_messages: list[Any]) -> str:
        raise RuntimeError("unexpected")

    await _processor(Session(resume), storage, FakeLLM(boom)).process(resume.id)
    assert resume.parse_status is ParseStatus.FAILED
    assert resume.parse_error_code is ResumeErrorCode.INTERNAL_ERROR
    assert "RuntimeError" in (resume.parse_error or "")


async def test_logs_never_contain_resume_text(
    stored: tuple[ResumeStorage, Resume], caplog: pytest.LogCaptureFixture
) -> None:
    storage, resume = stored
    caplog.set_level(logging.DEBUG)
    await _processor(Session(resume), storage).process(resume.id)
    joined = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "jordan.sample@example.org" not in joined
    assert "Example Corp" not in joined
