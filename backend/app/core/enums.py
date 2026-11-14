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


class SyncStatus(StrEnum):
    """Lifecycle of one developer-profile synchronisation run."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    # The platform offers no permitted data source (e.g. LeetCode): nothing will sync.
    NOT_APPLICABLE = "not_applicable"


class CollectionErrorCode(StrEnum):
    """Why a developer-profile synchronisation failed (stored on the profile)."""

    INVALID_PROFILE = "invalid_profile"  # account does not exist / not accessible
    NOT_A_USER = "not_a_user"  # e.g. a GitHub organisation, not a person
    RATE_LIMITED = "rate_limited"
    PERMISSION_DENIED = "permission_denied"
    AUTH_FAILED = "auth_failed"  # configured token rejected
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    API_ERROR = "api_error"
    INTEGRATION_DISABLED = "integration_disabled"
    INTERNAL_ERROR = "internal_error"


class AvailabilityStatus(StrEnum):
    """Outcome for one collected data category (profile, repositories, ...).

    Keeps "nothing there" (``empty``) distinct from "could not be obtained"
    (``unavailable``, ``rate_limited``, ...). Neither is evidence of a missing skill.
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"  # not offered by the API / not public
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    ERROR = "error"
    NOT_COLLECTED = "not_collected"  # skipped (limits, earlier failure)


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationErrorCode(StrEnum):
    """Why an evaluation failed (stored on the evaluation). LLM codes match ResumeErrorCode."""

    SOURCE_MISSING = "source_missing"  # candidate, JD or resume deleted meanwhile
    JD_NOT_PARSED = "jd_not_parsed"
    JD_HAS_NO_REQUIREMENTS = "jd_has_no_requirements"
    NO_EVIDENCE = "no_evidence"  # no parsed resume and no platform evidence
    LLM_NOT_CONFIGURED = "llm_not_configured"
    LLM_AUTH_FAILED = "llm_auth_failed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_ERROR = "llm_error"
    LLM_INVALID_RESPONSE = "llm_invalid_response"
    INTERNAL_ERROR = "internal_error"


class Verdict(StrEnum):
    """LLM verdict for one job requirement."""

    MET = "met"
    PARTIALLY_MET = "partially_met"
    NOT_MET = "not_met"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # never treated as "not met"


class EvidenceTrust(StrEnum):
    """How much an evidence item can be relied on."""

    PLATFORM_COLLECTED = "platform_collected"  # official platform API (ownership unverified)
    RESUME_CLAIM = "resume_claim"  # stated in the candidate's resume
    SELF_REPORTED = "self_reported"  # manually entered, not verifiable (e.g. LeetCode)
