"""Resume upload, retrieval, re-parse and deletion."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import ProcessorDep, SessionDep, SettingsDep, StorageDep
from app.api.v1.candidates import load_candidate
from app.core.enums import ParseStatus, ResumeErrorCode
from app.core.errors import error_response
from app.models import Resume
from app.schemas.common import ErrorResponse
from app.schemas.domain import ResumeDetail, ResumeRead
from app.services.resume.errors import ResumeError, UploadRejectedError
from app.services.resume.validation import validate_upload

logger = logging.getLogger("app.api.resumes")

router = APIRouter(tags=["resumes"])

# A "processing" row older than this is treated as abandoned and may be re-parsed.
STALE_PROCESSING_AFTER = timedelta(minutes=10)
MULTIPART_OVERHEAD_BYTES = 64 * 1024

_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Empty file"},
    404: {"model": ErrorResponse, "description": "Candidate not found"},
    409: {"model": ErrorResponse, "description": "Same file already uploaded for this candidate"},
    413: {"model": ErrorResponse, "description": "File too large"},
    415: {
        "model": ErrorResponse,
        "description": "Not a PDF/DOCX, or extension does not match content",
    },
    422: {"model": ErrorResponse, "description": "Corrupted, encrypted, unsafe or too many pages"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}


def _upload_error(exc: ResumeError, http_status: int) -> JSONResponse:
    return error_response(http_status, exc.code.value, exc.message)


async def _get_resume(session: SessionDep, resume_id: uuid.UUID) -> Resume:
    resume = await session.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    return resume


@router.post(
    "/candidates/{candidate_id}/resumes",
    response_model=ResumeRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a resume (PDF or DOCX); parsing runs in the background",
    responses=_UPLOAD_ERRORS,
)
async def upload_resume(
    candidate_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    processor: ProcessorDep,
    file: Annotated[UploadFile, File(description="Resume file (.pdf or .docx)")],
) -> Response | Resume:
    max_bytes = settings.resume_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + MULTIPART_OVERHEAD_BYTES:
        exc = UploadRejectedError(
            ResumeErrorCode.FILE_TOO_LARGE,
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit",
        )
        return _upload_error(exc, exc.http_status)

    await load_candidate(session, candidate_id)

    data = await file.read(max_bytes + 1)
    try:
        upload = await asyncio.to_thread(
            validate_upload,
            data,
            file.filename,
            max_bytes=max_bytes,
            max_pages=settings.resume_max_pages,
        )
    except UploadRejectedError as exc:
        logger.info("resume upload rejected", extra={"error_code": exc.code.value})
        return _upload_error(exc, exc.http_status)

    duplicate = await session.scalar(
        select(Resume.id).where(Resume.candidate_id == candidate_id, Resume.sha256 == upload.sha256)
    )
    if duplicate is not None:
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_resume",
            "This file has already been uploaded for this candidate",
            details=[{"resume_id": str(duplicate)}],
        )

    resume_id = uuid.uuid4()
    key = storage.key_for(resume_id, upload.extension)
    try:
        await asyncio.to_thread(storage.save, key, upload.data)
    except ResumeError as exc:
        logger.error("resume storage failed", extra={"resume_id": str(resume_id)})
        return _upload_error(exc, status.HTTP_500_INTERNAL_SERVER_ERROR)

    resume = Resume(
        id=resume_id,
        candidate_id=candidate_id,
        original_filename=upload.safe_filename,
        content_type=upload.content_type,
        file_size_bytes=upload.size,
        sha256=upload.sha256,
        storage_path=key,
        page_count=upload.page_count,
        parse_status=ParseStatus.PENDING,
    )
    session.add(resume)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await asyncio.to_thread(storage.delete, key)
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_resume",
            "This file has already been uploaded for this candidate",
        )
    except Exception:
        # Never leave an orphaned file when the database write fails.
        await asyncio.to_thread(storage.delete, key)
        raise
    await session.refresh(resume)

    background.add_task(processor.process, resume.id)
    logger.info(
        "resume uploaded",
        extra={"resume_id": str(resume.id), "kind": upload.kind, "size": upload.size},
    )
    return resume


@router.get(
    "/resumes/{resume_id}",
    response_model=ResumeDetail,
    summary="Get a resume with its parsing status and structured data",
    responses={404: {"model": ErrorResponse}},
)
async def get_resume(resume_id: uuid.UUID, session: SessionDep, response: Response) -> ResumeDetail:
    resume = await _get_resume(session, resume_id)
    response.headers["Cache-Control"] = "no-store"
    return ResumeDetail.from_model(resume)


@router.get(
    "/resumes/{resume_id}/file",
    summary="Download the original resume file",
    responses={404: {"model": ErrorResponse}},
    response_class=Response,
)
async def download_resume_file(
    resume_id: uuid.UUID, session: SessionDep, storage: StorageDep
) -> Response:
    resume = await _get_resume(session, resume_id)
    if not resume.storage_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The original file is not available")
    try:
        data = await asyncio.to_thread(storage.read, resume.storage_path)
    except ResumeError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "The original file is not available"
        ) from exc
    filename = quote(resume.original_filename)
    return Response(
        content=data,
        media_type=resume.content_type,
        headers={
            # Always a download, never rendered inline by the browser.
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post(
    "/resumes/{resume_id}/parse",
    response_model=ResumeRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run parsing (e.g. after configuring the LLM key or a failure)",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def reparse_resume(
    resume_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    processor: ProcessorDep,
) -> Resume:
    resume = await _get_resume(session, resume_id)
    if resume.parse_status in (ParseStatus.PROCESSING, ParseStatus.PENDING):
        age = datetime.now(UTC) - resume.updated_at
        if age < STALE_PROCESSING_AFTER:
            raise HTTPException(status.HTTP_409_CONFLICT, "This resume is already being processed")
    resume.parse_status = ParseStatus.PENDING
    resume.parse_error = None
    resume.parse_error_code = None
    await session.commit()
    await session.refresh(resume)
    background.add_task(processor.process, resume.id)
    return resume


@router.delete(
    "/resumes/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a resume, its parsed data and the stored file",
    responses={404: {"model": ErrorResponse}},
)
async def delete_resume(resume_id: uuid.UUID, session: SessionDep, storage: StorageDep) -> Response:
    resume = await _get_resume(session, resume_id)
    key = resume.storage_path
    await session.delete(resume)
    await session.commit()
    if key:
        try:
            await asyncio.to_thread(storage.delete, key)
        except ResumeError:
            # The record is gone; an orphaned file is logged for cleanup, not exposed.
            logger.error("could not delete stored resume file", extra={"resume_id": str(resume_id)})
    logger.info("resume deleted", extra={"resume_id": str(resume_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
