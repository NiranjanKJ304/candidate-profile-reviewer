"""Self-reported developer-platform evidence (LeetCode) and the not_applicable sync status.

Revision ID: 0005_self_reported_evidence
Revises: 0004_developer_profile_sync
Create Date: 2026-11-13

LeetCode offers no official or permitted data API, so its evidence can only be
supplied manually. It is stored in its own nullable columns, never in
``collected_data`` (which holds evidence collected from a platform), so
self-reported information cannot be mistaken for verified platform data.
``sync_status`` gains ``not_applicable`` for profiles that will never sync.

Safe on populated tables: only nullable columns are added. Downgrade (development
only) drops the self-reported evidence and resets ``not_applicable`` rows to
``pending`` before restoring the previous CHECK constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_self_reported_evidence"
down_revision: str | None = "0004_developer_profile_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "developer_profiles"
OLD_SYNC_STATUSES = ("pending", "processing", "completed", "failed")
NEW_SYNC_STATUSES = (*OLD_SYNC_STATUSES, "not_applicable")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column(T, sa.Column("self_reported_data", postgresql.JSONB(), nullable=True))
    op.add_column(T, sa.Column("self_reported_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f("ck_developer_profiles_sync_status"), T, type_="check")
    op.create_check_constraint(
        op.f("ck_developer_profiles_sync_status"), T, _in("sync_status", NEW_SYNC_STATUSES)
    )


def downgrade() -> None:
    op.execute(
        "UPDATE developer_profiles SET sync_status = 'pending' "
        "WHERE sync_status = 'not_applicable'"
    )
    op.drop_constraint(op.f("ck_developer_profiles_sync_status"), T, type_="check")
    op.create_check_constraint(
        op.f("ck_developer_profiles_sync_status"), T, _in("sync_status", OLD_SYNC_STATUSES)
    )
    op.drop_column(T, "self_reported_at")
    op.drop_column(T, "self_reported_data")
