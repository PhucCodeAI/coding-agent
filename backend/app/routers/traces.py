"""API trace: trace theo phiên (danh sách, chi tiết, xóa, lượt đang chạy) và trace 1 lượt (kèm span).

Các route `/sessions...` khai báo trước `/{trace_id}` để không bị bắt nhầm.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.dtos.session import SESSION_ID_PATTERN
from app.dtos.trace import LiveTraceDTO, TraceDetailDTO, TraceSessionDetailDTO, TraceSessionPageDTO
from app.routers.deps import ChatDep, TracesDep

trace_router = APIRouter(prefix="/traces", tags=["Traces"])
SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]


@trace_router.get("/sessions", response_model=TraceSessionPageDTO)
async def list_trace_sessions(
    traces: TracesDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TraceSessionPageDTO:
    """Trang danh sách phiên có trace, hoạt động gần nhất trước.

    Args:
        traces: TraceService.
        q: Tìm theo tiêu đề / session_id (không phân biệt hoa thường).
        status: Lọc theo trạng thái lượt cuối.
        limit: Số phiên tối đa.
        offset: Bỏ qua bao nhiêu phiên đầu.

    Returns:
        `{items, total}`.
    """
    return await traces.list_sessions(limit, offset, status, q)


@trace_router.get("/sessions/{session_id}/live", response_model=LiveTraceDTO | None)
async def live_trace(session_id: SessionId, chat: ChatDep) -> LiveTraceDTO | None:
    """Trace lượt đang chạy của phiên (span đã xong + việc đang chờ); phiên rảnh → null.

    Args:
        session_id: Mã phiên.
        chat: ChatService.

    Returns:
        Trace live hoặc None.
    """
    return chat.live_trace(session_id)


@trace_router.get("/sessions/{session_id}", response_model=TraceSessionDetailDTO)
async def get_trace_session(session_id: SessionId, traces: TracesDep) -> TraceSessionDetailDTO:
    """Trace phiên kèm danh sách lượt (cũ → mới, không kèm span).

    Args:
        session_id: Mã phiên.
        traces: TraceService.

    Returns:
        Trace phiên chi tiết.
    """
    return await traces.session_detail(session_id)


@trace_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trace_session(session_id: SessionId, traces: TracesDep) -> None:
    """Xóa mọi trace (dòng tóm tắt + blob) của phiên.

    Args:
        session_id: Mã phiên.
        traces: TraceService.
    """
    await traces.delete_session(session_id)


@trace_router.get("/{trace_id}", response_model=TraceDetailDTO)
async def get_trace(trace_id: Annotated[str, Path(pattern=r"^tr_[a-f0-9]{12}$")], traces: TracesDep) -> TraceDetailDTO:
    """Trace 1 lượt kèm toàn bộ span.

    Args:
        trace_id: Mã trace.
        traces: TraceService.

    Returns:
        Trace chi tiết.
    """
    return await traces.get(trace_id)


__all__ = ["trace_router"]
