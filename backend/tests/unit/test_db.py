"""Declarative base conventions and session/engine factories (no live database)."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from sqlalchemy import ForeignKey, MetaData, String, UniqueConstraint
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import CreateTable

from app.db.base import NAMING_CONVENTION, Base, TimestampMixin
from app.db.session import create_engine, create_session_factory, get_session
from tests.conftest import SettingsFactory, client_for


class _TestBase(DeclarativeBase):
    """Separate metadata (same convention) so these throwaway tables never leak into
    the application's ``Base.metadata`` and show up as migration drift."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class _Parent(_TestBase):
    __tablename__ = "phase0_test_parent"
    id: Mapped[int] = mapped_column(primary_key=True)


class _Child(TimestampMixin, _TestBase):
    __tablename__ = "phase0_test_child"
    __table_args__ = (UniqueConstraint("parent_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("phase0_test_parent.id"))


def _ddl(model: type[_TestBase]) -> str:
    table = _TestBase.metadata.tables[model.__tablename__]
    return str(CreateTable(table).compile(dialect=PGDialect()))  # type: ignore[no-untyped-call]


def test_naming_convention_applied() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    ddl = _ddl(_Child)
    assert "CONSTRAINT pk_phase0_test_child PRIMARY KEY" in ddl
    assert "CONSTRAINT uq_phase0_test_child_code UNIQUE" in ddl
    # Composite unique constraints include every column in the name.
    assert "CONSTRAINT uq_phase0_test_child_parent_id_code UNIQUE" in ddl
    assert "CONSTRAINT fk_phase0_test_child_parent_id_phase0_test_parent" in ddl


def test_test_tables_do_not_leak_into_application_metadata() -> None:
    assert "phase0_test_child" not in Base.metadata.tables
    assert "phase0_test_parent" not in Base.metadata.tables


def test_timestamp_mixin_columns_are_timezone_aware_and_server_defaulted() -> None:
    ddl = _ddl(_Child)
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl


async def test_engine_factory_is_lazy_and_hides_parameters(make_settings: SettingsFactory) -> None:
    engine = create_engine(make_settings())  # unreachable host: must not connect
    try:
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.sync_engine.hide_parameters is True
        assert "s3cr3t-pw" not in repr(engine.url)
    finally:
        await engine.dispose()


async def test_get_session_dependency_yields_and_closes(make_settings: SettingsFactory) -> None:
    engine = create_engine(make_settings())
    app = FastAPI()
    app.state.session_factory = create_session_factory(engine)
    seen: list[AsyncSession] = []

    @app.get("/s")
    async def route(session: AsyncSession = Depends(get_session)) -> dict[str, bool]:  # noqa: B008
        seen.append(session)
        return {"is_session": isinstance(session, AsyncSession)}

    try:
        async with client_for(app) as client:
            resp = await client.get("/s")
    finally:
        await engine.dispose()
    assert resp.json() == {"is_session": True}
    assert len(seen) == 1
    assert not seen[0].in_transaction()
