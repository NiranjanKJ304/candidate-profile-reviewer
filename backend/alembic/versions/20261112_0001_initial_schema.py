"""Initial schema: candidates, resumes, job descriptions, developer profiles, evaluations.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-12

Written to match ``app.models`` exactly (constraint names follow the naming
convention in ``app.db.base``). ``tests/integration/test_migrations_live.py`` checks
there is no drift between this migration and the models.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_PK_DEFAULT = sa.text("gen_random_uuid()")


def _id() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid(), server_default=UUID_PK_DEFAULT, nullable=False)


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _in(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


PLATFORMS = ("github", "leetcode", "hackerrank", "codeforces", "codechef", "kaggle")
PARSE_STATUSES = ("pending", "processing", "completed", "failed")
OWNERSHIP_STATUSES = ("unverified", "verified", "mismatch")
COLLECTION_STATUSES = ("not_collected", "collected", "unavailable", "failed")
EVALUATION_STATUSES = ("pending", "running", "completed", "failed")


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidates")),
        sa.CheckConstraint(
            "length(btrim(full_name)) > 0", name=op.f("ck_candidates_full_name_not_blank")
        ),
        sa.UniqueConstraint("email", name=op.f("uq_candidates_email")),
    )

    op.create_table(
        "job_descriptions",
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("analysis", postgresql.JSONB(), nullable=True),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_descriptions")),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_job_descriptions_title_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(description_text)) > 0",
            name=op.f("ck_job_descriptions_description_not_blank"),
        ),
    )

    op.create_table(
        "developer_profiles",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("profile_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "ownership_status", sa.String(length=20), server_default="unverified", nullable=False
        ),
        sa.Column(
            "collection_status",
            sa.String(length=20),
            server_default="not_collected",
            nullable=False,
        ),
        sa.Column("collected_data", postgresql.JSONB(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_developer_profiles")),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name=op.f("fk_developer_profiles_candidate_id_candidates"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "candidate_id", "platform", name=op.f("uq_developer_profiles_candidate_id_platform")
        ),
        sa.CheckConstraint(
            "length(btrim(username)) > 0", name=op.f("ck_developer_profiles_username_not_blank")
        ),
        sa.CheckConstraint(_in("platform", PLATFORMS), name=op.f("ck_developer_profiles_platform")),
        sa.CheckConstraint(
            _in("ownership_status", OWNERSHIP_STATUSES),
            name=op.f("ck_developer_profiles_ownership_status"),
        ),
        sa.CheckConstraint(
            _in("collection_status", COLLECTION_STATUSES),
            name=op.f("ck_developer_profiles_collection_status"),
        ),
    )
    op.create_index(
        op.f("ix_developer_profiles_candidate_id"), "developer_profiles", ["candidate_id"]
    )

    op.create_table(
        "resumes",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=True),
        sa.Column("parse_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("parsed_data", postgresql.JSONB(), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resumes")),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name=op.f("fk_resumes_candidate_id_candidates"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("candidate_id", "sha256", name=op.f("uq_resumes_candidate_id_sha256")),
        sa.CheckConstraint("file_size_bytes > 0", name=op.f("ck_resumes_file_size_positive")),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_resumes_sha256_hex")),
        sa.CheckConstraint(
            _in("parse_status", PARSE_STATUSES), name=op.f("ck_resumes_parse_status")
        ),
    )
    op.create_index(op.f("ix_resumes_candidate_id"), "resumes", ["candidate_id"])

    op.create_table(
        "evaluations",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_description_id", sa.Uuid(), nullable=False),
        sa.Column("resume_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("overall_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name=op.f("fk_evaluations_candidate_id_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_description_id"],
            ["job_descriptions.id"],
            name=op.f("fk_evaluations_job_description_id_job_descriptions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resume_id"],
            ["resumes.id"],
            name=op.f("fk_evaluations_resume_id_resumes"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name=op.f("ck_evaluations_overall_score_range"),
        ),
        sa.CheckConstraint(
            _in("status", EVALUATION_STATUSES), name=op.f("ck_evaluations_evaluation_status")
        ),
    )
    op.create_index(op.f("ix_evaluations_candidate_id"), "evaluations", ["candidate_id"])
    op.create_index(
        op.f("ix_evaluations_job_description_id"), "evaluations", ["job_description_id"]
    )
    op.create_index(op.f("ix_evaluations_resume_id"), "evaluations", ["resume_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_evaluations_resume_id"), table_name="evaluations")
    op.drop_index(op.f("ix_evaluations_job_description_id"), table_name="evaluations")
    op.drop_index(op.f("ix_evaluations_candidate_id"), table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_index(op.f("ix_resumes_candidate_id"), table_name="resumes")
    op.drop_table("resumes")
    op.drop_index(op.f("ix_developer_profiles_candidate_id"), table_name="developer_profiles")
    op.drop_table("developer_profiles")
    op.drop_table("job_descriptions")
    op.drop_table("candidates")
