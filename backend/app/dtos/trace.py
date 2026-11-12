"""Module định nghĩa DTOs cho Trace API.

Bao gồm:
    - SpanDTO: một đơn vị công việc có thời lượng trong trace (LLM call, sandbox
      exec, git commit, ...).
    - TraceSummaryDTO: metadata tinh gọn cho dashboard list.
    - TraceDetailDTO: metadata + toàn bộ spans cho waterfall view.
    - TraceTurnDTO / TraceSessionSummaryDTO / TraceSessionPageDTO / TraceSessionDetailDTO:
      trace gom theo phiên.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SpanType = Literal["llm", "tool", "sandbox", "git", "internal"]
SpanStatus = Literal["OK", "ERROR"]


class SpanDTO(BaseModel):
    """Một đơn vị công việc có thời lượng trong trace."""

    model_config = ConfigDict(extra="ignore")

    span_id: str = Field(..., description="ID unique của span")
    name: str = Field(..., description="Tên hiển thị (VD: 'LLM Generation (Attempt 1)')")
    span_type: SpanType = Field(..., description="Loại span để UI phân loại")
    duration_ms: int = Field(..., ge=0, description="Thời gian thực thi (ms)")
    start_offset_ms: int = Field(default=0, ge=0, description="Offset từ lúc trace bắt đầu tới lúc span bắt đầu (ms)")
    status: SpanStatus = Field(default="OK", description="OK hoặc ERROR")
    input_data: Any = Field(default=None, description="Payload đầu vào (đã truncate)")
    output_data: Any = Field(default=None, description="Payload đầu ra (đã truncate)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata bổ sung")


class TraceSummaryDTO(BaseModel):
    """Schema tóm tắt hiển thị danh sách Dashboard."""

    model_config = ConfigDict(extra="ignore")

    trace_id: str
    session_id: str
    prompt: str
    status: str
    duration_ms: int
    total_tokens: int
    cost: float
    created_at: str


class TraceDetailDTO(TraceSummaryDTO):
    """Schema chi tiết kèm toàn bộ cây Spans để UI vẽ Waterfall."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_tokens: int = 0
    error_message: str | None = None
    error_code: str | None = Field(default=None, description="Mã lỗi catalog (tách từ tiền tố '[CODE]' của error_message)")
    finished_at: str | None = None
    spans: list[SpanDTO] = Field(default_factory=list)


class TraceTurnDTO(TraceSummaryDTO):
    """1 lượt chat trong trace phiên (không kèm span — tải riêng qua `GET /traces/{trace_id}`)."""

    turn_index: int = Field(..., ge=1, description="Thứ tự lượt trong phiên (1 = đầu tiên)")
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_tokens: int = 0
    error_message: str | None = None
    error_code: str | None = None
    finished_at: str | None = None


class TraceSessionSummaryDTO(BaseModel):
    """Trace cấp phiên — gom mọi lượt chat của 1 phiên."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    title: str = ""
    turn_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    last_status: str | None = None
    last_trace_id: str | None = None
    duration_ms: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_tokens: int = 0
    cost: float = 0.0
    started_at: str = ""
    last_activity_at: str = ""


class TraceSessionPageDTO(BaseModel):
    """1 trang danh sách trace phiên + tổng số (cho phân trang)."""

    items: list[TraceSessionSummaryDTO] = Field(default_factory=list)
    total: int = 0


class TraceSessionDetailDTO(TraceSessionSummaryDTO):
    """Trace phiên kèm danh sách lượt (cũ → mới)."""

    turns: list[TraceTurnDTO] = Field(default_factory=list)


class LiveRunningSpanDTO(BaseModel):
    """1 việc đang chạy (chưa xong → chưa thành span), VD lời gọi LLM đang chờ."""

    name: str
    span_type: str
    start_offset_ms: int = Field(..., ge=0)
    elapsed_ms: int = Field(..., ge=0)


class LiveTraceDTO(BaseModel):
    """Trace của lượt ĐANG chạy (in-memory, chưa lưu Supabase)."""

    trace_id: str
    session_id: str
    prompt: str
    started_at: datetime
    elapsed_ms: int
    spans: list[SpanDTO]
    running: list[LiveRunningSpanDTO]


__all__ = [
    "LiveRunningSpanDTO",
    "LiveTraceDTO",
    "SpanDTO",
    "SpanStatus",
    "SpanType",
    "TraceDetailDTO",
    "TraceSessionDetailDTO",
    "TraceSessionPageDTO",
    "TraceSessionSummaryDTO",
    "TraceSummaryDTO",
    "TraceTurnDTO",
]
