"""Trace: thu span trong lúc agent chạy (TraceCollector) và lưu / đọc / xóa trên Supabase (TraceService).

Mỗi lượt chat = 1 trace. Lưu 2 tầng: toàn bộ span ở Storage (`<session_id>/<trace_id>.json`), tóm tắt ở bảng `traces`.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from loguru import logger

from app.config import configs
from app.dtos.trace import (
    LiveRunningSpanDTO,
    LiveTraceDTO,
    SpanDTO,
    SpanStatus,
    SpanType,
    TraceDetailDTO,
    TraceSessionDetailDTO,
    TraceSessionPageDTO,
    TraceSessionSummaryDTO,
    TraceTurnDTO,
)
from app.errors import AppError, TraceNotFoundError
from app.repositories.database import Database
from app.repositories.storage import Storage

_MAX_FIELD_BYTES = 32 * 1024
_ERROR_CODE = re.compile(r"^\[([A-Z][A-Z0-9_]*)\]\s*")
_AGGREGATE_LIMIT = 2000
_SUMMARY_COLUMNS = (
    "trace_id,session_id,prompt,status,duration_ms,total_tokens,input_tokens,output_tokens,"
    "reasoning_tokens,cache_tokens,cost,error_message,created_at,finished_at"
)
_TOKEN_KEYS = ("total_tokens", "input_tokens", "output_tokens", "reasoning_tokens", "cache_tokens")


def _truncate(value: Any) -> Any:
    """Cắt chuỗi / JSON quá `_MAX_FIELD_BYTES` (giữ đầu + cuối) để blob trace không phình.

    Args:
        value: Dữ liệu input/output của span.

    Returns:
        Dữ liệu đã cắt (hoặc nguyên bản).
    """
    if isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text.encode()) <= _MAX_FIELD_BYTES:
            return value
        value = text
    if isinstance(value, str) and len(value.encode()) > _MAX_FIELD_BYTES:
        half = _MAX_FIELD_BYTES // 2
        return value[:half] + "\n...[đã cắt]...\n" + value[-half:]
    return value


def extract_error_code(error_message: str | None) -> str | None:
    """Tách mã lỗi từ tiền tố "[CODE]" của error_message.

    Args:
        error_message: Chuỗi lỗi đã lưu (VD "[LLM_TIMEOUT] quá thời gian").

    Returns:
        Mã lỗi, hoặc None nếu không có tiền tố.
    """
    match = _ERROR_CODE.match(error_message or "")
    return match.group(1) if match else None


def aggregate_sessions(records: list[dict[str, Any]], titles: dict[str, str] | None = None) -> list[TraceSessionSummaryDTO]:
    """Gom các dòng trace theo phiên.

    Args:
        records: Dòng bảng traces (thứ tự bất kỳ).
        titles: session_id → tiêu đề từ bảng sessions; thiếu thì dùng prompt lượt đầu.

    Returns:
        Danh sách phiên, hoạt động gần nhất trước.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        groups.setdefault(str(r.get("session_id") or ""), []).append(r)
    out: list[TraceSessionSummaryDTO] = []
    for sid, rows in groups.items():
        rows.sort(key=lambda r: str(r.get("created_at") or ""))
        statuses = [r.get("status") for r in rows]
        sums = {k: sum(int(r.get(k) or 0) for r in rows) for k in (*_TOKEN_KEYS, "duration_ms")}
        out.append(
            TraceSessionSummaryDTO(
                session_id=sid,
                title=(titles or {}).get(sid) or str(rows[0].get("prompt") or ""),
                turn_count=len(rows),
                success_count=statuses.count("SUCCESS"),
                failed_count=statuses.count("FAILED"),
                cancelled_count=statuses.count("CANCELLED"),
                last_status=rows[-1].get("status"),
                last_trace_id=rows[-1].get("trace_id"),
                cost=sum(float(r.get("cost") or 0.0) for r in rows),
                started_at=str(rows[0].get("created_at") or ""),
                last_activity_at=max(str(r.get("finished_at") or r.get("created_at") or "") for r in rows),
                **sums,
            )
        )
    out.sort(key=lambda x: x.last_activity_at, reverse=True)
    return out


def filter_sessions(sessions: list[TraceSessionSummaryDTO], query: str | None = None, status: str | None = None) -> list[TraceSessionSummaryDTO]:
    """Lọc phiên theo từ khóa (tiêu đề / session_id, không phân biệt hoa thường) và trạng thái lượt cuối.

    Args:
        sessions: Phiên đã gom.
        query: Từ khóa; rỗng = không lọc.
        status: Trạng thái lượt cuối; rỗng = không lọc.

    Returns:
        Phiên thỏa điều kiện (giữ thứ tự).
    """
    q = (query or "").strip().lower()
    return [s for s in sessions if (not status or s.last_status == status) and (not q or q in s.title.lower() or q in s.session_id.lower())]


def to_turn(record: dict[str, Any], index: int) -> TraceTurnDTO:
    """Dòng trace của 1 lượt → TraceTurnDTO.

    Args:
        record: Dòng bảng traces.
        index: Thứ tự lượt (từ 1).

    Returns:
        Lượt.
    """
    return TraceTurnDTO(
        turn_index=index,
        trace_id=record["trace_id"],
        session_id=record["session_id"],
        prompt=record.get("prompt") or "",
        status=record.get("status") or "",
        duration_ms=int(record.get("duration_ms") or 0),
        cost=float(record.get("cost") or 0.0),
        created_at=str(record.get("created_at") or ""),
        finished_at=record.get("finished_at"),
        error_message=record.get("error_message"),
        error_code=extract_error_code(record.get("error_message")),
        **{k: int(record.get(k) or 0) for k in _TOKEN_KEYS},
    )


def _ms(a: datetime, b: datetime) -> int:
    """Khoảng cách (ms) giữa 2 thời điểm, không âm."""
    return max(0, int((b - a).total_seconds() * 1000))


class TraceCollector:
    """Span của 1 lượt đang chạy (trong bộ nhớ); lưu khi lượt kết thúc."""

    def __init__(self, session_id: str, prompt: str) -> None:
        """Tạo collector cho 1 lượt.

        Args:
            session_id: Mã phiên.
            prompt: Yêu cầu của người dùng.
        """
        self.trace_id = f"tr_{uuid.uuid4().hex[:12]}"
        self.session_id = session_id
        self.prompt = prompt
        self.started_at = datetime.now(UTC)
        self.spans: list[SpanDTO] = []
        self.running: dict[str, dict[str, Any]] = {}

    @contextmanager
    def track(self, name: str, span_type: str = "llm") -> Iterator[None]:
        """Đánh dấu 1 việc đang chạy (hiện ở trace live) và log bắt đầu / kết thúc.

        Args:
            name: Tên việc.
            span_type: Loại span.

        Yields:
            None.
        """
        key = uuid.uuid4().hex[:10]
        self.running[key] = {"name": name, "span_type": span_type, "started_at": datetime.now(UTC)}
        t0 = perf_counter()
        logger.info("▶ {} | session={}", name, self.session_id)
        try:
            yield
        except BaseException as e:
            logger.warning("✗ {} lỗi sau {:.1f}s: {}", name, perf_counter() - t0, type(e).__name__)
            raise
        else:
            logger.info("✓ {} xong sau {:.1f}s", name, perf_counter() - t0)
        finally:
            self.running.pop(key, None)

    def add_span(
        self,
        name: str,
        span_type: SpanType,
        duration_ms: int,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict[str, Any] | None = None,
        status: SpanStatus = "OK",
    ) -> SpanDTO:
        """Ghi 1 span vừa xong (thời điểm bắt đầu = bây giờ - thời lượng).

        Args:
            name: Tên.
            span_type: llm | tool | sandbox | git | internal.
            duration_ms: Thời lượng.
            input_data: Đầu vào.
            output_data: Đầu ra.
            metadata: Thông tin thêm (token, model...).
            status: OK | ERROR.

        Returns:
            Span vừa ghi.
        """
        span = SpanDTO(
            span_id=f"sp_{uuid.uuid4().hex[:8]}",
            name=name,
            span_type=span_type,
            duration_ms=duration_ms,
            start_offset_ms=max(0, _ms(self.started_at, datetime.now(UTC)) - duration_ms),
            status=status,
            input_data=_truncate(input_data),
            output_data=_truncate(output_data),
            metadata=metadata or {},
        )
        self.spans.append(span)
        return span

    def live(self) -> LiveTraceDTO:
        """Ảnh chụp trace đang chạy: span đã xong + việc đang chờ.

        Returns:
            Trace live.
        """
        now = datetime.now(UTC)
        return LiveTraceDTO(
            trace_id=self.trace_id,
            session_id=self.session_id,
            prompt=self.prompt,
            started_at=self.started_at,
            elapsed_ms=_ms(self.started_at, now),
            spans=list(self.spans),
            running=[
                LiveRunningSpanDTO(
                    name=r["name"], span_type=r["span_type"], start_offset_ms=_ms(self.started_at, r["started_at"]), elapsed_ms=_ms(r["started_at"], now)
                )
                for r in list(self.running.values())
            ],
        )


class TraceService:
    """Lưu / đọc / xóa trace."""

    def __init__(self, db: Database, storage: Storage) -> None:
        """Tạo service.

        Args:
            db: Repository database.
            storage: Repository object storage (bucket trace).
        """
        self.db = db
        self.storage = storage
        self.table = configs.database.table_name

    def create_collector(self, session_id: str, prompt: str) -> TraceCollector:
        """Tạo collector cho 1 lượt.

        Args:
            session_id: Mã phiên.
            prompt: Yêu cầu.

        Returns:
            Collector.
        """
        return TraceCollector(session_id, prompt)

    async def save(self, collector: TraceCollector, status: str, duration_ms: int, metrics: dict[str, float], error_message: str | None) -> None:
        """Lưu trace: blob span trước, dòng tóm tắt sau; ghi dòng lỗi thì xóa blob vừa ghi.

        Args:
            collector: Collector của lượt.
            status: SUCCESS | FAILED | CANCELLED.
            duration_ms: Tổng thời gian.
            metrics: Token và chi phí cộng dồn.
            error_message: Lỗi dạng "[CODE] chi tiết".
        """
        finished_at = datetime.now(UTC).isoformat()
        path = f"{collector.session_id}/{collector.trace_id}.json"
        await self.storage.write_json(
            path,
            {
                "trace_id": collector.trace_id,
                "session_id": collector.session_id,
                "prompt": collector.prompt,
                "status": status,
                "duration_ms": duration_ms,
                "metrics": metrics,
                "spans": [s.model_dump() for s in collector.spans],
                "error_message": error_message,
                "started_at": collector.started_at.isoformat(),
                "finished_at": finished_at,
            },
        )
        row = {
            "trace_id": collector.trace_id,
            "session_id": collector.session_id,
            "prompt": collector.prompt,
            "status": status,
            "duration_ms": duration_ms,
            "storage_path": path,
            "error_message": error_message,
            "finished_at": finished_at,
            "cost": float(metrics.get("cost") or 0.0),
            **{k: int(metrics.get(k) or 0) for k in ("total_tokens", "input_tokens", "output_tokens", "reasoning_tokens", "cache_tokens")},
        }
        try:
            await self.db.insert(self.table, row)
        except AppError:
            try:
                await self.storage.delete([path])
            except AppError as e:
                logger.critical("Không xóa được blob mồ côi {}: {}", path, e.message)
            raise
        logger.info("Đã lưu trace {} | {} | {}ms | {} token", collector.trace_id, status, duration_ms, row["total_tokens"])

    async def get(self, trace_id: str) -> TraceDetailDTO:
        """Trace 1 lượt kèm toàn bộ span.

        Args:
            trace_id: Mã trace.

        Returns:
            Trace chi tiết.

        Raises:
            TraceNotFoundError: Không có trace.
        """
        row = await self.db.get(self.table, {"trace_id": trace_id})
        if row is None:
            raise TraceNotFoundError(f"Không tìm thấy trace: {trace_id}", context={"trace_id": trace_id})
        blob = await self.storage.read_json(row["storage_path"]) if row.get("storage_path") else None
        return TraceDetailDTO(
            **{k: v for k, v in row.items() if k in TraceDetailDTO.model_fields and v is not None},
            error_code=extract_error_code(row.get("error_message")),
            spans=[SpanDTO(**s) for s in (blob or {}).get("spans", [])],
        )

    async def _titles(self, filters: dict[str, Any] | None = None) -> dict[str, str]:
        """Tiêu đề phiên từ bảng sessions.

        Args:
            filters: Điều kiện (VD 1 session_id); None = mọi phiên (tối đa `_AGGREGATE_LIMIT`).

        Returns:
            session_id → tiêu đề.
        """
        rows = await self.db.select(configs.database.sessions_table, filters, columns="session_id,title", limit=_AGGREGATE_LIMIT)
        return {str(r["session_id"]): str(r.get("title") or "") for r in rows}

    async def list_sessions(self, limit: int = 30, offset: int = 0, status: str | None = None, query: str | None = None) -> TraceSessionPageDTO:
        """Trang danh sách phiên có trace (gom bằng Python trên `_AGGREGATE_LIMIT` trace gần nhất).

        Args:
            limit: Số phiên tối đa.
            offset: Bỏ qua bao nhiêu phiên đầu.
            status: Lọc theo trạng thái lượt cuối.
            query: Tìm theo tiêu đề / session_id.

        Returns:
            `{items, total}`.
        """
        records = await self.db.select(self.table, columns=_SUMMARY_COLUMNS, order="created_at.desc", limit=_AGGREGATE_LIMIT)
        sessions = filter_sessions(aggregate_sessions(records, await self._titles()), query, status)
        return TraceSessionPageDTO(items=sessions[offset : offset + limit], total=len(sessions))

    async def session_detail(self, session_id: str) -> TraceSessionDetailDTO:
        """Trace phiên kèm danh sách lượt (cũ → mới, không kèm span).

        Args:
            session_id: Mã phiên.

        Returns:
            Trace phiên chi tiết.

        Raises:
            TraceNotFoundError: Phiên chưa có trace nào.
        """
        records = await self.db.select(self.table, {"session_id": session_id}, columns=_SUMMARY_COLUMNS, order="created_at.asc")
        if not records:
            raise TraceNotFoundError(f"Phiên chưa có trace: {session_id}", context={"session_id": session_id})
        summary = aggregate_sessions(records, await self._titles({"session_id": session_id}))[0]
        return TraceSessionDetailDTO(**summary.model_dump(), turns=[to_turn(r, i) for i, r in enumerate(records, start=1)])

    async def delete_session(self, session_id: str) -> None:
        """Xóa mọi trace của 1 phiên (dòng tóm tắt + blob).

        Args:
            session_id: Mã phiên.
        """
        rows = await self.db.delete(self.table, {"session_id": session_id})
        await self.storage.delete([r["storage_path"] for r in rows if r.get("storage_path")])


__all__ = ["TraceCollector", "TraceService", "aggregate_sessions", "extract_error_code", "filter_sessions", "to_turn"]
