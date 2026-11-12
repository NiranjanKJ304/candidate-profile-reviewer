"""Fixtures for live PostgreSQL tests.

``migrated_engine`` runs the real Alembic migrations into a throwaway schema with a
random name, yields an engine whose ``search_path`` points at it, and drops the
schema afterwards. Existing tables and data in the target database are never
touched, so these tests are safe to point at a development database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import integration_env

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(connection: Connection) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["connection"] = connection
    cfg.attributes["skip_logging_config"] = True
    return cfg


def run_alembic(connection: Connection, action: str, revision: str) -> None:
    cfg = alembic_config(connection)
    if action == "upgrade":
        command.upgrade(cfg, revision)
    else:
        command.downgrade(cfg, revision)


@pytest.fixture
def database_url() -> str:
    url = integration_env("INTEGRATION_DATABASE_URL")
    if not url:
        pytest.skip("INTEGRATION_DATABASE_URL not set")
    return url


@pytest.fixture
async def schema_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine bound to a fresh, empty schema (dropped afterwards)."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(database_url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
async def migrated_engine(schema_engine: AsyncEngine) -> AsyncEngine:
    async with schema_engine.begin() as conn:
        await conn.run_sync(run_alembic, "upgrade", "head")
    return schema_engine
