"""Shared response schemas."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HealthResponse(BaseModel):
    """Returned by the health check endpoint."""

    status: Literal["ok", "degraded"] = "ok"
    service: str = Field(..., examples=["SettleSherlock"])
    version: str = Field(..., examples=["0.1.0"])
    timestamp: datetime = Field(default_factory=_utcnow)


class RootResponse(BaseModel):
    """Returned by the API root endpoint."""

    service: str
    version: str
    description: str
    docs_url: str


class ErrorResponse(BaseModel):
    """Uniform error envelope for handled failures."""

    detail: str
    status_code: int
