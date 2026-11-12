"""Alembic migrations without a database (offline SQL generation)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[2]
OFFLINE_URL = "postgresql+asyncpg://offline:offline@127.0.0.1:5432/offline"


def _config(buffer: io.StringIO | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=buffer)
    cfg.attributes["database_url"] = OFFLINE_URL
    cfg.attributes["skip_logging_config"] = True
    return cfg


def _sql(revision: str, downgrade: bool = False) -> str:
    buffer = io.StringIO()
    cfg = _config(buffer)
    if downgrade:
        command.downgrade(cfg, revision, sql=True)
    else:
        command.upgrade(cfg, revision, sql=True)
    return buffer.getvalue()


def test_single_linear_head() -> None:
    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == ["0003_job_description_parsing"]
    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == [
        "0003_job_description_parsing",
        "0002_resume_parsing",
        "0001_initial_schema",
    ]
    assert revisions[0].down_revision == "0002_resume_parsing"
    assert revisions[1].down_revision == "0001_initial_schema"
    assert revisions[2].down_revision is None


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _sql("head")


@pytest.mark.parametrize(
    "table", ["candidates", "job_descriptions", "developer_profiles", "resumes", "evaluations"]
)
def test_upgrade_creates_every_table(upgrade_sql: str, table: str) -> None:
    assert f"CREATE TABLE {table} (" in upgrade_sql


def test_tables_created_in_dependency_order(upgrade_sql: str) -> None:
    pos = {
        t: upgrade_sql.index(f"CREATE TABLE {t} (")
        for t in ("candidates", "job_descriptions", "resumes", "evaluations")
    }
    assert pos["candidates"] < pos["resumes"] < pos["evaluations"]
    assert pos["job_descriptions"] < pos["evaluations"]


@pytest.mark.parametrize(
    "fragment",
    [
        "id UUID DEFAULT gen_random_uuid() NOT NULL",
        "REFERENCES candidates (id) ON DELETE CASCADE",
        "REFERENCES job_descriptions (id) ON DELETE RESTRICT",
        "REFERENCES resumes (id) ON DELETE SET NULL",
        "CONSTRAINT uq_resumes_candidate_id_sha256 UNIQUE (candidate_id, sha256)",
        "CONSTRAINT uq_developer_profiles_candidate_id_platform UNIQUE (candidate_id, platform)",
        "CREATE INDEX ix_evaluations_job_description_id ON evaluations (job_description_id)",
        "INSERT INTO alembic_version (version_num) VALUES ('0001_initial_schema')",
        "UPDATE alembic_version SET version_num='0002_resume_parsing'",
        "ALTER TABLE resumes ADD COLUMN extracted_text TEXT",
        "ALTER TABLE resumes ADD COLUMN parse_warnings JSONB",
        "ADD CONSTRAINT ck_resumes_parse_error_code CHECK",
        "ADD CONSTRAINT ck_resumes_extraction_method CHECK",
    ],
)
def test_upgrade_sql_contains(upgrade_sql: str, fragment: str) -> None:
    assert fragment in upgrade_sql


def test_downgrade_drops_everything_in_reverse_order() -> None:
    sql = _sql("0001_initial_schema:base", downgrade=True)
    drops = [line for line in sql.splitlines() if line.startswith("DROP TABLE")]
    assert drops == [
        "DROP TABLE evaluations;",
        "DROP TABLE resumes;",
        "DROP TABLE developer_profiles;",
        "DROP TABLE job_descriptions;",
        "DROP TABLE candidates;",
    ]


def test_resume_parsing_migration_downgrades_cleanly() -> None:
    sql = _sql("0002_resume_parsing:0001_initial_schema", downgrade=True)
    for column in (
        "extraction_method",
        "page_count",
        "extracted_text",
        "parser_model",
        "parse_error_code",
        "parse_warnings",
    ):
        assert f"ALTER TABLE resumes DROP COLUMN {column}" in sql
    assert "DROP CONSTRAINT ck_resumes_parse_error_code" in sql


def test_job_description_migration_sql() -> None:
    sql = _sql("0002_resume_parsing:0003_job_description_parsing")
    assert "ALTER TABLE job_descriptions ALTER COLUMN title DROP NOT NULL" in sql
    assert "ALTER TABLE job_descriptions ADD COLUMN reviewed_data JSONB" in sql
    assert "ADD CONSTRAINT uq_job_descriptions_sha256 UNIQUE (sha256)" in sql
    assert "'txt'" in sql


def test_job_description_migration_downgrade_sql() -> None:
    sql = _sql("0003_job_description_parsing:0002_resume_parsing", downgrade=True)
    assert "ALTER TABLE job_descriptions DROP COLUMN reviewed_data" in sql
    assert "ALTER COLUMN title SET NOT NULL" in sql
