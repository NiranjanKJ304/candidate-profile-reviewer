"""JD upload validation and extraction: PDF, DOCX, TXT (encodings), OCR, failures."""

from __future__ import annotations

import pytest

from app.core.enums import ExtractionMethod, ResumeErrorCode
from app.services.resume.errors import ProcessingError, UploadRejectedError
from app.services.resume.extraction import OcrConfig, extract_text, extract_txt, ocr_available
from app.services.resume.validation import (
    JOB_DESCRIPTION_KINDS,
    TXT_CONTENT_TYPE,
    ValidatedUpload,
    decode_text,
    validate_upload,
)
from tests import jd_fixtures as jf
from tests import resume_fixtures as rf

OCR = OcrConfig(enabled=True, tessdata_dir=None, language="eng", dpi=300)


def _jd(data: bytes, name: str, max_bytes: int = 5 * 1024 * 1024) -> ValidatedUpload:
    return validate_upload(
        data, name, max_bytes=max_bytes, max_pages=20, allowed=JOB_DESCRIPTION_KINDS
    )


def _rejected(data: bytes, name: str, code: ResumeErrorCode) -> None:
    with pytest.raises(UploadRejectedError) as info:
        _jd(data, name)
    assert info.value.code is code, info.value.message


class TestValidation:
    @pytest.mark.parametrize(
        ("data", "name", "kind"),
        [
            (jf.jd_pdf(), "jd.pdf", "pdf"),
            (jf.jd_docx(), "jd.docx", "docx"),
            (jf.jd_txt(), "jd.txt", "txt"),
            (jf.jd_txt(encoding="utf-16"), "jd.TXT", "txt"),
            (b"\xef\xbb\xbf" + jf.jd_txt(), "jd.txt", "txt"),  # UTF-8 with BOM
            (jf.jd_scanned_pdf(), "scan.pdf", "pdf"),
        ],
    )
    def test_valid_documents(self, data: bytes, name: str, kind: str) -> None:
        result = _jd(data, name)
        assert result.kind == kind
        if kind == "txt":
            assert result.content_type == TXT_CONTENT_TYPE

    def test_resume_uploads_still_reject_txt(self) -> None:
        with pytest.raises(UploadRejectedError) as info:
            validate_upload(jf.jd_txt(), "cv.txt", max_bytes=10**7, max_pages=30)
        assert info.value.code is ResumeErrorCode.UNSUPPORTED_TYPE
        assert "text" not in info.value.message

    def test_jd_error_message_lists_three_formats(self) -> None:
        with pytest.raises(UploadRejectedError) as info:
            _jd(b"x", "jd.rtf")
        assert (
            info.value.message == "Only PDF (.pdf), Word (.docx) and text (.txt) files are accepted"
        )

    @pytest.mark.parametrize(
        ("data", "name", "code"),
        [
            (b"", "jd.txt", ResumeErrorCode.EMPTY_FILE),
            (b"   \n\t  ", "jd.txt", ResumeErrorCode.EMPTY_FILE),
            (b"MZ\x90\x00\x03\x00\x00\x00binary", "jd.txt", ResumeErrorCode.CONTENT_MISMATCH),
            (bytes(range(1, 31)) * 20, "jd.txt", ResumeErrorCode.CONTENT_MISMATCH),
            (rf.text_pdf(), "jd.txt", ResumeErrorCode.CONTENT_MISMATCH),
            (rf.make_docx(), "jd.txt", ResumeErrorCode.CONTENT_MISMATCH),
            (jf.jd_txt(), "jd.pdf", ResumeErrorCode.CORRUPTED_FILE),
            (jf.jd_txt(), "jd.docx", ResumeErrorCode.CONTENT_MISMATCH),
            (rf.encrypted_pdf(), "jd.pdf", ResumeErrorCode.ENCRYPTED_FILE),
            (rf.macro_docm_as_docx(), "jd.docx", ResumeErrorCode.UNSAFE_FILE),
            (rf.zip_bomb_docx(), "jd.docx", ResumeErrorCode.UNSAFE_FILE),
            (rf.text_pdf()[:300], "jd.pdf", ResumeErrorCode.CORRUPTED_FILE),
            (b"\x81\x8d\x8f\x90\x9d" * 10, "jd.txt", ResumeErrorCode.CONTENT_MISMATCH),
        ],
    )
    def test_rejections(self, data: bytes, name: str, code: ResumeErrorCode) -> None:
        _rejected(data, name, code)

    def test_size_limit(self) -> None:
        with pytest.raises(UploadRejectedError) as info:
            _jd(jf.jd_txt() * 100, "jd.txt", max_bytes=1024)
        assert info.value.code is ResumeErrorCode.FILE_TOO_LARGE


class TestDecodeText:
    def test_utf8(self) -> None:
        assert decode_text(b"Caf" + bytes([0xC3, 0xA9])) == "Caf" + chr(0xE9)

    def test_cp1252_fallback(self) -> None:
        assert decode_text(bytes([0x43, 0x61, 0x66, 0xE9])) == "Caf" + chr(0xE9)

    def test_utf16_bom(self) -> None:
        assert decode_text("Python".encode("utf-16")) == "Python"


class TestExtraction:
    def test_txt(self) -> None:
        result = extract_txt(jf.jd_txt(encoding="utf-16"))
        assert result.method is ExtractionMethod.TXT
        assert result.page_count is None
        assert "Nice to have" in result.text

    def test_txt_too_short(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_txt(b"Engineer")
        assert info.value.code is ResumeErrorCode.NO_TEXT

    def test_dispatch(self) -> None:
        assert extract_text(jf.jd_txt(), "txt", OCR).method is ExtractionMethod.TXT
        assert extract_text(jf.jd_docx(), "docx", OCR).method is ExtractionMethod.DOCX
        assert extract_text(jf.jd_pdf(), "pdf", OCR).method is ExtractionMethod.PDF_TEXT

    @pytest.mark.skipif(not ocr_available(OCR), reason="Tesseract language data not installed")
    def test_scanned_jd_ocr(self) -> None:
        result = extract_text(jf.jd_scanned_pdf(), "pdf", OCR)
        assert result.method is ExtractionMethod.PDF_OCR
        assert "Senior Backend Engineer" in result.text
        assert "PostgreSQL" in result.text
