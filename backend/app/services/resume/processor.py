"""Resume processing pipeline: stored file -> text -> structured JSON -> database.

Runs as a FastAPI background task after the upload response (HTTP 202). Every
outcome is written to the resume row: ``completed`` with ``parsed_data``, or
``failed`` with ``parse_error_code`` / ``parse_error``. Extracted text is saved
as soon as it exists, so an LLM failure does not lose the extraction.
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
from app.models import Resume
from app.services.resume.errors import ProcessingError
from app.services.resume.extraction import OcrConfig, extract_text
from app.services.resume.storage import ResumeStorage
from app.services.resume.structuring import ResumeStructurer
from app.services.resume.validation import FileKind

logger = logging.getLogger("app.resume.processor")

_KIND_BY_EXTENSION: dict[str, FileKind] = {"pdf": "pdf", "docx": "docx"}


class ResumeProcessor:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ResumeStorage,
        structurer: ResumeStructurer,
        ocr: OcrConfig,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._structurer = structurer
        self._ocr = ocr

    async def process(self, resume_id: uuid.UUID) -> None:
        """Process one resume. Never raises: failures are recorded or logged."""
        started = time.perf_counter()
        try:
            await self._run(resume_id)
        except Exception:
            logger.exception("resume processing crashed", extra={"resume_id": str(resume_id)})
        finally:
            logger.info(
                "resume processing finished",
                extra={
                    "resume_id": str(resume_id),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    async def _run(self, resume_id: uuid.UUID) -> None:
        async with self._sessions() as session:
            resume = await session.get(Resume, resume_id)
            if resume is None:
                logger.info("resume deleted before processing", extra={"resume_id": str(resume_id)})
                return
            resume.parse_status = ParseStatus.PROCESSING
            resume.parse_error = None
            resume.parse_error_code = None
            await session.commit()

            try:
                if resume.extracted_text:
                    text = resume.extracted_text  # re-parse: reuse the stored extraction
                    warnings: list[str] = []
                else:
                    text, warnings = await self._extract(resume)
                    await session.commit()

                result = await self._structurer.structure(text)
                resume.parsed_data = result.resume.model_dump(mode="json")
                resume.parser_model = result.model
                resume.parse_warnings = warnings + result.warnings
                resume.parse_status = ParseStatus.COMPLETED
                resume.parsed_at = datetime.now(UTC)
                logger.info(
                    "resume parsed",
                    extra={
                        "resume_id": str(resume_id),
                        "sections": result.resume.section_counts(),
                        "warnings": len(resume.parse_warnings),
                    },
                )
            except ProcessingError as exc:
                await self._fail(session, resume, exc.code, exc.message)
                return
            except SQLAlchemyError:
                raise
            except Exception as exc:
                logger.exception(
                    "unexpected resume processing error", extra={"resume_id": str(resume_id)}
                )
                await self._fail(
                    session,
                    resume,
                    ResumeErrorCode.INTERNAL_ERROR,
                    f"Unexpected error ({type(exc).__name__})",
                )
                return

            await session.commit()

    async def _extract(self, resume: Resume) -> tuple[str, list[str]]:
        if not resume.storage_path:
            raise ProcessingError(ResumeErrorCode.STORAGE_ERROR, "The resume has no stored file")
        data = await asyncio.to_thread(self._storage.read, resume.storage_path)
        kind = _KIND_BY_EXTENSION.get(resume.storage_path.rsplit(".", 1)[-1])
        if kind is None:
            raise ProcessingError(ResumeErrorCode.UNSUPPORTED_TYPE, "Unsupported stored file type")
        extraction = await asyncio.to_thread(extract_text, data, kind, self._ocr)
        resume.extracted_text = extraction.text
        resume.extraction_method = extraction.method
        resume.page_count = extraction.page_count
        return extraction.text, extraction.warnings

    @staticmethod
    async def _fail(
        session: AsyncSession, resume: Resume, code: ResumeErrorCode, message: str
    ) -> None:
        resume.parse_status = ParseStatus.FAILED
        resume.parse_error_code = code
        resume.parse_error = message[:2000]
        logger.warning(
            "resume processing failed",
            extra={"resume_id": str(resume.id), "error_code": code.value},
        )
        try:
            await session.commit()
        except SQLAlchemyError:
            logger.exception("could not record resume failure", extra={"resume_id": str(resume.id)})
