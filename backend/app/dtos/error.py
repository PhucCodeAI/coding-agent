"""Đặc tả một mã lỗi (tiêu đề, thông báo, cách xử lý) — dùng chung cho API, SSE và UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ErrorScope = Literal["general", "pipeline", "session", "llm", "workspace", "sandbox", "git", "trace", "client"]


@dataclass(frozen=True)
class ErrorSpec:
    """Đặc tả hiển thị của một mã lỗi."""

    code: str
    scope: ErrorScope
    http_status: int
    title: str
    message: str
    hint: str
    retryable: bool

    def to_dict(self) -> dict[str, object]:
        """Serialize để trả qua API.

        Returns:
            Dict các field của spec.
        """
        return asdict(self)


__all__ = ["ErrorScope", "ErrorSpec"]
