"""Resume parsing: extraction metadata, structuring provenance, error codes, warnings.

Revision ID: 0002_resume_parsing
Revises: 0001_initial_schema
Create Date: 2026-11-12

All new columns are nullable, so the migration is safe on populated tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_resume_parsing"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTRACTION_METHODS = ("pdf_text", "pdf_ocr", "docx")
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


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("resumes", sa.Column("extraction_method", sa.String(length=20), nullable=True))
    op.add_column("resumes", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("resumes", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.add_column("resumes", sa.Column("parser_model", sa.String(length=200), nullable=True))
    op.add_column("resumes", sa.Column("parse_error_code", sa.String(length=40), nullable=True))
    op.add_column("resumes", sa.Column("parse_warnings", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        op.f("ck_resumes_extraction_method"),
        "resumes",
        _in("extraction_method", EXTRACTION_METHODS),
    )
    op.create_check_constraint(
        op.f("ck_resumes_parse_error_code"), "resumes", _in("parse_error_code", ERROR_CODES)
    )
    op.create_check_constraint(
        op.f("ck_resumes_page_count_positive"), "resumes", "page_count IS NULL OR page_count > 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_resumes_page_count_positive"), "resumes", type_="check")
    op.drop_constraint(op.f("ck_resumes_parse_error_code"), "resumes", type_="check")
    op.drop_constraint(op.f("ck_resumes_extraction_method"), "resumes", type_="check")
    op.drop_column("resumes", "parse_warnings")
    op.drop_column("resumes", "parse_error_code")
    op.drop_column("resumes", "parser_model")
    op.drop_column("resumes", "extracted_text")
    op.drop_column("resumes", "page_count")
    op.drop_column("resumes", "extraction_method")
