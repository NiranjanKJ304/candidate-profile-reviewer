"""Live PostgreSQL + Redis integration tests against natively installed services.

No Docker is involved. Locally this project uses the PostgreSQL Windows service
(port 5432) and Memurai, a Windows-native Redis-compatible server (port 6379).

Run all live tests with the helper (reads backend/.env)::

    powershell -ExecutionPolicy Bypass -File scripts\\integration-test.ps1

or manually::

    $env:INTEGRATION_DATABASE_URL = "postgresql+asyncpg://candidate_app:<pw>@127.0.0.1:5432/candidate_intel"
    $env:INTEGRATION_REDIS_URL    = "redis://127.0.0.1:6379/0"
    uv run pytest -m integration

Each URL is independent: PostgreSQL tests skip only when INTEGRATION_DATABASE_URL is
unset, Redis tests only when INTEGRATION_REDIS_URL is unset. If a URL is set but the
service is unreachable, the tests fail.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.cache.redis import create_redis
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app
from app.schemas.common import CheckStatus
from app.services.health import check_postgres, check_redis
from tests.conftest import SettingsFactory, integration_env

pytestmark = pytest.mark.integration


def _require(name: str) -> str:
    value = integration_env(name)
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture
def pg_settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(database_url=_require("INTEGRATION_DATABASE_URL"))


@pytest.fixture
def redis_settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(redis_url=_require("INTEGRATION_REDIS_URL"))


@pytest.fixture
def full_settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(
        database_url=_require("INTEGRATION_DATABASE_URL"),
        redis_url=_require("INTEGRATION_REDIS_URL"),
        readiness_timeout_seconds=5,
    )


# --------------------------------------------------------------------------- PostgreSQL
async def test_postgres_probe_ok(pg_settings: Settings) -> None:
    engine = create_engine(pg_settings)
    try:
        result = await check_postgres(engine, timeout_s=5)
        async with engine.connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
            user = (await conn.execute(text("SELECT current_user"))).scalar_one()
            is_super = (
                await conn.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert result.status is CheckStatus.OK, result
    assert version
    assert user
    # The application must not connect as a superuser.
    assert is_super is False


async def test_postgres_wrong_password_is_reported(
    pg_settings: Settings, make_settings: SettingsFactory
) -> None:
    parts = urlsplit(pg_settings.database_url.get_secret_value())
    host = parts.netloc.rsplit("@", 1)[-1]
    user = (parts.username or "postgres").split(":", 1)[0]
    bad_url = urlunsplit(parts._replace(netloc=f"{user}:definitely-wrong@{host}"))
    engine = create_engine(make_settings(database_url=bad_url))
    try:
        result = await check_postgres(engine, timeout_s=5)
    finally:
        await engine.dispose()
    assert result.status is CheckStatus.ERROR
    assert result.error == "InvalidPasswordError"
    assert "definitely-wrong" not in result.model_dump_json()


# --------------------------------------------------------------------------- Redis
async def test_redis_probe_ok_and_roundtrip(redis_settings: Settings) -> None:
    client = create_redis(redis_settings)
    key = "candidate-intel:phase0:integration"
    try:
        result = await check_redis(client, timeout_s=5)
        await client.set(key, "ok", ex=30)
        value = await client.get(key)
        ttl = await client.ttl(key)
        await client.delete(key)
        after_delete = await client.get(key)
    finally:
        await client.aclose()
    assert result.status is CheckStatus.OK, result
    assert value == "ok"
    assert 0 < ttl <= 30
    assert after_delete is None


async def test_redis_bad_credentials_are_reported(
    redis_settings: Settings, make_settings: SettingsFactory
) -> None:
    """Wrong password (or a password sent to a server without auth) is an error, not a crash."""
    parts = urlsplit(redis_settings.redis_url.get_secret_value())
    host = parts.netloc.rsplit("@", 1)[-1]
    bad_url = urlunsplit(parts._replace(netloc=f"wrong-user:definitely-wrong@{host}"))
    client = create_redis(make_settings(redis_url=bad_url))
    try:
        result = await check_redis(client, timeout_s=5)
    finally:
        await client.aclose()
    assert result.status is CheckStatus.ERROR
    assert result.error is not None
    assert "definitely-wrong" not in result.model_dump_json()


# --------------------------------------------------------------------------- end to end
def test_ready_endpoint_with_real_lifespan(full_settings: Settings) -> None:
    with TestClient(create_app(full_settings)) as client:
        resp = client.get("/api/v1/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"
