"""Alembic migrations against a live PostgreSQL (isolated temporary schema)."""

from __future__ import annotations

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

import app.models  # noqa: F401  (register tables)
from app.db.base import Base
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

DOMAIN_TABLES = {"candidates", "resumes", "job_descriptions", "developer_profiles", "evaluations"}


def _tables(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


def _drift(conn: Connection) -> list[Any]:
    ctx = MigrationContext.configure(conn, opts={"compare_type": True})
    return list(compare_metadata(ctx, Base.metadata))


async def test_upgrade_creates_tables_and_records_revision(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        tables = await conn.run_sync(_tables)
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert tables >= DOMAIN_TABLES
    assert version == "0006_evaluation_errors"


async def test_migration_matches_models_exactly(migrated_engine: AsyncEngine) -> None:
    """No drift between the hand-written migration and the ORM models."""
    async with migrated_engine.connect() as conn:
        diff = await conn.run_sync(_drift)
    assert diff == [], f"migration and models differ: {diff}"


async def test_downgrade_removes_everything_and_upgrade_is_repeatable(
    migrated_engine: AsyncEngine,
) -> None:
    async with migrated_engine.begin() as conn:
        await conn.run_sync(run_alembic, "downgrade", "base")
    async with migrated_engine.connect() as conn:
        assert not (DOMAIN_TABLES & await conn.run_sync(_tables))

    async with migrated_engine.begin() as conn:
        await conn.run_sync(run_alembic, "upgrade", "head")
    async with migrated_engine.connect() as conn:
        assert await conn.run_sync(_tables) >= DOMAIN_TABLES


async def test_self_reported_migration_round_trip_with_data(migrated_engine: AsyncEngine) -> None:
    """0005 down/up on a populated table: not_applicable rows survive a downgrade."""
    async with migrated_engine.begin() as conn:
        cid = (
            await conn.execute(
                text("INSERT INTO candidates (full_name) VALUES ('Round Trip') RETURNING id")
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO developer_profiles (candidate_id, platform, username, sync_status, "
                "self_reported_data) VALUES (:cid, 'leetcode', 'jane', 'not_applicable', "
                '\'{"source_type": "self_reported"}\')'
            ),
            {"cid": cid},
        )
        await conn.run_sync(run_alembic, "downgrade", "0004_developer_profile_sync")  # via 0006
        row = (await conn.execute(text("SELECT sync_status FROM developer_profiles"))).one()
        assert row.sync_status == "pending"
        cols = {
            c["name"]
            for c in await conn.run_sync(lambda c: inspect(c).get_columns("developer_profiles"))
        }
        assert "self_reported_data" not in cols
        await conn.run_sync(run_alembic, "upgrade", "head")
        with pytest.raises(Exception, match="ck_developer_profiles_sync_status"):
            async with conn.begin_nested():
                await conn.execute(text("UPDATE developer_profiles SET sync_status = 'bogus'"))
        await conn.execute(text("UPDATE developer_profiles SET sync_status = 'not_applicable'"))


async def test_upgrade_is_idempotent_at_head(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.begin() as conn:
        await conn.run_sync(run_alembic, "upgrade", "head")  # already at head: no-op
        count = (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar_one()
    assert count == 1
