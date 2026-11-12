"""Domain enumerations shared by configuration, ORM models and API schemas.

Values are lowercase strings: they are stored as-is in the database (VARCHAR with a
CHECK constraint, not native PostgreSQL enums, so adding a value later is a simple
constraint change) and appear verbatim in API payloads.
"""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    """External developer platforms the system may collect evidence from."""

    GITHUB = "github"
    LEETCODE = "leetcode"
    HACKERRANK = "hackerrank"
    CODEFORCES = "codeforces"
    CODECHEF = "codechef"
    KAGGLE = "kaggle"


class ParseStatus(StrEnum):
    """Lifecycle of resume text extraction (parsing itself arrives in a later phase)."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionMethod(StrEnum):
    """How resume text was obtained."""

    PDF_TEXT = "pdf_text"  # embedded text layer
    PDF_OCR = "pdf_ocr"  # at least one page needed OCR (scanned / image-only)
    DOCX = "docx"
    TXT = "txt"  # plain-text upload (job descriptions)


class ResumeErrorCode(StrEnum):
    """Machine-readable reasons for upload rejection or parsing failure."""

    # Upload validation (returned synchronously, nothing stored)
    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_TYPE = "unsupported_type"
    CONTENT_MISMATCH = "content_mismatch"
    CORRUPTED_FILE = "corrupted_file"
    ENCRYPTED_FILE = "encrypted_file"
    TOO_MANY_PAGES = "too_many_pages"
    UNSAFE_FILE = "unsafe_file"
    # Processing (stored on the resume)
    NO_TEXT = "no_text"
    OCR_UNAVAILABLE = "ocr_unavailable"
    OCR_FAILED = "ocr_failed"
    LLM_NOT_CONFIGURED = "llm_not_configured"
    LLM_AUTH_FAILED = "llm_auth_failed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_ERROR = "llm_error"
    LLM_INVALID_RESPONSE = "llm_invalid_response"
    STORAGE_ERROR = "storage_error"
    INTERNAL_ERROR = "internal_error"


class OwnershipStatus(StrEnum):
    """Whether a developer profile is confirmed to belong to the candidate."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    MISMATCH = "mismatch"


class CollectionStatus(StrEnum):
    """Outcome of collecting public profile data.

    ``UNAVAILABLE`` (profile private, missing, or platform offers no permitted source)
    is deliberately distinct from ``COLLECTED`` with zero activity: a missing profile is
    never evidence of a missing skill.
    """

    NOT_COLLECTED = "not_collected"
    COLLECTED = "collected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
