"""Liveness and readiness endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.cache.redis import get_redis
from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.schemas.common import (
    CheckStatus,
    HealthResponse,
    ReadinessResponse,
    ReadinessStatus,
)
from app.services.health import check_postgres, check_redis

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 while the API process is running. Does not touch dependencies.",
)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Checks PostgreSQL and Redis concurrently, each bounded by "
        "READINESS_TIMEOUT_SECONDS. Returns 200 when every check is `ok`, otherwise 503 "
        "with the per-dependency status."
    ),
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ReadinessResponse:
    timeout = settings.readiness_timeout_seconds
    postgres_check, redis_check = await asyncio.gather(
        check_postgres(engine, timeout), check_redis(redis, timeout)
    )
    checks = {"postgres": postgres_check, "redis": redis_check}
    all_ok = all(c.status is CheckStatus.OK for c in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    response.headers["Cache-Control"] = "no-store"
    return ReadinessResponse(
        status=ReadinessStatus.READY if all_ok else ReadinessStatus.NOT_READY,
        checks=checks,
        timestamp=datetime.now(UTC),
    )
