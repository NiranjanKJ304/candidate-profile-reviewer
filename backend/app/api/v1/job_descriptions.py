"""Job-description upload, retrieval, review, re-parse and deletion."""

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
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import JDProcessorDep, JDStorageDep, SessionDep, SettingsDep
from app.core.enums import ParseStatus, ResumeErrorCode
from app.core.errors import error_response
from app.models import JobDescription
from app.schemas.common import ErrorResponse
from app.schemas.job_description_api import (
    JobDescriptionDetail,
    JobDescriptionPage,
    JobDescriptionSummary,
    ParsedJobDescriptionResponse,
)
from app.schemas.job_description_parsing import ParsedJobDescription
from app.services.resume.errors import ResumeError, UploadRejectedError
from app.services.resume.validation import JOB_DESCRIPTION_KINDS, validate_upload

logger = logging.getLogger("app.api.job_descriptions")

router = APIRouter(prefix="/job-descriptions", tags=["job-descriptions"])

STALE_PROCESSING_AFTER = timedelta(minutes=10)
MULTIPART_OVERHEAD_BYTES = 64 * 1024

_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Empty file"},
    409: {"model": ErrorResponse, "description": "This file was already uploaded"},
    413: {"model": ErrorResponse, "description": "File too large"},
    415: {
        "model": ErrorResponse,
        "description": "Not PDF/DOCX/TXT, or extension does not match content",
    },
    422: {"model": ErrorResponse, "description": "Corrupted, encrypted, unsafe or too many pages"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}


def _rejected(exc: ResumeError, http_status: int) -> JSONResponse:
    return error_response(http_status, exc.code.value, exc.message)


async def _get(session: SessionDep, jd_id: uuid.UUID) -> JobDescription:
    jd = await session.get(JobDescription, jd_id)
    if jd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job description not found")
    return jd


def _not_parsed(jd: JobDescription) -> JSONResponse:
    return error_response(
        status.HTTP_409_CONFLICT,
        "parse_not_completed",
        f"The job description has not been parsed successfully (status: {jd.parse_status.value})",
    )


@router.post(
    "/upload",
    response_model=JobDescriptionSummary,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a job description (PDF, DOCX or TXT); parsing runs in the background",
    responses=_UPLOAD_ERRORS,
)
async def upload_job_description(
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    storage: JDStorageDep,
    processor: JDProcessorDep,
    file: Annotated[UploadFile, File(description="Job description (.pdf, .docx or .txt)")],
) -> Response | JobDescriptionSummary:
    max_bytes = settings.job_description_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + MULTIPART_OVERHEAD_BYTES:
        exc = UploadRejectedError(
            ResumeErrorCode.FILE_TOO_LARGE,
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit",
        )
        return _rejected(exc, exc.http_status)

    data = await file.read(max_bytes + 1)
    try:
        upload = await asyncio.to_thread(
            validate_upload,
            data,
            file.filename,
            max_bytes=max_bytes,
            max_pages=settings.job_description_max_pages,
            allowed=JOB_DESCRIPTION_KINDS,
        )
    except UploadRejectedError as exc:
        logger.info("job description upload rejected", extra={"error_code": exc.code.value})
        return _rejected(exc, exc.http_status)

    duplicate = await session.scalar(
        select(JobDescription.id).where(JobDescription.sha256 == upload.sha256)
    )
    if duplicate is not None:
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_job_description",
            "This file has already been uploaded",
            details=[{"job_description_id": str(duplicate)}],
        )

    jd_id = uuid.uuid4()
    key = storage.key_for(jd_id, upload.extension)
    try:
        await asyncio.to_thread(storage.save, key, upload.data)
    except ResumeError as exc:
        logger.error("job description storage failed", extra={"jd_id": str(jd_id)})
        return _rejected(exc, status.HTTP_500_INTERNAL_SERVER_ERROR)

    jd = JobDescription(
        id=jd_id,
        original_filename=upload.safe_filename,
        content_type=upload.content_type,
        file_size_bytes=upload.size,
        sha256=upload.sha256,
        storage_path=key,
        page_count=upload.page_count,
        parse_status=ParseStatus.PENDING,
    )
    session.add(jd)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await asyncio.to_thread(storage.delete, key)
        return error_response(
            status.HTTP_409_CONFLICT,
            "duplicate_job_description",
            "This file has already been uploaded",
        )
    except Exception:
        await asyncio.to_thread(storage.delete, key)  # no orphaned file when the DB write fails
        raise
    await session.refresh(jd)

    background.add_task(processor.process, jd.id)
    logger.info(
        "job description uploaded",
        extra={"jd_id": str(jd.id), "kind": upload.kind, "size": upload.size},
    )
    return JobDescriptionSummary.from_model(jd)


@router.get("", response_model=JobDescriptionPage, summary="List job descriptions (newest first)")
async def list_job_descriptions(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobDescriptionPage:
    total = await session.scalar(select(func.count()).select_from(JobDescription)) or 0
    rows = await session.execute(
        select(JobDescription)
        .order_by(JobDescription.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return JobDescriptionPage(
        items=[JobDescriptionSummary.from_model(jd) for jd in rows.scalars()],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{jd_id}",
    response_model=JobDescriptionDetail,
    summary="Get a job description's metadata, status, warnings and errors",
    responses={404: {"model": ErrorResponse}},
)
async def get_job_description(
    jd_id: uuid.UUID, session: SessionDep, response: Response
) -> JobDescriptionDetail:
    jd = await _get(session, jd_id)
    response.headers["Cache-Control"] = "no-store"
    return JobDescriptionDetail.from_model(jd)


@router.get(
    "/{jd_id}/parsed",
    response_model=ParsedJobDescriptionResponse,
    summary="Get the structured job description (reviewed version if present)",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Not parsed yet"},
    },
)
async def get_parsed(
    jd_id: uuid.UUID, session: SessionDep
) -> Response | ParsedJobDescriptionResponse:
    jd = await _get(session, jd_id)
    if jd.parse_status != ParseStatus.COMPLETED or jd.analysis is None:
        return _not_parsed(jd)
    return ParsedJobDescriptionResponse.from_model(jd)


@router.put(
    "/{jd_id}/parsed",
    response_model=ParsedJobDescriptionResponse,
    summary="Save a reviewed (human-corrected) version; the LLM extraction is kept unchanged",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def review_parsed(
    jd_id: uuid.UUID, payload: ParsedJobDescription, session: SessionDep
) -> Response | ParsedJobDescriptionResponse:
    jd = await _get(session, jd_id)
    if jd.parse_status != ParseStatus.COMPLETED or jd.analysis is None:
        return _not_parsed(jd)
    jd.reviewed_data = payload.model_dump(mode="json")
    jd.reviewed_at = datetime.now(UTC)
    if payload.job_title:
        jd.title = payload.job_title
    jd.company = payload.company_name
    await session.commit()
    await session.refresh(jd)
    logger.info("job description reviewed", extra={"jd_id": str(jd_id)})
    return ParsedJobDescriptionResponse.from_model(jd)


@router.delete(
    "/{jd_id}/parsed/review",
    response_model=ParsedJobDescriptionResponse,
    summary="Discard the reviewed version and use the LLM extraction again",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def discard_review(
    jd_id: uuid.UUID, session: SessionDep
) -> Response | ParsedJobDescriptionResponse:
    jd = await _get(session, jd_id)
    if jd.parse_status != ParseStatus.COMPLETED or jd.analysis is None:
        return _not_parsed(jd)
    jd.reviewed_data = None
    jd.reviewed_at = None
    extracted = ParsedJobDescription.model_validate(jd.analysis)
    jd.title = extracted.job_title or jd.title
    jd.company = extracted.company_name
    await session.commit()
    await session.refresh(jd)
    return ParsedJobDescriptionResponse.from_model(jd)


@router.post(
    "/{jd_id}/parse",
    response_model=JobDescriptionSummary,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run parsing (e.g. after configuring the LLM key or a failure)",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def reparse_job_description(
    jd_id: uuid.UUID, background: BackgroundTasks, session: SessionDep, processor: JDProcessorDep
) -> JobDescriptionSummary:
    jd = await _get(session, jd_id)
    busy = jd.parse_status in (ParseStatus.PROCESSING, ParseStatus.PENDING)
    if busy and datetime.now(UTC) - jd.updated_at < STALE_PROCESSING_AFTER:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This job description is already being processed"
        )
    if not jd.storage_path and not jd.extracted_text:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This job description has no file or text to parse"
        )
    jd.parse_status = ParseStatus.PENDING
    jd.parse_error = None
    jd.parse_error_code = None
    await session.commit()
    await session.refresh(jd)
    background.add_task(processor.process, jd.id)
    return JobDescriptionSummary.from_model(jd)


@router.get(
    "/{jd_id}/file",
    summary="Download the original job description file",
    responses={404: {"model": ErrorResponse}},
    response_class=Response,
)
async def download_file(jd_id: uuid.UUID, session: SessionDep, storage: JDStorageDep) -> Response:
    jd = await _get(session, jd_id)
    if not jd.storage_path or not jd.content_type:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The original file is not available")
    try:
        data = await asyncio.to_thread(storage.read, jd.storage_path)
    except ResumeError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "The original file is not available"
        ) from exc
    filename = quote(jd.original_filename or "job-description")
    return Response(
        content=data,
        media_type=jd.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.delete(
    "/{jd_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a job description, its parsed data and the stored file",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Used by evaluations"},
    },
)
async def delete_job_description(
    jd_id: uuid.UUID, session: SessionDep, storage: JDStorageDep
) -> Response:
    jd = await _get(session, jd_id)
    key = jd.storage_path
    await session.delete(jd)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return error_response(
            status.HTTP_409_CONFLICT,
            "job_description_in_use",
            "This job description is referenced by evaluations and cannot be deleted",
        )
    if key:
        try:
            await asyncio.to_thread(storage.delete, key)
        except ResumeError:
            logger.error(
                "could not delete stored job description file", extra={"jd_id": str(jd_id)}
            )
    logger.info("job description deleted", extra={"jd_id": str(jd_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
