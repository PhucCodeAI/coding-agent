"""Kết quả, ngữ cảnh và tham số của công cụ agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.workflow.agent import Agent


@dataclass
class ToolOutput:
    """Kết quả 1 lần gọi công cụ."""

    text: str
    ok: bool = True
    stop: bool = False


@dataclass(frozen=True)
class ToolContext:
    """Ngữ cảnh 1 lần gọi công cụ: agent đang chạy (trạng thái ở `agent.state`)."""

    agent: Agent


@dataclass(frozen=True)
class Param:
    """1 tham số của công cụ (suy ra từ chữ ký + docstring)."""

    name: str
    type: Any
    required: bool
    default: Any
    doc: str

    @property
    def usage(self) -> str:
        """Chữ ký ngắn của tham số (VD `path` / `limit=10` / `prefix?`)."""
        if self.required:
            return self.name
        if self.default in (None, "", 0):
            return f"{self.name}?"
        return f"{self.name}={self.default!r}"


__all__ = ["Param", "ToolContext", "ToolOutput"]
