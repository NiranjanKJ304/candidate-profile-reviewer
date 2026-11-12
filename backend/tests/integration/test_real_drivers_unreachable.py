"""Real asyncpg / redis drivers and the real app lifespan against closed ports.

These run without Docker: they prove that genuine driver failures (not mocks)
are converted into a 503 readiness response rather than a crash.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.cache.redis import create_redis
from app.schemas.common import CheckStatus
from app.services.health import check_postgres, check_redis
from tests.conftest import SettingsFactory


def _closed_port() -> int:
    """Return a localhost port that nothing is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest.fixture
def unreachable(make_settings: SettingsFactory) -> object:
    return make_settings(
        database_url=f"postgresql+asyncpg://u:pw@127.0.0.1:{_closed_port()}/db",
        redis_url=f"redis://:pw@127.0.0.1:{_closed_port()}/0",
        readiness_timeout_seconds=3,
        db_connect_timeout_seconds=2,
        redis_socket_timeout_seconds=2,
    )


async def test_real_asyncpg_unreachable() -> None:
    engine = create_async_engine(f"postgresql+asyncpg://u:pw@127.0.0.1:{_closed_port()}/db")
    try:
        result = await check_postgres(engine, timeout_s=3)
    finally:
        await engine.dispose()
    assert result.status in (CheckStatus.ERROR, CheckStatus.TIMEOUT)
    assert result.latency_ms is None


async def test_real_redis_unreachable(make_settings: SettingsFactory) -> None:
    client = create_redis(
        make_settings(
            redis_url=f"redis://127.0.0.1:{_closed_port()}/0", redis_socket_timeout_seconds=2
        )
    )
    try:
        result = await check_redis(client, timeout_s=3)
    finally:
        await client.aclose()
    assert result.status in (CheckStatus.ERROR, CheckStatus.TIMEOUT)


def test_full_lifespan_starts_and_reports_not_ready(unreachable: object) -> None:
    from app.core.config import Settings
    from app.main import create_app

    assert isinstance(unreachable, Settings)
    with TestClient(create_app(unreachable)) as client:  # runs startup + shutdown
        health = client.get("/api/v1/health")
        ready = client.get("/api/v1/ready")
    assert health.status_code == 200
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"]["status"] in ("error", "timeout")
    assert body["checks"]["redis"]["status"] in ("error", "timeout")
    assert "pw@" not in ready.text
