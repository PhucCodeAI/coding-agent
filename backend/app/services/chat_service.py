"""Chat: 1 request = 1 lượt — khóa theo phiên, mở phiên, chạy agent, ghi lại sự kiện, lưu trace + lịch sử.

1. khóa phiên (1 phiên = 1 workspace + 1 git repo, không chạy song song)       → `error` SESSION_BUSY nếu đang chạy
2. tạo phiên nếu chưa có, lấy lịch sử, lưu prompt                                → event `session`
3. chạy Agent, yield và ghi lại từng sự kiện
4. kết quả không thành công                                                      → event `error`
5. lưu trace + lịch sử trong task riêng (kể cả khi client ngắt)                   → event `done`
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from time import perf_counter
from typing import Any

from loguru import logger

from app.config import tool_configs
from app.dtos.agent import AgentContext
from app.dtos.events import ChatRequest, DoneEventPayload, SessionEventPayload
from app.errors import AppError, error_payload, error_payload_from_exc
from app.repositories.workspace import Workspace
from app.services.session_service import SessionService, new_session_id, title_from_prompt
from app.services.trace_service import TraceCollector, TraceService
from app.utils.sse import format_sse, parse_sse
from app.workflow.agent import Agent
from app.workflow.questions import QuestionBroker


class ChatService:
    """Chạy lượt chat và quản lý các lượt đang chạy."""

    def __init__(self, llm: Any, traces: Any, sessions: SessionService | None, workspace_base: str) -> None:
        """Tạo service.

        Args:
            llm: LLMClient (hoặc bản giả có cùng `chat()`).
            traces: TraceService (hoặc bản giả có `create_collector` + `save`).
            sessions: SessionService; None = không lưu phiên (chạy offline).
            workspace_base: Thư mục chứa workspace các phiên.
        """
        self.llm = llm
        self.traces: TraceService = traces
        self.sessions = sessions
        self.workspace_base = Path(workspace_base)
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.agent_cfg = tool_configs.agent
        self.questions = QuestionBroker()
        self._locks: dict[str, asyncio.Lock] = {}
        self._live: dict[str, TraceCollector] = {}
        self._background: set[asyncio.Task[None]] = set()

    def is_busy(self, session_id: str) -> bool:
        """Phiên có đang chạy lượt nào không.

        Args:
            session_id: Mã phiên.

        Returns:
            True nếu đang chạy.
        """
        lock = self._locks.get(session_id)
        return lock is not None and lock.locked()

    def live_trace(self, session_id: str) -> Any | None:
        """Trace của lượt đang chạy trong phiên (None nếu phiên đang rảnh).

        Args:
            session_id: Mã phiên.

        Returns:
            LiveTraceDTO hoặc None.
        """
        collector = self._live.get(session_id)
        return collector.live() if collector else None

    async def stream(self, req: ChatRequest) -> AsyncGenerator[str, None]:
        """Chạy 1 lượt và stream SSE; event đầu luôn là `session`, cuối là `done`.

        Args:
            req: Yêu cầu (không có session_id → tạo phiên mới).

        Yields:
            Chuỗi SSE.
        """
        session_id = req.session_id or new_session_id()
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            yield format_sse("error", error_payload("SESSION_BUSY", context={"session_id": session_id}))
            yield format_sse("done", DoneEventPayload(status="FAILED", duration_ms=0))
            return
        async with lock:
            async for sse in self._run(req.prompt, session_id):
                yield sse

    async def _run(self, prompt: str, session_id: str) -> AsyncGenerator[str, None]:
        """Thân 1 lượt (đã giữ khóa phiên).

        Args:
            prompt: Yêu cầu.
            session_id: Mã phiên.

        Yields:
            Chuỗi SSE.
        """
        start = perf_counter()
        title, history = await self._open(session_id, prompt)
        yield format_sse("session", SessionEventPayload(session_id=session_id, title=title))
        workspace_dir = self.workspace_base / session_id
        collector = self.traces.create_collector(session_id, prompt)
        context = AgentContext(
            session_id=session_id, prompt=prompt, workspace_dir=workspace_dir, workspace=Workspace(workspace_dir), collector=collector, history=history
        )
        agent = Agent(context, self.llm, self.questions, self.agent_cfg)
        self._live[session_id] = collector
        events: list[dict[str, Any]] = []

        def record(sse: str) -> str:
            """Ghi lại sự kiện để lưu vào lịch sử phiên, trả nguyên chuỗi SSE."""
            name, data = parse_sse(sse)
            if name:
                events.append({"event": name, "data": data})
            return sse

        status, error = "FAILED", None
        cancelled = False
        try:
            async for sse in agent.run():
                yield record(sse)
            outcome = agent.outcome
            if outcome and outcome.status == "SUCCESS":
                status = "SUCCESS"
            else:
                code = (outcome.error_code if outcome else None) or "AGENT_INCOMPLETE"
                error = f"[{code}] {outcome.text if outcome else 'Không rõ kết quả.'}"
                yield record(format_sse("error", error_payload(code, detail=outcome.text if outcome else None)))
        except asyncio.CancelledError:
            cancelled, status, error = True, "CANCELLED", "[CANCELLED] Client ngắt kết nối"
            raise
        except Exception as e:
            error = f"[{getattr(e, 'code', 'SYSTEM_ERROR')}] {getattr(e, 'message', str(e))}"
            logger.exception("Lượt chat lỗi")
            yield record(format_sse("error", error_payload_from_exc(e)))
        finally:
            self._live.pop(session_id, None)
            duration_ms = int((perf_counter() - start) * 1000)
            m = context.metrics
            done = DoneEventPayload(
                status=status,
                duration_ms=duration_ms,
                trace_id=collector.trace_id,
                cost=m["cost"],
                **{k: int(m[k]) for k in ("total_tokens", "input_tokens", "output_tokens", "reasoning_tokens", "cache_tokens")},
            )
            if not cancelled:
                events.append({"event": "done", "data": done.model_dump()})
            await self._save_in_background(session_id, collector, status, duration_ms, m, error, events)
        if not cancelled:
            yield format_sse("done", done)

    async def _open(self, session_id: str, prompt: str) -> tuple[str, list[dict[str, str]]]:
        """Tạo/đọc phiên, lưu prompt, lấy lịch sử (lỗi database không chặn lượt chạy).

        Args:
            session_id: Mã phiên.
            prompt: Yêu cầu.

        Returns:
            (tiêu đề, lịch sử).
        """
        if self.sessions is None:
            return title_from_prompt(prompt), []
        try:
            return await self.sessions.start_turn(session_id, prompt, self.agent_cfg.history_turns)
        except AppError as e:
            logger.warning("Không lưu được phiên {}: [{}] {}", session_id, e.code, e.message)
            return title_from_prompt(prompt), []

    async def _save_in_background(
        self,
        session_id: str,
        collector: TraceCollector,
        status: str,
        duration_ms: int,
        metrics: dict[str, float],
        error: str | None,
        events: list[dict[str, Any]],
    ) -> None:
        """Lưu trace + lịch sử trong task riêng và chờ (shield): client ngắt kết nối thì phần lưu vẫn chạy nốt.

        Args:
            session_id: Mã phiên.
            collector: Collector của lượt.
            status: Trạng thái cuối.
            duration_ms: Thời gian.
            metrics: Token và chi phí.
            error: Lỗi dạng "[CODE] chi tiết".
            events: Sự kiện của lượt.
        """

        async def save() -> None:
            """Lưu trace rồi lịch sử; lỗi chỉ ghi log."""
            try:
                await self.traces.save(collector, status, duration_ms, metrics, error)
            except Exception as e:  # noqa: BLE001
                logger.error("Lưu trace {} thất bại: {!r}", collector.trace_id, e)
            if self.sessions is not None:
                try:
                    await self.sessions.finish_turn(session_id, events, status, collector.trace_id, duration_ms, metrics)
                except Exception as e:  # noqa: BLE001
                    logger.error("Lưu lịch sử phiên {} thất bại: {!r}", session_id, e)

        task = asyncio.create_task(save())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        await asyncio.shield(task)


__all__ = ["ChatService"]
