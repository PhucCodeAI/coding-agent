"""API chat: chạy 1 lượt (SSE) và trả lời câu hỏi agent đang chờ."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.dtos.command import SlashCommand
from app.dtos.events import AnswersRequest, ChatRequest
from app.dtos.question import Answer
from app.routers.deps import ChatDep
from app.utils.sse import with_heartbeat
from app.workflow.commands import commands

chat_router = APIRouter(prefix="/chat", tags=["Chat"])


@chat_router.post("/run")
async def run(req: ChatRequest, chat: ChatDep) -> StreamingResponse:
    """Chạy 1 lượt và stream sự kiện SSE (client ngắt kết nối = hủy lượt).

    Args:
        req: Yêu cầu (không có session_id → phiên mới).
        chat: ChatService.

    Returns:
        Stream `text/event-stream`.
    """
    return StreamingResponse(
        with_heartbeat(chat.stream(req)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@chat_router.post("/answers", status_code=status.HTTP_204_NO_CONTENT)
async def answer(req: AnswersRequest, chat: ChatDep) -> None:
    """Trả lời câu hỏi agent đang chờ.

    Args:
        req: Câu trả lời.
        chat: ChatService.
    """
    chat.questions.resolve(req.session_id, req.request_id, [Answer(question_id=a.question_id, choice=a.choice, text=a.text) for a in req.answers])


@chat_router.get("/commands", response_model=list[SlashCommand])
async def list_commands() -> list[SlashCommand]:
    """Lệnh hệ thống (`/tên`) cho menu gợi ý của UI.

    Returns:
        Danh sách lệnh.
    """
    return list(commands().values())


__all__ = ["chat_router"]
