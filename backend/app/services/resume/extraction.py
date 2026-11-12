"""Text extraction from validated PDF/DOCX resumes, with OCR fallback.

PDF: PyMuPDF reads the embedded text layer page by page. A page is treated as
scanned (image-only) when it has almost no usable text - or when its text is
mostly U+FFFD replacement characters (fonts without a Unicode mapping). Only
those pages are OCR'd, using MuPDF's built-in Tesseract and the installed
Tesseract language data.

DOCX: python-docx reads headers (contact details often live there), body
paragraphs and tables in document order, and footers.

These functions are CPU-bound and synchronous; callers run them in a thread.
"""

from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.core.enums import ExtractionMethod, ResumeErrorCode
from app.services.resume.errors import ProcessingError
from app.services.resume.validation import FileKind, decode_text

logger = logging.getLogger("app.resume.extraction")

MIN_PAGE_CHARS = 30  # non-whitespace characters below which a page counts as scanned
MAX_REPLACEMENT_RATIO = 0.3
MIN_DOCUMENT_CHARS = 20
NBSP = chr(0xA0)  # non-breaking space
CR, LF = chr(13), chr(10)
REPLACEMENT_CHAR = chr(0xFFFD)  # emitted for glyphs without a Unicode mapping


@dataclass(frozen=True)
class OcrConfig:
    enabled: bool
    tessdata_dir: Path | None
    language: str
    dpi: int


@dataclass
class ExtractionResult:
    text: str
    method: ExtractionMethod
    page_count: int | None
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def resolve_tessdata(configured: Path | None) -> Path | None:
    """Find the Tesseract language-data folder, or ``None`` if OCR is unavailable."""
    candidates: list[Path] = []
    if configured:
        candidates.append(configured)
    if env := os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(env))
    for base in (
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
    ):
        if base:
            candidates.append(Path(base) / "Programs" / "Tesseract-OCR" / "tessdata")
            candidates.append(Path(base) / "Tesseract-OCR" / "tessdata")
    candidates += [Path("/usr/share/tesseract-ocr/5/tessdata"), Path("/usr/share/tessdata")]
    for path in candidates:
        if path.is_dir() and any(path.glob("*.traineddata")):
            return path
    return None


def ocr_available(config: OcrConfig) -> bool:
    if not config.enabled:
        return False
    tessdata = resolve_tessdata(config.tessdata_dir)
    if tessdata is None:
        return False
    return all((tessdata / f"{lang}.traineddata").is_file() for lang in config.language.split("+"))


_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace(CR + LF, LF).replace(CR, LF).replace(NBSP, " ")
    text = _CTRL.sub("", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def _meaningful_chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace() and ch != REPLACEMENT_CHAR)


def _page_needs_ocr(text: str) -> bool:
    stripped = [ch for ch in text if not ch.isspace()]
    if len(stripped) < MIN_PAGE_CHARS:
        return True
    replacement = sum(1 for ch in stripped if ch == REPLACEMENT_CHAR)
    return replacement / len(stripped) > MAX_REPLACEMENT_RATIO


def _ocr_page(page: pymupdf.Page, config: OcrConfig, tessdata: Path) -> str:
    textpage = page.get_textpage_ocr(
        language=config.language, dpi=config.dpi, full=True, tessdata=str(tessdata)
    )
    return str(page.get_text("text", textpage=textpage))


def extract_pdf(data: bytes, ocr: OcrConfig) -> ExtractionResult:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ProcessingError(
            ResumeErrorCode.CORRUPTED_FILE, "The PDF could not be opened"
        ) from exc

    warnings: list[str] = []
    ocr_pages: list[int] = []
    texts: list[str] = []
    tessdata = resolve_tessdata(ocr.tessdata_dir) if ocr_available(ocr) else None
    try:
        page_count = int(doc.page_count)
        for index in range(page_count):
            page = doc.load_page(index)
            text = str(page.get_text("text", sort=True))
            if _page_needs_ocr(text):
                if tessdata is None:
                    texts.append(text)
                    continue
                try:
                    ocr_text = _ocr_page(page, ocr, tessdata)
                except Exception as exc:
                    logger.warning(
                        "ocr failed on page",
                        extra={"page": index + 1, "error_type": type(exc).__name__},
                    )
                    raise ProcessingError(
                        ResumeErrorCode.OCR_FAILED, f"OCR failed on page {index + 1}"
                    ) from exc
                ocr_pages.append(index + 1)
                # Keep whichever version carries more real text.
                texts.append(
                    ocr_text if _meaningful_chars(ocr_text) >= _meaningful_chars(text) else text
                )
            else:
                texts.append(text)
    finally:
        doc.close()

    full_text = normalize_text("\n\n".join(texts))
    if _meaningful_chars(full_text) < MIN_DOCUMENT_CHARS:
        if tessdata is None:
            reason = "OCR is disabled" if not ocr.enabled else "the OCR engine is not installed"
            raise ProcessingError(
                ResumeErrorCode.OCR_UNAVAILABLE,
                f"This looks like a scanned PDF with no text layer, but {reason}",
            )
        raise ProcessingError(ResumeErrorCode.NO_TEXT, "No readable text was found in the PDF")

    if tessdata is None and any(_page_needs_ocr(t) for t in texts):
        warnings.append(
            "Some pages appear to be scanned but OCR is unavailable; their text is missing"
        )
    if ocr_pages:
        warnings.append(
            f"OCR was used for page(s) {', '.join(map(str, ocr_pages))}; "
            "check for recognition errors"
        )

    return ExtractionResult(
        text=full_text,
        method=ExtractionMethod.PDF_OCR if ocr_pages else ExtractionMethod.PDF_TEXT,
        page_count=page_count,
        ocr_pages=ocr_pages,
        warnings=warnings,
    )


def extract_docx(data: bytes) -> ExtractionResult:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ProcessingError(
            ResumeErrorCode.CORRUPTED_FILE, "The Word file could not be opened"
        ) from exc

    def table_lines(table: Table) -> list[str]:
        lines: list[str] = []
        for row in table.rows:
            cells: list[str] = []
            for cell in row.cells:
                value = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
                if value and (not cells or cells[-1] != value):  # merged cells repeat
                    cells.append(value)
            if cells:
                lines.append(" | ".join(cells))
        return lines

    parts: list[str] = []
    seen_headers: set[str] = set()
    for section in document.sections:
        for header in (section.header, section.first_page_header):
            text = "\n".join(p.text for p in header.paragraphs if p.text.strip())
            if text and text not in seen_headers:
                seen_headers.add(text)
                parts.append(text)

    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                parts.append(block.text)
        elif isinstance(block, Table):
            parts.extend(table_lines(block))

    for section in document.sections:
        text = "\n".join(p.text for p in section.footer.paragraphs if p.text.strip())
        if text and text not in seen_headers:
            seen_headers.add(text)
            parts.append(text)

    full_text = normalize_text("\n".join(parts))
    if _meaningful_chars(full_text) < MIN_DOCUMENT_CHARS:
        raise ProcessingError(
            ResumeErrorCode.NO_TEXT, "No readable text was found in the Word file"
        )
    return ExtractionResult(text=full_text, method=ExtractionMethod.DOCX, page_count=None)


def extract_txt(data: bytes) -> ExtractionResult:
    try:
        raw = decode_text(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProcessingError(
            ResumeErrorCode.CORRUPTED_FILE, "The text file could not be decoded"
        ) from exc
    full_text = normalize_text(raw)
    if _meaningful_chars(full_text) < MIN_DOCUMENT_CHARS:
        raise ProcessingError(ResumeErrorCode.NO_TEXT, "The text file contains too little text")
    return ExtractionResult(text=full_text, method=ExtractionMethod.TXT, page_count=None)


def extract_text(data: bytes, kind: FileKind, ocr: OcrConfig) -> ExtractionResult:
    if kind == "pdf":
        return extract_pdf(data, ocr)
    if kind == "txt":
        return extract_txt(data)
    return extract_docx(data)
