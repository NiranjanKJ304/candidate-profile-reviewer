"""Alembic environment using the async engine and application settings.

Supported invocations:

* CLI (``alembic upgrade head``): URL from application settings (DATABASE_URL).
* Programmatic with ``config.attributes["connection"]``: runs on that existing
  synchronous connection (used by tests to migrate an isolated schema).
* Programmatic with ``config.attributes["database_url"]``: overrides the URL
  (used for offline SQL generation in tests without real settings).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.attributes.get("database_url")
    if isinstance(override, str):
        return override
    from app.core.config import get_settings

    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Emit SQL without a database connection (``alembic upgrade head --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    existing = config.attributes.get("connection")
    if isinstance(existing, Connection):
        _do_run_migrations(existing)
    else:
        asyncio.run(run_migrations_online())
