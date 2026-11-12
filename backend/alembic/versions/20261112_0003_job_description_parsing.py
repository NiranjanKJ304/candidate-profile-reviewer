"""Job-description parsing: file reference, extraction, structuring provenance, review.

Revision ID: 0003_job_description_parsing
Revises: 0002_resume_parsing
Create Date: 2026-11-12

* ``title`` / ``description_text`` become nullable (unknown until a file is parsed);
  their not-blank checks now allow NULL.
* New nullable columns mirror the resume pipeline (file metadata, extraction,
  status, errors, warnings, model) plus ``reviewed_data`` / ``reviewed_at``.
* ``parse_status`` is NOT NULL with server default 'pending' (safe for existing rows).
* Extraction-method checks on both tables gain 'txt'.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_job_description_parsing"
down_revision: str | None = "0002_resume_parsing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTRACTION_METHODS_OLD = ("pdf_text", "pdf_ocr", "docx")
EXTRACTION_METHODS = ("pdf_text", "pdf_ocr", "docx", "txt")
PARSE_STATUSES = ("pending", "processing", "completed", "failed")
ERROR_CODES = (
    "empty_file",
    "file_too_large",
    "unsupported_type",
    "content_mismatch",
    "corrupted_file",
    "encrypted_file",
    "too_many_pages",
    "unsafe_file",
    "no_text",
    "ocr_unavailable",
    "ocr_failed",
    "llm_not_configured",
    "llm_auth_failed",
    "llm_timeout",
    "llm_rate_limited",
    "llm_error",
    "llm_invalid_response",
    "storage_error",
    "internal_error",
)
JD = "job_descriptions"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # Nullable title / text with NULL-tolerant not-blank checks.
    op.drop_constraint(op.f("ck_job_descriptions_title_not_blank"), JD, type_="check")
    op.drop_constraint(op.f("ck_job_descriptions_description_not_blank"), JD, type_="check")
    op.alter_column(JD, "title", existing_type=sa.String(length=300), nullable=True)
    op.alter_column(JD, "description_text", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        op.f("ck_job_descriptions_title_not_blank"), JD, "title IS NULL OR length(btrim(title)) > 0"
    )
    op.create_check_constraint(
        op.f("ck_job_descriptions_description_not_blank"),
        JD,
        "description_text IS NULL OR length(btrim(description_text)) > 0",
    )

    columns = [
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_path", sa.String(length=1024), nullable=True),
        sa.Column("extraction_method", sa.String(length=20), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("parse_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("parse_error_code", sa.String(length=40), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parse_warnings", postgresql.JSONB(), nullable=True),
        sa.Column("parser_model", sa.String(length=200), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_data", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in columns:
        op.add_column(JD, column)

    op.create_unique_constraint(op.f("uq_job_descriptions_sha256"), JD, ["sha256"])
    op.create_check_constraint(
        op.f("ck_job_descriptions_file_size_positive"), JD, "file_size_bytes IS NULL OR file_size_bytes > 0"
    )
    op.create_check_constraint(
        op.f("ck_job_descriptions_sha256_hex"), JD, "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'"
    )
    op.create_check_constraint(
        op.f("ck_job_descriptions_page_count_positive"), JD, "page_count IS NULL OR page_count > 0"
    )
    op.create_check_constraint(
        op.f("ck_job_descriptions_extraction_method"), JD, _in("extraction_method", EXTRACTION_METHODS)
    )
    op.create_check_constraint(
        op.f("ck_job_descriptions_parse_status"), JD, _in("parse_status", PARSE_STATUSES)
    )
    op.create_check_constraint(
        op.f("ck_job_descriptions_parse_error_code"), JD, _in("parse_error_code", ERROR_CODES)
    )

    # Resumes share the ExtractionMethod enum; widen its check to include 'txt'.
    op.drop_constraint(op.f("ck_resumes_extraction_method"), "resumes", type_="check")
    op.create_check_constraint(
        op.f("ck_resumes_extraction_method"), "resumes", _in("extraction_method", EXTRACTION_METHODS)
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_resumes_extraction_method"), "resumes", type_="check")
    op.create_check_constraint(
        op.f("ck_resumes_extraction_method"), "resumes", _in("extraction_method", EXTRACTION_METHODS_OLD)
    )
    for name in (
        "ck_job_descriptions_parse_error_code",
        "ck_job_descriptions_parse_status",
        "ck_job_descriptions_extraction_method",
        "ck_job_descriptions_page_count_positive",
        "ck_job_descriptions_sha256_hex",
        "ck_job_descriptions_file_size_positive",
    ):
        op.drop_constraint(op.f(name), JD, type_="check")
    op.drop_constraint(op.f("uq_job_descriptions_sha256"), JD, type_="unique")
    for column in (
        "reviewed_at",
        "reviewed_data",
        "parsed_at",
        "parser_model",
        "parse_warnings",
        "parse_error",
        "parse_error_code",
        "parse_status",
        "extracted_text",
        "page_count",
        "extraction_method",
        "storage_path",
        "sha256",
        "file_size_bytes",
        "content_type",
        "original_filename",
    ):
        op.drop_column(JD, column)

    # Uploaded JDs that never got a title/text cannot satisfy NOT NULL again.
    op.execute("DELETE FROM job_descriptions WHERE title IS NULL OR description_text IS NULL")
    op.drop_constraint(op.f("ck_job_descriptions_title_not_blank"), JD, type_="check")
    op.drop_constraint(op.f("ck_job_descriptions_description_not_blank"), JD, type_="check")
    op.alter_column(JD, "title", existing_type=sa.String(length=300), nullable=False)
    op.alter_column(JD, "description_text", existing_type=sa.Text(), nullable=False)
    op.create_check_constraint(op.f("ck_job_descriptions_title_not_blank"), JD, "length(btrim(title)) > 0")
    op.create_check_constraint(
        op.f("ck_job_descriptions_description_not_blank"), JD, "length(btrim(description_text)) > 0"
    )
