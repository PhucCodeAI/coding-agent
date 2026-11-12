import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.dtos.events import ChatRequest
from app.services.chat_service import ChatService
from app.services.trace_service import TraceCollector
from app.utils.sse import parse_sse

HAS_DOCKER = shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


class StubTracer:
    """Trace service giả: thu span trong bộ nhớ, không lưu Supabase."""

    def create_collector(self, session_id: str, prompt: str) -> TraceCollector:
        """Tạo collector trong bộ nhớ.

        Args:
            session_id: Mã phiên.
            prompt: Yêu cầu.

        Returns:
            Collector.
        """
        return TraceCollector(session_id, prompt)

    async def save(self, *args: Any) -> None:
        """Bỏ qua lưu trace.

        Args:
            *args: Tham số của TraceService.save.
        """


class ScriptedLLM:
    """LLM giả: mỗi lời gọi trả lượt kế tiếp của kịch bản (danh sách tool call), 0 token."""

    providers: list[Any] = []

    def __init__(self, *turns: list[dict[str, Any]]) -> None:
        """Tạo LLM giả.

        Args:
            *turns: Danh sách tool call của mỗi lượt; hết kịch bản thì lặp lại lượt cuối.
        """
        self.turns = list(turns)
        self.calls = 0
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, reasoning: str | None = None) -> dict[str, Any]:
        """Trả lượt kế tiếp.

        Args:
            messages: Hội thoại (không dùng).
            tools: Schema công cụ (không dùng).
            reasoning: Mức suy luận (không dùng).

        Returns:
            Tin nhắn + thông tin token giả.
        """
        self.seen.append(messages)
        calls = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return {"message": {"content": "", "tool_calls": calls}, "info": {"input_token": 100, "output_token": 10, "total_token": 110, "model": "scripted"}}

    async def aclose(self) -> None:
        """Không có gì để đóng."""


def call(name: str, **args: Any) -> dict[str, Any]:
    """Tạo 1 lời gọi công cụ.

    Args:
        name: Tên công cụ.
        **args: Tham số.

    Returns:
        Tool call theo định dạng của LLMClient.
    """
    return {"id": f"c_{name}", "name": name, "arguments": args}


FINISH = [call("finish", status="done", answer="xong")]


async def run_turn(workspace_base: Path, llm: ScriptedLLM, prompt: str = "viết calc", **agent_cfg: Any) -> list[tuple[str, dict[str, Any]]]:
    """Chạy 1 lượt chat và trả các sự kiện SSE đã parse.

    Args:
        workspace_base: Thư mục chứa workspace.
        llm: LLM giả.
        prompt: Yêu cầu.
        **agent_cfg: Ghi đè `[agent]`.

    Returns:
        Danh sách (tên sự kiện, dữ liệu).
    """
    runner = ChatService(llm, StubTracer(), None, str(workspace_base))
    runner.agent_cfg = runner.agent_cfg.model_copy(update=agent_cfg)
    events = []
    async for raw in runner.stream(ChatRequest(prompt=prompt, session_id="ses_test")):
        name, data = parse_sse(raw)
        if name:
            events.append((name, data))
    return events


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """Thư mục chứa workspace của test.

    Args:
        tmp_path: Thư mục tạm của pytest.

    Returns:
        Đường dẫn.
    """
    return tmp_path
