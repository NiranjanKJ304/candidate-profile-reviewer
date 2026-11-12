"""Shared fixtures and test doubles."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.cache.redis import get_redis
from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.main import create_app

TEST_DATABASE_URL = "postgresql+asyncpg://tester:s3cr3t-pw@db.internal:5432/candidates"
TEST_REDIS_URL = "redis://:r3dis-pw@cache.internal:6379/0"

_SETTINGS_ENV_VARS = (
    "APP_NAME",
    "ENVIRONMENT",
    "DEBUG",
    "API_V1_PREFIX",
    "LOG_LEVEL",
    "LOG_JSON",
    "CORS_ORIGINS",
    "DATABASE_URL",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_CONNECT_TIMEOUT_SECONDS",
    "DB_ECHO",
    "REDIS_URL",
    "REDIS_SOCKET_TIMEOUT_SECONDS",
    "READINESS_TIMEOUT_SECONDS",
    "ENABLED_PLATFORMS",
    "LLM_PROVIDER",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_TIMEOUT_SECONDS",
    "RESUME_STORAGE_DIR",
    "RESUME_MAX_BYTES",
    "OCR_ENABLED",
    "OCR_TESSDATA_DIR",
)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent the developer's shell environment from leaking into unit tests."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


SettingsFactory = Callable[..., Settings]


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": TEST_DATABASE_URL,
            "redis_url": TEST_REDIS_URL,
            "environment": "test",
            "log_json": True,
            "readiness_timeout_seconds": 0.5,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


# --------------------------------------------------------------------------- fakes
class FakeConnection:
    def __init__(self, delay: float, error: BaseException | None) -> None:
        self._delay = delay
        self._error = error

    async def execute(self, _statement: Any) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error


class FakeEngine:
    """Stands in for ``AsyncEngine`` in the PostgreSQL probe."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        execute_error: BaseException | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self._delay = delay
        self._execute_error = execute_error
        self._connect_error = connect_error
        self.connect_calls = 0

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[FakeConnection]:
        self.connect_calls += 1
        if self._connect_error is not None:
            raise self._connect_error
        yield FakeConnection(self._delay, self._execute_error)


class FakeRedis:
    """Stands in for ``redis.asyncio.Redis`` in the Redis probe."""

    def __init__(
        self, *, delay: float = 0.0, error: BaseException | None = None, reply: Any = True
    ) -> None:
        self._delay = delay
        self._error = error
        self._reply = reply

    async def ping(self) -> Any:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._reply


# --------------------------------------------------------------------------- app/client
AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(make_settings: SettingsFactory) -> AppFactory:
    """Build an app whose dependencies are replaced by fakes (no lifespan needed)."""

    def factory(
        *,
        engine: FakeEngine | None = None,
        redis: FakeRedis | None = None,
        **settings_overrides: Any,
    ) -> FastAPI:
        app = create_app(make_settings(**settings_overrides))
        fake_engine = engine or FakeEngine()
        fake_redis = redis or FakeRedis()
        app.dependency_overrides[get_engine] = lambda: fake_engine
        app.dependency_overrides[get_redis] = lambda: fake_redis
        return app

    return factory


@asynccontextmanager
async def client_for(
    app: FastAPI, raise_app_exceptions: bool = True
) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def client(make_app: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(make_app()) as c:
        yield c


def integration_env(name: str) -> str | None:
    return os.environ.get(name) or None
