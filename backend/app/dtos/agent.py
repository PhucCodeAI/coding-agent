"""Dữ liệu của 1 lượt làm việc: trạng thái agent và kết quả cuối. Không chứa logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.repositories.workspace import Workspace
    from app.services.trace_service import TraceCollector


@dataclass
class AgentState:
    """Trạng thái của agent trong 1 lượt (1 hội thoại với LLM).

    Attributes:
        steps: Số lời gọi LLM đã dùng.
        fail_streak: Số lệnh công cụ lỗi liên tiếp.
        last_errors: Tối đa 3 lỗi công cụ gần nhất (báo kèm khi dừng vì lỗi lặp).
        call_ids: Id các lệnh gọi công cụ đã dùng (đảm bảo duy nhất).
        output_errors: Số lượt liên tiếp output model hỏng (quá dài / tool call sai).
        read_versions: Đường dẫn → phiên bản file lúc agent đọc (chặn ghi đè file chưa đọc).
        last_log: Log đầy đủ của lệnh sandbox gần nhất (cho `read_log`).
        last_provider: Nhà cung cấp LLM trả lời lời gọi gần nhất (để hạ ưu tiên khi output hỏng).
    """

    steps: int = 0
    fail_streak: int = 0
    last_errors: list[str] = field(default_factory=list)
    call_ids: set[str] = field(default_factory=set)
    output_errors: int = 0
    read_versions: dict[str, int] = field(default_factory=dict)
    last_log: str = ""
    last_provider: str = ""


@dataclass
class Outcome:
    """Kết quả cuối của lượt.

    Attributes:
        status: SUCCESS | FAILED.
        text: Câu trả lời cho người dùng.
        error_code: Mã lỗi khi FAILED.
        output: Dữ liệu thêm (status gốc, commit_hash...).
    """

    status: str
    text: str
    error_code: str | None = None
    output: dict[str, Any] = field(default_factory=dict)

    @property
    def error(self) -> str | None:
        """Nội dung lỗi (None khi thành công)."""
        return None if self.status == "SUCCESS" else self.text


@dataclass
class AgentContext:
    """Dữ liệu của 1 lượt chat, tạo ở đầu lượt; agent và công cụ dùng chung.

    Attributes:
        session_id: Mã phiên.
        prompt: Yêu cầu của người dùng.
        workspace_dir: Thư mục workspace.
        workspace: Đọc/ghi file trong workspace.
        collector: Trace collector của lượt.
        history: Các lượt trước `{role, content}` (cũ → mới).
        metrics: Token và chi phí cộng dồn.
    """

    session_id: str
    prompt: str
    workspace_dir: Path
    workspace: Workspace
    collector: TraceCollector
    history: list[dict[str, str]] = field(default_factory=list)
    metrics: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(("input_tokens", "output_tokens", "reasoning_tokens", "cache_tokens", "total_tokens", "cost"), 0.0)
    )

    def add_usage(self, info: dict[str, Any]) -> None:
        """Cộng token/chi phí của 1 lời gọi LLM.

        Args:
            info: `info` của LLMClient.chat.
        """
        for metric, key in (
            ("input_tokens", "input_token"),
            ("output_tokens", "output_token"),
            ("reasoning_tokens", "reasoning_token"),
            ("cache_tokens", "cached_token"),
            ("total_tokens", "total_token"),
            ("cost", "cost"),
        ):
            self.metrics[metric] += float(info.get(key) or 0)


__all__ = ["AgentContext", "AgentState", "Outcome"]
