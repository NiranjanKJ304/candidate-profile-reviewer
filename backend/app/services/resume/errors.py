"""Typed errors for the resume pipeline."""

from __future__ import annotations

from app.core.enums import ResumeErrorCode

# HTTP status for each upload-validation error (processing errors are stored instead).
UPLOAD_ERROR_STATUS: dict[ResumeErrorCode, int] = {
    ResumeErrorCode.EMPTY_FILE: 400,
    ResumeErrorCode.FILE_TOO_LARGE: 413,
    ResumeErrorCode.UNSUPPORTED_TYPE: 415,
    ResumeErrorCode.CONTENT_MISMATCH: 415,
    ResumeErrorCode.CORRUPTED_FILE: 422,
    ResumeErrorCode.ENCRYPTED_FILE: 422,
    ResumeErrorCode.TOO_MANY_PAGES: 422,
    ResumeErrorCode.UNSAFE_FILE: 422,
}


class ResumeError(Exception):
    """An expected, user-explainable failure with a machine-readable code."""

    def __init__(self, code: ResumeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UploadRejectedError(ResumeError):
    """The uploaded file failed validation; nothing was stored."""

    @property
    def http_status(self) -> int:
        return UPLOAD_ERROR_STATUS.get(self.code, 400)


class ProcessingError(ResumeError):
    """Text extraction or structuring failed; recorded on the resume row."""
