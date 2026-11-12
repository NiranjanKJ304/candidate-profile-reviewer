"""Text extraction: PDF text layer, scanned PDF with OCR, DOCX, failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.enums import ExtractionMethod, ResumeErrorCode
from app.services.resume.errors import ProcessingError
from app.services.resume.extraction import (
    OcrConfig,
    extract_docx,
    extract_pdf,
    extract_text,
    normalize_text,
    ocr_available,
    resolve_tessdata,
)
from tests import resume_fixtures as fx

OCR_ON = OcrConfig(enabled=True, tessdata_dir=None, language="eng", dpi=300)
OCR_OFF = OcrConfig(enabled=False, tessdata_dir=None, language="eng", dpi=300)
OCR_MISSING = OcrConfig(
    enabled=True, tessdata_dir=Path("/nonexistent/tessdata"), language="xxx", dpi=300
)

needs_ocr = pytest.mark.skipif(
    not ocr_available(OCR_ON), reason="Tesseract language data not installed"
)


class TestPdf:
    def test_text_pdf(self) -> None:
        result = extract_pdf(fx.text_pdf(), OCR_ON)
        assert result.method is ExtractionMethod.PDF_TEXT
        assert result.page_count == 1
        assert result.ocr_pages == []
        assert "jordan.sample@example.org" in result.text
        assert "FastAPI" in result.text
        assert result.warnings == []

    def test_multi_page_pdf_keeps_all_pages(self) -> None:
        result = extract_pdf(fx.text_pdf(pages=3), OCR_ON)
        assert result.page_count == 3
        assert result.text.count("Jordan Sample") == 3

    @needs_ocr
    def test_scanned_pdf_uses_ocr(self) -> None:
        result = extract_pdf(fx.scanned_pdf(), OCR_ON)
        assert result.method is ExtractionMethod.PDF_OCR
        assert result.ocr_pages == [1]
        assert "Jordan Sample" in result.text
        assert "jordan.sample@example.org" in result.text
        assert any("OCR was used" in w for w in result.warnings)

    def test_scanned_pdf_with_ocr_disabled(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_pdf(fx.scanned_pdf(), OCR_OFF)
        assert info.value.code is ResumeErrorCode.OCR_UNAVAILABLE
        assert "disabled" in info.value.message

    def test_scanned_pdf_with_ocr_engine_missing(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_pdf(fx.scanned_pdf(), OCR_MISSING)
        assert info.value.code is ResumeErrorCode.OCR_UNAVAILABLE

    @needs_ocr
    def test_blank_pdf_has_no_text_even_after_ocr(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_pdf(fx.blank_pdf(), OCR_ON)
        assert info.value.code is ResumeErrorCode.NO_TEXT

    def test_corrupted_bytes(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_pdf(b"not a pdf at all", OCR_ON)
        assert info.value.code is ResumeErrorCode.CORRUPTED_FILE


class TestDocx:
    def test_docx_paragraphs(self) -> None:
        result = extract_docx(fx.make_docx())
        assert result.method is ExtractionMethod.DOCX
        assert result.page_count is None
        assert "Software Engineer, Example Corp" in result.text

    def test_docx_header_and_table_are_included(self) -> None:
        data = fx.make_docx(
            ["Experience section text for the resume"],
            header="Jordan Sample - jordan.sample@example.org",
            table=[["Skill", "Years"], ["Python", "5"], ["PostgreSQL", "3"]],
        )
        text = extract_docx(data).text
        assert text.startswith("Jordan Sample - jordan.sample@example.org")
        assert "Python | 5" in text
        assert "PostgreSQL | 3" in text

    def test_empty_docx(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_docx(fx.make_docx([]))
        assert info.value.code is ResumeErrorCode.NO_TEXT

    def test_corrupted_docx(self) -> None:
        with pytest.raises(ProcessingError) as info:
            extract_docx(b"PK\x03\x04broken")
        assert info.value.code is ResumeErrorCode.CORRUPTED_FILE


def test_extract_text_dispatches_by_kind() -> None:
    assert extract_text(fx.text_pdf(), "pdf", OCR_OFF).method is ExtractionMethod.PDF_TEXT
    assert extract_text(fx.make_docx(), "docx", OCR_OFF).method is ExtractionMethod.DOCX


class TestNormalize:
    def test_line_endings_controls_and_blank_runs(self) -> None:
        raw = "a\r\nb\rc\x00\x07d  \n\n\n\n\ne" + chr(0xA0) + "f"
        assert normalize_text(raw) == "a\nb\ncd\n\ne f"

    def test_unicode_is_nfc(self) -> None:
        assert normalize_text("Jose" + chr(0x301)) == "Jos" + chr(0xE9)  # combining -> composed


class TestOcrConfiguration:
    def test_configured_dir_wins(self, tmp_path: Path) -> None:
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        assert resolve_tessdata(tmp_path) == tmp_path

    def test_unavailable_language(self, tmp_path: Path) -> None:
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        cfg = OcrConfig(enabled=True, tessdata_dir=tmp_path, language="deu", dpi=300)
        assert ocr_available(cfg) is False

    def test_disabled(self) -> None:
        assert ocr_available(OCR_OFF) is False
