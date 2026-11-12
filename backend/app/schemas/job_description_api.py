"""HTTP response schemas for the job-description endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ExtractionMethod, ParseStatus, ResumeErrorCode
from app.schemas.job_description_parsing import ParsedJobDescription, TechnologySummary


class JobDescriptionSummary(BaseModel):
    """List item and upload response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    company: str | None
    original_filename: str | None
    content_type: str | None
    file_size_bytes: int | None
    parse_status: ParseStatus
    parse_error_code: ResumeErrorCode | None = None
    extraction_method: ExtractionMethod | None = None
    has_review: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, jd: Any) -> JobDescriptionSummary:
        base = cls.model_validate(jd)
        base.has_review = jd.reviewed_data is not None
        return base


class JobDescriptionDetail(JobDescriptionSummary):
    sha256: str | None
    page_count: int | None
    parse_error: str | None
    parse_warnings: list[str] = Field(default_factory=list)
    parser_model: str | None
    parsed_at: datetime | None
    reviewed_at: datetime | None
    text_char_count: int | None = Field(
        default=None, description="Length of the extracted text; null if not extracted yet."
    )

    @classmethod
    def from_model(cls, jd: Any) -> JobDescriptionDetail:
        summary = JobDescriptionSummary.from_model(jd).model_dump()
        text = jd.extracted_text
        return cls(
            **summary,
            sha256=jd.sha256,
            page_count=jd.page_count,
            parse_error=jd.parse_error,
            parse_warnings=list(jd.parse_warnings or []),
            parser_model=jd.parser_model,
            parsed_at=jd.parsed_at,
            reviewed_at=jd.reviewed_at,
            text_char_count=len(text) if text is not None else None,
        )


class JobDescriptionPage(BaseModel):
    items: list[JobDescriptionSummary]
    total: int
    limit: int
    offset: int


class ParsedJobDescriptionResponse(BaseModel):
    """Effective structured JD plus provenance.

    ``data`` is the reviewed version when one exists, otherwise the LLM extraction.
    ``extracted`` is always the untouched LLM extraction, so corrections can be compared.
    """

    jd_id: uuid.UUID
    source: Literal["extracted", "reviewed"]
    data: ParsedJobDescription
    extracted: ParsedJobDescription
    technology_summary: TechnologySummary
    parser_model: str | None
    parsed_at: datetime | None
    reviewed_at: datetime | None
    warnings: list[str]

    @classmethod
    def from_model(cls, jd: Any) -> ParsedJobDescriptionResponse:
        extracted = ParsedJobDescription.model_validate(jd.analysis)
        reviewed = (
            ParsedJobDescription.model_validate(jd.reviewed_data) if jd.reviewed_data else None
        )
        effective = reviewed or extracted
        return cls(
            jd_id=jd.id,
            source="reviewed" if reviewed else "extracted",
            data=effective,
            extracted=extracted,
            technology_summary=TechnologySummary.from_parsed(effective),
            parser_model=jd.parser_model,
            parsed_at=jd.parsed_at,
            reviewed_at=jd.reviewed_at,
            warnings=list(jd.parse_warnings or []),
        )
