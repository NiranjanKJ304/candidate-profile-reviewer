"""Developer-profile synchronisation: lifecycle, errors, warnings, stable account id.

Revision ID: 0004_developer_profile_sync
Revises: 0003_job_description_parsing
Create Date: 2026-11-12

All new columns are nullable except ``sync_status`` (server default 'pending'),
so the migration is safe on populated tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_developer_profile_sync"
down_revision: str | None = "0003_job_description_parsing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "developer_profiles"
SYNC_STATUSES = ("pending", "processing", "completed", "failed")
SYNC_ERROR_CODES = (
    "invalid_profile",
    "not_a_user",
    "rate_limited",
    "permission_denied",
    "auth_failed",
    "network_error",
    "timeout",
    "api_error",
    "integration_disabled",
    "internal_error",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column(T, sa.Column("external_id", sa.String(length=64), nullable=True))
    op.add_column(
        T, sa.Column("sync_status", sa.String(length=20), server_default="pending", nullable=False)
    )
    op.add_column(T, sa.Column("sync_error_code", sa.String(length=40), nullable=True))
    op.add_column(T, sa.Column("sync_error", sa.Text(), nullable=True))
    op.add_column(T, sa.Column("sync_warnings", postgresql.JSONB(), nullable=True))
    op.add_column(T, sa.Column("last_sync_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_developer_profiles_sync_status"), T, _in("sync_status", SYNC_STATUSES)
    )
    op.create_check_constraint(
        op.f("ck_developer_profiles_sync_error_code"), T, _in("sync_error_code", SYNC_ERROR_CODES)
    )
    op.create_index("ix_developer_profiles_platform_external_id", T, ["platform", "external_id"])


def downgrade() -> None:
    op.drop_index("ix_developer_profiles_platform_external_id", table_name=T)
    op.drop_constraint(op.f("ck_developer_profiles_sync_error_code"), T, type_="check")
    op.drop_constraint(op.f("ck_developer_profiles_sync_status"), T, type_="check")
    for column in (
        "last_sync_attempt_at",
        "sync_warnings",
        "sync_error",
        "sync_error_code",
        "sync_status",
        "external_id",
    ):
        op.drop_column(T, column)
