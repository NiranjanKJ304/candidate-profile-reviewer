"""Upload validation: valid files, corrupted/empty files and security checks."""

from __future__ import annotations

import pytest

from app.core.enums import ResumeErrorCode
from app.services.resume.errors import UploadRejectedError
from app.services.resume.validation import (
    DOCX_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    ValidatedUpload,
    sanitize_filename,
    validate_upload,
)
from tests import resume_fixtures as fx

MAX = 10 * 1024 * 1024


def _validate(
    data: bytes, name: str = "resume.pdf", max_pages: int = 30, max_bytes: int = MAX
) -> ValidatedUpload:
    return validate_upload(data, name, max_bytes=max_bytes, max_pages=max_pages)


def _rejected(data: bytes, name: str, code: ResumeErrorCode, **kw: int) -> UploadRejectedError:
    with pytest.raises(UploadRejectedError) as info:
        _validate(data, name, **kw)
    assert info.value.code is code, info.value.message
    return info.value


class TestValidFiles:
    def test_valid_pdf(self) -> None:
        data = fx.text_pdf()
        result = _validate(data, "Jordan CV.pdf")
        assert result.kind == "pdf"
        assert result.content_type == PDF_CONTENT_TYPE
        assert result.extension == ".pdf"
        assert result.page_count == 1
        assert result.size == len(data)
        assert len(result.sha256) == 64
        assert result.safe_filename == "Jordan CV.pdf"

    def test_valid_docx(self) -> None:
        result = _validate(fx.make_docx(), "resume.DOCX")
        assert result.kind == "docx"
        assert result.content_type == DOCX_CONTENT_TYPE
        assert result.extension == ".docx"
        assert result.page_count is None

    def test_scanned_pdf_is_a_valid_upload(self) -> None:
        assert _validate(fx.scanned_pdf()).kind == "pdf"

    def test_same_bytes_same_hash(self) -> None:
        data = fx.text_pdf()
        assert _validate(data).sha256 == _validate(data, "other-name.pdf").sha256


class TestEmptyAndCorrupted:
    def test_empty_file(self) -> None:
        err = _rejected(b"", "resume.pdf", ResumeErrorCode.EMPTY_FILE)
        assert err.http_status == 400

    def test_random_bytes_named_pdf(self) -> None:
        _rejected(b"\x00\x01garbage" * 100, "resume.pdf", ResumeErrorCode.CORRUPTED_FILE)

    def test_truncated_pdf(self) -> None:
        data = fx.text_pdf()
        err = _rejected(data[: len(data) // 3], "resume.pdf", ResumeErrorCode.CORRUPTED_FILE)
        assert err.http_status == 422

    def test_pdf_header_only(self) -> None:
        _rejected(b"%PDF-1.7\n%%EOF", "resume.pdf", ResumeErrorCode.CORRUPTED_FILE)

    def test_docx_missing_document_part(self) -> None:
        _rejected(fx.docx_without_document_xml(), "resume.docx", ResumeErrorCode.CONTENT_MISMATCH)

    def test_corrupted_zip_named_docx(self) -> None:
        data = fx.make_docx()
        _rejected(data[:200], "resume.docx", ResumeErrorCode.CORRUPTED_FILE)

    def test_random_bytes_named_docx(self) -> None:
        _rejected(b"hello world" * 50, "resume.docx", ResumeErrorCode.CONTENT_MISMATCH)


class TestTypeAndSize:
    @pytest.mark.parametrize(
        "name", ["resume.txt", "resume.doc", "resume.exe", "resume", "cv.pdf.exe"]
    )
    def test_unsupported_extension(self, name: str) -> None:
        err = _rejected(fx.text_pdf(), name, ResumeErrorCode.UNSUPPORTED_TYPE)
        assert err.http_status == 415

    def test_pdf_content_with_docx_extension(self) -> None:
        _rejected(fx.text_pdf(), "resume.docx", ResumeErrorCode.CONTENT_MISMATCH)

    def test_docx_content_with_pdf_extension(self) -> None:
        _rejected(fx.make_docx(), "resume.pdf", ResumeErrorCode.CONTENT_MISMATCH)

    def test_plain_zip_named_docx(self) -> None:
        _rejected(fx.plain_zip(), "resume.docx", ResumeErrorCode.CONTENT_MISMATCH)

    def test_file_too_large(self) -> None:
        err = _rejected(fx.text_pdf(), "resume.pdf", ResumeErrorCode.FILE_TOO_LARGE, max_bytes=1024)
        assert err.http_status == 413

    def test_too_many_pages(self) -> None:
        _rejected(fx.text_pdf(pages=4), "resume.pdf", ResumeErrorCode.TOO_MANY_PAGES, max_pages=3)

    def test_page_limit_is_inclusive(self) -> None:
        assert _validate(fx.text_pdf(pages=3), max_pages=3).page_count == 3


class TestSecurity:
    def test_encrypted_pdf_rejected(self) -> None:
        _rejected(fx.encrypted_pdf(), "resume.pdf", ResumeErrorCode.ENCRYPTED_FILE)

    def test_macro_enabled_document_rejected(self) -> None:
        _rejected(fx.macro_docm_as_docx(), "resume.docx", ResumeErrorCode.UNSAFE_FILE)

    def test_zip_bomb_rejected(self) -> None:
        _rejected(fx.zip_bomb_docx(), "resume.docx", ResumeErrorCode.UNSAFE_FILE)

    def test_error_messages_do_not_echo_file_content(self) -> None:
        err = _rejected(b"<script>alert(1)</script>", "resume.pdf", ResumeErrorCode.CORRUPTED_FILE)
        assert "<script>" not in err.message


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("../../etc/passwd.pdf", "passwd.pdf"),
            ("..\\..\\windows\\system32\\cv.docx", "cv.docx"),
            ("C:\\Users\\x\\My CV.pdf", "My CV.pdf"),
            ("cv\x00\x1f.pdf", "cv.pdf"),
            ("", "resume"),
            (None, "resume"),
            ("...", "resume"),
            ("  spaced.pdf  ", "spaced.pdf"),
        ],
    )
    def test_sanitize(self, raw: str | None, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_long_names_are_truncated_keeping_extension(self) -> None:
        name = sanitize_filename("a" * 400 + ".pdf")
        assert len(name) == 255
        assert name.endswith(".pdf")

    def test_traversal_name_still_validated_by_content(self) -> None:
        result = _validate(fx.text_pdf(), "../../secret/resume.pdf")
        assert result.safe_filename == "resume.pdf"


@pytest.mark.parametrize("fraction", [0.9, 0.66])
def test_partially_truncated_pdf_is_rejected_not_silently_repaired(fraction: float) -> None:
    data = fx.text_pdf()
    _rejected(data[: int(len(data) * fraction)], "resume.pdf", ResumeErrorCode.CORRUPTED_FILE)


def test_trailing_bytes_after_eof_are_tolerated() -> None:
    assert _validate(fx.text_pdf() + b"\n\n").kind == "pdf"
