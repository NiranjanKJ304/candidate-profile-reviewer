"""JD processing pipeline: stored file -> text -> structured JSON -> database.

Mirrors the resume processor: runs as a background task after HTTP 202, records
every outcome on the row, and keeps extracted text when structuring fails.
Human corrections (``reviewed_data``) are never overwritten by a re-parse.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import ParseStatus, ResumeErrorCode
from app.models import JobDescription
from app.services.job_description.structuring import JobDescriptionStructurer
from app.services.resume.errors import ProcessingError
from app.services.resume.extraction import OcrConfig, extract_text
from app.services.resume.storage import ResumeStorage
from app.services.resume.validation import FileKind

logger = logging.getLogger("app.job_description.processor")

_KIND_BY_EXTENSION: dict[str, FileKind] = {"pdf": "pdf", "docx": "docx", "txt": "txt"}


class JobDescriptionProcessor:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ResumeStorage,
        structurer: JobDescriptionStructurer,
        ocr: OcrConfig,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._structurer = structurer
        self._ocr = ocr

    async def process(self, jd_id: uuid.UUID) -> None:
        """Process one JD. Never raises: failures are recorded or logged."""
        started = time.perf_counter()
        try:
            await self._run(jd_id)
        except Exception:
            logger.exception("job description processing crashed", extra={"jd_id": str(jd_id)})
        finally:
            logger.info(
                "job description processing finished",
                extra={
                    "jd_id": str(jd_id),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    async def _run(self, jd_id: uuid.UUID) -> None:
        async with self._sessions() as session:
            jd = await session.get(JobDescription, jd_id)
            if jd is None:
                logger.info(
                    "job description deleted before processing", extra={"jd_id": str(jd_id)}
                )
                return
            jd.parse_status = ParseStatus.PROCESSING
            jd.parse_error = None
            jd.parse_error_code = None
            await session.commit()

            try:
                if jd.extracted_text:
                    text = jd.extracted_text
                    warnings: list[str] = []
                else:
                    text, warnings = await self._extract(jd)
                    await session.commit()

                result = await self._structurer.structure(text)
                jd.analysis = result.data.model_dump(mode="json")
                if result.data.job_title:
                    jd.title = result.data.job_title
                if result.data.company_name:
                    jd.company = result.data.company_name
                jd.parser_model = result.model
                jd.parse_warnings = warnings + result.warnings
                if jd.reviewed_data is not None:
                    jd.parse_warnings.append(
                        "A reviewed (corrected) version from an earlier extraction is still "
                        "in use; "
                        "discard it to use this extraction"
                    )
                jd.parse_status = ParseStatus.COMPLETED
                jd.parsed_at = datetime.now(UTC)
                logger.info(
                    "job description parsed",
                    extra={
                        "jd_id": str(jd_id),
                        "required_skills": len(result.data.required_skills),
                        "preferred_skills": len(result.data.preferred_skills),
                        "warnings": len(jd.parse_warnings),
                    },
                )
            except ProcessingError as exc:
                await self._fail(session, jd, exc.code, exc.message)
                return
            except SQLAlchemyError:
                raise
            except Exception as exc:
                logger.exception(
                    "unexpected job description processing error", extra={"jd_id": str(jd_id)}
                )
                await self._fail(
                    session,
                    jd,
                    ResumeErrorCode.INTERNAL_ERROR,
                    f"Unexpected error ({type(exc).__name__})",
                )
                return

            await session.commit()

    async def _extract(self, jd: JobDescription) -> tuple[str, list[str]]:
        if not jd.storage_path:
            raise ProcessingError(
                ResumeErrorCode.STORAGE_ERROR, "The job description has no stored file"
            )
        data = await asyncio.to_thread(self._storage.read, jd.storage_path)
        kind = _KIND_BY_EXTENSION.get(jd.storage_path.rsplit(".", 1)[-1])
        if kind is None:
            raise ProcessingError(ResumeErrorCode.UNSUPPORTED_TYPE, "Unsupported stored file type")
        extraction = await asyncio.to_thread(extract_text, data, kind, self._ocr)
        jd.extracted_text = extraction.text
        jd.description_text = extraction.text
        jd.extraction_method = extraction.method
        jd.page_count = extraction.page_count
        return extraction.text, extraction.warnings

    @staticmethod
    async def _fail(
        session: AsyncSession, jd: JobDescription, code: ResumeErrorCode, message: str
    ) -> None:
        jd.parse_status = ParseStatus.FAILED
        jd.parse_error_code = code
        jd.parse_error = message[:2000]
        logger.warning(
            "job description processing failed",
            extra={"jd_id": str(jd.id), "error_code": code.value},
        )
        try:
            await session.commit()
        except SQLAlchemyError:
            logger.exception(
                "could not record job description failure", extra={"jd_id": str(jd.id)}
            )
