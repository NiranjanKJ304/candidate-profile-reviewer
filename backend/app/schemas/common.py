"""Shared response schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Machine-readable error body."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["Resource not found"])
    details: list[dict[str, Any]] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Standard error envelope returned by every failing endpoint."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


class HealthResponse(BaseModel):
    """Liveness response: the process is up and serving requests."""

    status: str = Field(examples=["ok"])
    service: str
    version: str
    environment: str
    timestamp: datetime


class CheckStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class DependencyCheck(BaseModel):
    """Result of probing one backing service."""

    status: CheckStatus
    latency_ms: float | None = Field(
        default=None, description="Probe duration; null when the probe did not complete."
    )
    error: str | None = Field(
        default=None,
        description="Sanitised failure reason (exception type only, never credentials).",
    )


class ReadinessStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class ReadinessResponse(BaseModel):
    """Readiness response: whether all backing services are reachable."""

    status: ReadinessStatus
    checks: dict[str, DependencyCheck]
    timestamp: datetime
