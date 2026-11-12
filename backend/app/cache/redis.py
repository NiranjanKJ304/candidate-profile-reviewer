"""Async Redis client.

``Redis.from_url`` is lazy: no connection is made until the first command, so the
API can start while Redis is unavailable.
"""

from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    client: Redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        health_check_interval=30,
    )
    return client


def get_redis(request: Request) -> Redis:
    """FastAPI dependency returning the application's Redis client."""
    client: Redis = request.app.state.redis
    return client
