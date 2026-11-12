"""Synthetic resume files generated in code for tests (no real personal data).

The fictional person "Jordan Sample" and the example.org domain are used
throughout; nothing here is a real resume.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import docx
import pymupdf

RESUME_LINES = [
    "Jordan Sample",
    "Email: jordan.sample@example.org | Phone: +1 555 010 2030",
    "Location: Springfield | github.com/jordan-sample",
    "",
    "EXPERIENCE",
    "Software Engineer, Example Corp (Jan 2021 - Present)",
    "- Built REST APIs with Python and FastAPI on PostgreSQL",
    "",
    "INTERNSHIP",
    "Backend Intern, Sample Labs (Jun 2020 - Aug 2020)",
    "",
    "EDUCATION",
    "B.Sc. Computer Science, Example University (2017 - 2021)",
    "",
    "SKILLS: Python, FastAPI, PostgreSQL, Docker",
    "CERTIFICATIONS: AWS Certified Cloud Practitioner (2022)",
    "ACHIEVEMENTS: Winner, Example Hackathon 2020",
]
RESUME_TEXT = "\n".join(RESUME_LINES)


def text_pdf(lines: list[str] | None = None, pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72.0
        for line in lines if lines is not None else RESUME_LINES:
            page.insert_text((72, y), line, fontsize=11)
            y += 16
    return bytes(doc.tobytes())


def scanned_pdf(lines: list[str] | None = None) -> bytes:
    """Image-only PDF (no text layer), like a scanned paper resume."""
    source = pymupdf.open(stream=text_pdf(lines), filetype="pdf")
    page = source.load_page(0)
    pixmap = page.get_pixmap(dpi=200)
    scan = pymupdf.open()
    target = scan.new_page(width=page.rect.width, height=page.rect.height)
    target.insert_image(target.rect, stream=pixmap.tobytes("png"))
    return bytes(scan.tobytes(garbage=3, deflate=True))


def blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    return bytes(doc.tobytes())


def encrypted_pdf() -> bytes:
    doc = pymupdf.open(stream=text_pdf(), filetype="pdf")
    buffer = io.BytesIO()
    doc.save(
        buffer,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret-user-pw",
        owner_pw="secret-owner-pw",
    )
    return buffer.getvalue()


def make_docx(
    paragraphs: list[str] | None = None,
    *,
    header: str | None = None,
    table: list[list[str]] | None = None,
) -> bytes:
    document = docx.Document()
    if header:
        document.sections[0].header.paragraphs[0].text = header
    for line in paragraphs if paragraphs is not None else RESUME_LINES:
        document.add_paragraph(line)
    if table:
        t = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _rewrite_zip(data: bytes, edit: Any) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        edit(source, target)
    return out.getvalue()


def docx_without_document_xml() -> bytes:
    def edit(src: zipfile.ZipFile, dst: zipfile.ZipFile) -> None:
        for info in src.infolist():
            if info.filename != "word/document.xml":
                dst.writestr(info, src.read(info))

    return _rewrite_zip(make_docx(), edit)


def macro_docm_as_docx() -> bytes:
    def edit(src: zipfile.ZipFile, dst: zipfile.ZipFile) -> None:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("word/vbaProject.bin", b"\x00fake-macro")

    return _rewrite_zip(make_docx(), edit)


def plain_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("notes.txt", "not a word document")
    return out.getvalue()


def zip_bomb_docx() -> bytes:
    """Valid DOCX parts plus a member that inflates enormously (compression ratio)."""

    def edit(src: zipfile.ZipFile, dst: zipfile.ZipFile) -> None:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("word/media/bomb.bin", b"\x00" * (20 * 1024 * 1024))

    return _rewrite_zip(make_docx(), edit)


def llm_resume_json(**overrides: Any) -> str:
    """A well-formed structured response consistent with RESUME_TEXT."""
    data: dict[str, Any] = {
        "personal_details": {
            "full_name": "Jordan Sample",
            "email": "jordan.sample@example.org",
            "phone": "+1 555 010 2030",
            "location": "Springfield",
            "linkedin_url": None,
            "github_url": "github.com/jordan-sample",
            "portfolio_url": None,
            "other_links": [],
            "summary": None,
        },
        "education": [
            {
                "institution": "Example University",
                "degree": "B.Sc.",
                "field_of_study": "Computer Science",
                "start_date": "2017",
                "end_date": "2021",
                "grade": None,
                "location": None,
            }
        ],
        "work_experience": [
            {
                "company": "Example Corp",
                "title": "Software Engineer",
                "location": None,
                "start_date": "Jan 2021",
                "end_date": "Present",
                "is_current": True,
                "responsibilities": ["Built REST APIs with Python and FastAPI on PostgreSQL"],
                "technologies": ["Python", "FastAPI", "PostgreSQL"],
            }
        ],
        "internships": [
            {
                "company": "Sample Labs",
                "title": "Backend Intern",
                "location": None,
                "start_date": "Jun 2020",
                "end_date": "Aug 2020",
                "is_current": False,
                "responsibilities": [],
                "technologies": [],
            }
        ],
        "technical_skills": [
            {"name": "Python", "category": "language"},
            {"name": "FastAPI", "category": "framework"},
            {"name": "PostgreSQL", "category": "database"},
            {"name": "Docker", "category": "devops"},
        ],
        "projects": [],
        "certifications": [
            {
                "name": "AWS Certified Cloud Practitioner",
                "issuer": None,
                "issue_date": "2022",
                "expiry_date": None,
                "credential_id": None,
                "url": None,
            }
        ],
        "achievements": [{"description": "Winner, Example Hackathon 2020", "date": "2020"}],
    }
    data.update(overrides)
    return json.dumps(data)
