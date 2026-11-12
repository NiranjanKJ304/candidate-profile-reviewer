"""Dependency probes for the readiness endpoint.

Each probe is bounded by a timeout and never raises: failures are reported as a
:class:`DependencyCheck` with a sanitised error (the exception type only), so
connection strings and hostnames are never leaked to clients.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import text

from app.schemas.common import CheckStatus, DependencyCheck

logger = logging.getLogger("app.health")


class SupportsConnect(Protocol):
    """The subset of ``AsyncEngine`` used by the PostgreSQL probe."""

    def connect(self) -> Any: ...


class SupportsPing(Protocol):
    """The subset of the Redis client used by the Redis probe."""

    def ping(self) -> Awaitable[Any]: ...


_MAX_CAUSE_DEPTH = 5


def root_cause_name(exc: BaseException) -> str:
    """Return the type name of the most specific underlying exception.

    SQLAlchemy wraps driver errors (``DBAPIError`` -> ``.orig`` adapter error ->
    ``__cause__`` asyncpg error), so the outer type alone would hide whether the
    failure was a bad password, a missing database or a refused connection. Only
    the type name is returned, never the message.
    """
    current: BaseException = exc
    orig = getattr(current, "orig", None)
    if isinstance(orig, BaseException):
        current = orig
    for _ in range(_MAX_CAUSE_DEPTH):
        if current.__cause__ is None:
            break
        current = current.__cause__
    return type(current).__name__


async def _probe(
    name: str, call: Callable[[], Awaitable[None]], timeout_s: float
) -> DependencyCheck:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s):
            await call()
    except TimeoutError:
        logger.warning(
            "readiness probe timed out", extra={"dependency": name, "timeout_s": timeout_s}
        )
        return DependencyCheck(status=CheckStatus.TIMEOUT, error=f"timed out after {timeout_s}s")
    except Exception as exc:
        error_type = root_cause_name(exc)
        logger.warning(
            "readiness probe failed",
            extra={"dependency": name, "error_type": error_type},
        )
        return DependencyCheck(status=CheckStatus.ERROR, error=error_type)
    latency = round((time.perf_counter() - start) * 1000, 2)
    return DependencyCheck(status=CheckStatus.OK, latency_ms=latency)


async def check_postgres(engine: SupportsConnect, timeout_s: float) -> DependencyCheck:
    async def run() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return await _probe("postgres", run, timeout_s)


async def check_redis(client: SupportsPing, timeout_s: float) -> DependencyCheck:
    async def run() -> None:
        if not await client.ping():
            raise ConnectionError("unexpected PING reply")

    return await _probe("redis", run, timeout_s)
