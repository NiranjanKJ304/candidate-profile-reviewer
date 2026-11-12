"""Dependency probes in isolation (success, failure, timeout, edge cases)."""

from __future__ import annotations

import asyncpg
import pytest
from redis.exceptions import AuthenticationError
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import DBAPIError

from app.schemas.common import CheckStatus
from app.services.health import check_postgres, check_redis
from tests.conftest import FakeEngine, FakeRedis


class TestPostgresProbe:
    async def test_ok(self) -> None:
        engine = FakeEngine()
        result = await check_postgres(engine, timeout_s=1)
        assert result.status is CheckStatus.OK
        assert result.latency_ms is not None
        assert result.latency_ms >= 0
        assert result.error is None
        assert engine.connect_calls == 1

    async def test_connection_refused(self) -> None:
        result = await check_postgres(
            FakeEngine(connect_error=ConnectionRefusedError("refused 10.0.0.1")), timeout_s=1
        )
        assert result.status is CheckStatus.ERROR
        assert result.error == "ConnectionRefusedError"
        assert result.latency_ms is None

    async def test_invalid_password_does_not_leak_details(self) -> None:
        exc = asyncpg.InvalidPasswordError("password authentication failed for user 'admin'")
        result = await check_postgres(FakeEngine(connect_error=exc), timeout_s=1)
        assert result.status is CheckStatus.ERROR
        assert result.error == "InvalidPasswordError"
        assert "admin" not in result.model_dump_json()

    async def test_sqlalchemy_wrapped_driver_error_reports_root_cause(self) -> None:
        """Mirrors the real chain: DBAPIError -> .orig adapter error -> asyncpg error."""
        driver_error = asyncpg.InvalidPasswordError("password authentication failed for 'app'")
        adapter_error = RuntimeError("adapted")
        adapter_error.__cause__ = driver_error
        wrapped = DBAPIError("SELECT 1", None, adapter_error)
        result = await check_postgres(FakeEngine(connect_error=wrapped), timeout_s=1)
        assert result.status is CheckStatus.ERROR
        assert result.error == "InvalidPasswordError"
        assert "app" not in (result.error or "")

    async def test_query_failure(self) -> None:
        result = await check_postgres(FakeEngine(execute_error=RuntimeError("boom")), timeout_s=1)
        assert result.status is CheckStatus.ERROR
        assert result.error == "RuntimeError"

    async def test_timeout(self) -> None:
        result = await check_postgres(FakeEngine(delay=1.0), timeout_s=0.05)
        assert result.status is CheckStatus.TIMEOUT
        assert result.error is not None
        assert "0.05" in result.error


class TestRedisProbe:
    async def test_ok(self) -> None:
        result = await check_redis(FakeRedis(), timeout_s=1)
        assert result.status is CheckStatus.OK
        assert result.latency_ms is not None

    async def test_connection_error(self) -> None:
        result = await check_redis(FakeRedis(error=RedisConnectionError("nope")), timeout_s=1)
        assert result.status is CheckStatus.ERROR
        assert result.error == "ConnectionError"

    async def test_auth_error(self) -> None:
        result = await check_redis(FakeRedis(error=AuthenticationError("bad pw")), timeout_s=1)
        assert result.status is CheckStatus.ERROR
        assert result.error == "AuthenticationError"

    async def test_timeout(self) -> None:
        result = await check_redis(FakeRedis(delay=1.0), timeout_s=0.05)
        assert result.status is CheckStatus.TIMEOUT

    @pytest.mark.parametrize("reply", [False, None, ""])
    async def test_falsy_ping_reply_is_an_error(self, reply: object) -> None:
        result = await check_redis(FakeRedis(reply=reply), timeout_s=1)
        assert result.status is CheckStatus.ERROR
        assert result.error == "ConnectionError"

    async def test_cancellation_is_not_swallowed(self) -> None:
        import asyncio

        task = asyncio.create_task(check_redis(FakeRedis(delay=5), timeout_s=10))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
