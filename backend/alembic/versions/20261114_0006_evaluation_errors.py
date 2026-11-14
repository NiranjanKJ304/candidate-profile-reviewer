"""Evaluation failure reason and warnings (Phase 5).

Revision ID: 0006_evaluation_errors
Revises: 0005_self_reported_evidence
Create Date: 2026-11-14

Adds nullable ``error_code`` (machine-readable failure reason, CHECK-constrained)
and ``warnings`` (JSONB) to ``evaluations``, matching resumes, JDs and developer
profiles. Safe on populated tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_evaluation_errors"
down_revision: str | None = "0005_self_reported_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "evaluations"
ERROR_CODES = (
    "source_missing",
    "jd_not_parsed",
    "jd_has_no_requirements",
    "no_evidence",
    "llm_not_configured",
    "llm_auth_failed",
    "llm_timeout",
    "llm_rate_limited",
    "llm_error",
    "llm_invalid_response",
    "internal_error",
)


def upgrade() -> None:
    op.add_column(T, sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column(T, sa.Column("warnings", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        op.f("ck_evaluations_error_code"),
        T,
        "error_code IN (" + ", ".join(repr(c) for c in ERROR_CODES) + ")",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_evaluations_error_code"), T, type_="check")
    op.drop_column(T, "warnings")
    op.drop_column(T, "error_code")
