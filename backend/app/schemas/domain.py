"""API schemas for the domain models.

``*Create`` schemas validate untrusted input (strip whitespace, bound lengths,
reject malformed values). ``*Read`` schemas serialise ORM objects
(``from_attributes``). No endpoints use them yet in Phase 0; they define the
contract the next phases build on.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
)

from app.core.enums import (
    CollectionStatus,
    EvaluationStatus,
    ExtractionMethod,
    OwnershipStatus,
    ParseStatus,
    Platform,
    ResumeErrorCode,
)
from app.schemas.resume_parsing import ParsedResume

# Pragmatic e-mail check (one @, no spaces, dotted domain). Full RFC validation and
# deliverability checks are out of scope; addresses are normalised to lower case.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
# Usernames on all supported platforms fit this set; it also blocks path/URL injection.
_USERNAME_RE = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9_])?$"


def _normalise_email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not value:
        return None
    if len(value) > 320 or not _EMAIL_RE.fullmatch(value):
        raise ValueError("invalid e-mail address")
    return value


def _url_to_str(value: HttpUrl | None) -> str | None:
    return None if value is None else str(value)


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OptionalText = Annotated[str | None, StringConstraints(strip_whitespace=True)]
Email = Annotated[str | None, AfterValidator(_normalise_email)]
# Accepts only absolute http(s) URLs; stored/serialised as a plain string.
OptionalUrl = Annotated[HttpUrl | None, AfterValidator(_url_to_str)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- Candidate
class CandidateCreate(_Input):
    full_name: Annotated[NonBlank, Field(max_length=200)]
    email: Email = None  # length (<= 320) is enforced by the validator
    phone: Annotated[OptionalText, Field(max_length=50)] = None
    location: Annotated[OptionalText, Field(max_length=200)] = None

    @field_validator("phone", "location")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None


class CandidateRead(_Output):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    location: str | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- Resume
class ResumeRead(_Output):
    """Resume metadata and processing status (list views, upload response)."""

    id: uuid.UUID
    candidate_id: uuid.UUID
    original_filename: str
    content_type: str
    file_size_bytes: int
    sha256: str
    parse_status: ParseStatus
    parse_error_code: ResumeErrorCode | None = None
    parse_error: str | None = None
    extraction_method: ExtractionMethod | None = None
    page_count: int | None = None
    parsed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResumeDetail(ResumeRead):
    """Full resume record including the validated structured data."""

    parsed_data: ParsedResume | None = None
    parse_warnings: list[str] = Field(default_factory=list)
    parser_model: str | None = None
    text_char_count: int | None = Field(
        default=None, description="Length of the extracted text; null if not extracted yet."
    )

    @classmethod
    def from_model(cls, resume: Any) -> ResumeDetail:
        base = ResumeRead.model_validate(resume).model_dump()
        text = getattr(resume, "extracted_text", None)
        return cls(
            **base,
            parsed_data=(
                ParsedResume.model_validate(resume.parsed_data) if resume.parsed_data else None
            ),
            parse_warnings=list(resume.parse_warnings or []),
            parser_model=resume.parser_model,
            text_char_count=len(text) if text is not None else None,
        )


# --------------------------------------------------------------------------- JobDescription
class JobDescriptionCreate(_Input):
    title: Annotated[NonBlank, Field(max_length=300)]
    company: Annotated[OptionalText, Field(max_length=200)] = None
    description_text: Annotated[NonBlank, Field(max_length=100_000)]
    source_url: OptionalUrl = None

    @field_validator("company")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None


class JobDescriptionRead(_Output):
    id: uuid.UUID
    title: str
    company: str | None
    description_text: str
    source_url: str | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- DeveloperProfile
class DeveloperProfileCreate(_Input):
    platform: Platform
    username: Annotated[str, StringConstraints(strip_whitespace=True, pattern=_USERNAME_RE)]
    profile_url: OptionalUrl = None


class DeveloperProfileRead(_Output):
    id: uuid.UUID
    candidate_id: uuid.UUID
    platform: Platform
    username: str
    profile_url: str | None
    ownership_status: OwnershipStatus
    collection_status: CollectionStatus
    source_url: str | None
    collected_at: datetime | None


# --------------------------------------------------------------------------- Evaluation
class EvaluationRead(_Output):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_description_id: uuid.UUID
    resume_id: uuid.UUID | None
    status: EvaluationStatus
    overall_score: Annotated[Decimal | None, Field(ge=0, le=100)] = Field(
        description="Null until scored; never conflated with 0."
    )
    result: dict[str, Any] | None
    model_name: str | None
    completed_at: datetime | None
    created_at: datetime
