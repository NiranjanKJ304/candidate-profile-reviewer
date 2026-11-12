"""Upload validation: type, size and content checks for PDF and DOCX resumes.

Nothing about the upload is trusted: the file type is decided from the bytes
(magic number + internal structure), never from the extension or the
client-supplied Content-Type, and the extension must agree with the content.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

import pymupdf

from app.core.enums import ResumeErrorCode
from app.services.resume.errors import UploadRejectedError

FileKind = Literal["pdf", "docx", "txt"]

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT_CONTENT_TYPE = "text/plain; charset=utf-8"
CONTENT_TYPES: dict[FileKind, str] = {
    "pdf": PDF_CONTENT_TYPE,
    "docx": DOCX_CONTENT_TYPE,
    "txt": TXT_CONTENT_TYPE,
}
EXTENSIONS: dict[str, FileKind] = {".pdf": "pdf", ".docx": "docx", ".txt": "txt"}
KIND_LABELS: dict[FileKind, str] = {
    "pdf": "PDF (.pdf)",
    "docx": "Word (.docx)",
    "txt": "text (.txt)",
}
RESUME_KINDS: frozenset[FileKind] = frozenset({"pdf", "docx"})
JOB_DESCRIPTION_KINDS: frozenset[FileKind] = frozenset({"pdf", "docx", "txt"})

# Plain-text limits: share of control characters above which a .txt is treated as binary.
MAX_TEXT_CONTROL_RATIO = 0.05

# DOCX (ZIP) safety limits - guard against zip bombs and oversized archives.
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRY_RATIO = 200  # uncompressed / compressed, per entry

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ValidatedUpload:
    kind: FileKind
    content_type: str
    extension: str
    safe_filename: str
    data: bytes
    size: int
    sha256: str
    page_count: int | None  # PDFs only


def sanitize_filename(raw: str | None) -> str:
    """Display-only original name: basename, no control chars, bounded length.

    The stored file never uses this name (storage uses the resume UUID).
    """
    name = raw or ""
    # Strip any directory components, for both separator styles.
    name = PureWindowsPath(PurePosixPath(name).name).name
    name = unicodedata.normalize("NFC", name)
    name = _CONTROL_CHARS.sub("", name).strip().strip(".")
    if not name:
        return "resume"
    if len(name) > 255:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 255 - len(ext) - 1] + dot + ext) if dot and len(ext) <= 10 else name[:255]
    return name


def _extension(filename: str, allowed: frozenset[FileKind]) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in EXTENSIONS or EXTENSIONS[suffix] not in allowed:
        labels = [KIND_LABELS[k] for k in ("pdf", "docx", "txt") if k in allowed]
        raise UploadRejectedError(
            ResumeErrorCode.UNSUPPORTED_TYPE,
            f"Only {', '.join(labels[:-1])} and {labels[-1]} files are accepted"
            if len(labels) > 1
            else f"Only {labels[0]} files are accepted",
        )
    return suffix


UTF16_BOMS = (bytes([0xFF, 0xFE]), bytes([0xFE, 0xFF]))
NUL = chr(0)
# Tab, line feed, carriage return and form feed are normal in text files.
TEXT_WHITESPACE_CONTROLS = frozenset({chr(9), chr(10), chr(13), chr(12)})


def decode_text(data: bytes) -> str:
    """Decode a plain-text upload: UTF-8 (with or without BOM), UTF-16 with BOM, else cp1252.

    Raises UnicodeDecodeError when the bytes are not text in any of these encodings.
    """
    if data.startswith(UTF16_BOMS):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")  # legacy Windows text; raises on undefined bytes


def _check_txt(data: bytes) -> None:
    try:
        text = decode_text(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise UploadRejectedError(
            ResumeErrorCode.CONTENT_MISMATCH, "The file is not a readable text document"
        ) from exc
    if NUL in text:
        raise UploadRejectedError(
            ResumeErrorCode.CONTENT_MISMATCH, "The file contains binary data, not plain text"
        )
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in TEXT_WHITESPACE_CONTROLS)
    if text and controls / len(text) > MAX_TEXT_CONTROL_RATIO:
        raise UploadRejectedError(
            ResumeErrorCode.CONTENT_MISMATCH, "The file contains binary data, not plain text"
        )
    if not text.strip():
        raise UploadRejectedError(ResumeErrorCode.EMPTY_FILE, "The text file contains no text")


def _sniff(data: bytes) -> FileKind | None:
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "docx"  # confirmed by _check_docx
    return None


def _check_pdf(data: bytes, max_pages: int) -> int:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UploadRejectedError(
            ResumeErrorCode.CORRUPTED_FILE, "The PDF could not be opened; it may be damaged"
        ) from exc
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise UploadRejectedError(
                ResumeErrorCode.ENCRYPTED_FILE, "Password-protected PDFs are not supported"
            )
        # MuPDF silently repairs damaged files and returns whatever survived. A repaired
        # file without the end-of-file marker is truncated: its text is incomplete.
        if doc.is_repaired and b"%%EOF" not in data[-2048:]:
            raise UploadRejectedError(
                ResumeErrorCode.CORRUPTED_FILE,
                "The PDF appears to be truncated or damaged; please upload the complete file",
            )
        pages = int(doc.page_count)
        if pages < 1:
            raise UploadRejectedError(ResumeErrorCode.CORRUPTED_FILE, "The PDF has no pages")
        if pages > max_pages:
            raise UploadRejectedError(
                ResumeErrorCode.TOO_MANY_PAGES,
                f"The PDF has {pages} pages; the limit is {max_pages}",
            )
        # Touch every page so structurally broken files fail now, not mid-extraction.
        for index in range(pages):
            doc.load_page(index).get_text("text")
        return pages
    except UploadRejectedError:
        raise
    except Exception as exc:
        raise UploadRejectedError(
            ResumeErrorCode.CORRUPTED_FILE, "The PDF is damaged and cannot be read"
        ) from exc
    finally:
        doc.close()


def _check_docx(data: bytes) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise UploadRejectedError(
            ResumeErrorCode.CORRUPTED_FILE, "The Word file could not be opened; it may be damaged"
        ) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise UploadRejectedError(
                ResumeErrorCode.UNSAFE_FILE, "The Word file has too many parts"
            )
        total = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise UploadRejectedError(
                    ResumeErrorCode.ENCRYPTED_FILE,
                    "Password-protected Word files are not supported",
                )
            total += info.file_size
            ratio = info.file_size / max(info.compress_size, 1)
            if total > MAX_ZIP_UNCOMPRESSED_BYTES or (
                info.file_size > 1024 * 1024 and ratio > MAX_ZIP_ENTRY_RATIO
            ):
                raise UploadRejectedError(
                    ResumeErrorCode.UNSAFE_FILE, "The Word file expands to an unsafe size"
                )
        names = {info.filename for info in infos}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise UploadRejectedError(
                ResumeErrorCode.CONTENT_MISMATCH,
                "The file is a ZIP archive but not a Word document",
            )
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise UploadRejectedError(
                ResumeErrorCode.UNSAFE_FILE, "Macro-enabled Word documents are not accepted"
            )
        try:
            content_types = archive.read("[Content_Types].xml")
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            raise UploadRejectedError(
                ResumeErrorCode.CORRUPTED_FILE, "The Word file is damaged"
            ) from exc
        if b"macroEnabled" in content_types:
            raise UploadRejectedError(
                ResumeErrorCode.UNSAFE_FILE, "Macro-enabled Word documents are not accepted"
            )
        if b"wordprocessingml.document.main" not in content_types:
            raise UploadRejectedError(
                ResumeErrorCode.CONTENT_MISMATCH, "The file is not a Word (.docx) document"
            )

    try:
        import docx

        docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise UploadRejectedError(
            ResumeErrorCode.CORRUPTED_FILE, "The Word file is damaged and cannot be read"
        ) from exc


def validate_upload(
    data: bytes,
    filename: str | None,
    *,
    max_bytes: int,
    max_pages: int,
    allowed: frozenset[FileKind] = RESUME_KINDS,
) -> ValidatedUpload:
    """Validate an uploaded document. Raises :class:`UploadRejectedError`.

    ``allowed`` selects the accepted kinds (resumes: PDF/DOCX; job descriptions: also TXT).
    """
    safe_name = sanitize_filename(filename)
    extension = _extension(safe_name, allowed)
    if not data:
        raise UploadRejectedError(ResumeErrorCode.EMPTY_FILE, "The uploaded file is empty")
    if len(data) > max_bytes:
        raise UploadRejectedError(
            ResumeErrorCode.FILE_TOO_LARGE,
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit",
        )

    declared_kind = EXTENSIONS[extension]
    detected = _sniff(data)
    if declared_kind == "txt":
        if detected is not None:
            raise UploadRejectedError(
                ResumeErrorCode.CONTENT_MISMATCH,
                f"The file extension is {extension} but the content is {detected.upper()}",
            )
        _check_txt(data)
        detected = "txt"
    if detected is None:
        raise UploadRejectedError(
            ResumeErrorCode.CORRUPTED_FILE
            if declared_kind == "pdf"
            else ResumeErrorCode.CONTENT_MISMATCH,
            f"The file content is not a valid {declared_kind.upper()} document",
        )
    if detected != declared_kind:
        raise UploadRejectedError(
            ResumeErrorCode.CONTENT_MISMATCH,
            f"The file extension is {extension} but the content is {detected.upper()}",
        )

    page_count: int | None = None
    if detected == "pdf":
        page_count = _check_pdf(data, max_pages)
    elif detected == "docx":
        _check_docx(data)

    return ValidatedUpload(
        kind=detected,
        content_type=CONTENT_TYPES[detected],
        extension=extension,
        safe_filename=safe_name,
        data=data,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=page_count,
    )
