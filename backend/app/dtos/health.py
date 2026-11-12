"""Kết quả health check của backend."""

from __future__ import annotations

from pydantic import BaseModel


class HealthStatus(BaseModel):
    """Schema response cho health check."""

    status: str
    version: str = "0.0.0"


__all__ = ["HealthStatus"]
