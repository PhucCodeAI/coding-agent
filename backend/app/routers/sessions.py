"""API phiên chat: danh sách, chi tiết (kèm sự kiện từng lượt), đổi tên, xóa, đọc/ghi workspace, chạy lệnh trong sandbox, lịch sử commit và thay đổi chưa commit."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, status

from app.dtos.session import (
    SESSION_ID_PATTERN,
    CommitDiffDTO,
    CommitListDTO,
    ExecRequest,
    FileWriteRequest,
    SessionDetailDTO,
    SessionDTO,
    SessionUpdateRequest,
    WorkingChangesDTO,
)
from app.dtos.workspace import ExecutionResult
from app.errors import AppError
from app.routers.deps import ChatDep, SessionsDep

session_router = APIRouter(prefix="/sessions", tags=["Sessions"])
SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]


@session_router.get("", response_model=list[SessionDTO])
async def list_sessions(sessions: SessionsDep, limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> list[SessionDTO]:
    """Danh sách phiên, mới cập nhật trước.

    Args:
        sessions: SessionService.
        limit: Số phiên tối đa.
        offset: Bỏ qua bao nhiêu phiên đầu.

    Returns:
        Danh sách phiên.
    """
    return await sessions.list(limit, offset)


@session_router.get("/{session_id}", response_model=SessionDetailDTO)
async def get_session(session_id: SessionId, sessions: SessionsDep) -> SessionDetailDTO:
    """Phiên kèm toàn bộ tin nhắn.

    Args:
        session_id: Mã phiên.
        sessions: SessionService.

    Returns:
        Phiên chi tiết.
    """
    return await sessions.detail(session_id)


@session_router.patch("/{session_id}", response_model=SessionDTO)
async def rename_session(session_id: SessionId, body: SessionUpdateRequest, sessions: SessionsDep) -> SessionDTO:
    """Đổi tên phiên.

    Args:
        session_id: Mã phiên.
        body: `{title}`.
        sessions: SessionService.

    Returns:
        Phiên sau cập nhật.
    """
    return await sessions.rename(session_id, body.title)


@session_router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: SessionId, sessions: SessionsDep, chat: ChatDep) -> None:
    """Xóa phiên (tin nhắn, trace, workspace); phiên đang chạy thì từ chối.

    Args:
        session_id: Mã phiên.
        sessions: SessionService.
        chat: ChatService (kiểm phiên đang chạy).

    Raises:
        AppError: SESSION_BUSY khi phiên đang chạy.
    """
    _ensure_idle(chat, session_id)
    await sessions.delete(session_id)


@session_router.get("/{session_id}/files")
async def list_files(session_id: SessionId, sessions: SessionsDep) -> list[dict[str, Any]]:
    """File trong workspace của phiên.

    Args:
        session_id: Mã phiên.
        sessions: SessionService.

    Returns:
        Danh sách `{path, size}`.
    """
    return await sessions.list_files(session_id)


@session_router.get("/{session_id}/files/content")
async def read_file(session_id: SessionId, sessions: SessionsDep, path: Annotated[str, Query(min_length=1, max_length=512)]) -> dict[str, Any]:
    """Nội dung 1 file trong workspace.

    Args:
        session_id: Mã phiên.
        sessions: SessionService.
        path: Đường dẫn tương đối.

    Returns:
        `{path, content, size, truncated, binary}`.
    """
    return await sessions.read_file(session_id, path)


def _ensure_idle(chat: ChatDep, session_id: str) -> None:
    """Từ chối thao tác lên workspace khi agent đang chạy trong phiên.

    Args:
        chat: ChatService.
        session_id: Mã phiên.

    Raises:
        AppError: SESSION_BUSY khi phiên đang chạy.
    """
    if chat.is_busy(session_id):
        raise AppError("Phiên đang chạy", code="SESSION_BUSY", context={"session_id": session_id})


@session_router.put("/{session_id}/files/content")
async def write_file(session_id: SessionId, body: FileWriteRequest, sessions: SessionsDep, chat: ChatDep) -> dict[str, Any]:
    """Ghi 1 file trong workspace (sửa code từ UI); phiên đang chạy thì từ chối.

    Args:
        session_id: Mã phiên.
        body: `{path, content}`.
        sessions: SessionService.
        chat: ChatService (kiểm phiên đang chạy).

    Returns:
        `{path, size}`.
    """
    _ensure_idle(chat, session_id)
    return await sessions.write_file(session_id, body.path, body.content)


@session_router.post("/{session_id}/exec", response_model=ExecutionResult)
async def exec_command(session_id: SessionId, body: ExecRequest, sessions: SessionsDep, chat: ChatDep) -> ExecutionResult:
    """Chạy 1 lệnh shell trong sandbox của phiên; phiên đang chạy thì từ chối.

    Args:
        session_id: Mã phiên.
        body: `{command}`.
        sessions: SessionService.
        chat: ChatService (kiểm phiên đang chạy).

    Returns:
        `{exit_code, stdout, stderr, duration_ms, is_timeout}`.
    """
    _ensure_idle(chat, session_id)
    return await sessions.exec_command(session_id, body.command)


@session_router.get("/{session_id}/commits", response_model=CommitListDTO)
async def list_commits(session_id: SessionId, sessions: SessionsDep, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> CommitListDTO:
    """Lịch sử commit workspace của phiên (mới nhất trước).

    Args:
        session_id: Mã phiên.
        sessions: SessionService.
        limit: Số commit tối đa.

    Returns:
        `{commits: [{hash, message, author, date, files}]}`.
    """
    return await sessions.list_commits(session_id, limit)


@session_router.get("/{session_id}/changes", response_model=WorkingChangesDTO)
async def working_changes(session_id: SessionId, sessions: SessionsDep) -> WorkingChangesDTO:
    """Thay đổi chưa commit của workspace.

    Args:
        session_id: Mã phiên.
        sessions: SessionService.

    Returns:
        `{files: [{path, status}], diff, truncated}`.
    """
    return await sessions.working_changes(session_id)


@session_router.get("/{session_id}/commits/{commit_hash}/diff", response_model=CommitDiffDTO)
async def commit_diff(session_id: SessionId, commit_hash: Annotated[str, Path(pattern=r"^[0-9a-f]{4,40}$")], sessions: SessionsDep) -> CommitDiffDTO:
    """Unified diff của 1 commit.

    Args:
        session_id: Mã phiên.
        commit_hash: Hash commit (hex 4–40 ký tự).
        sessions: SessionService.

    Returns:
        `{hash, diff, truncated}`.
    """
    return await sessions.commit_diff(session_id, commit_hash)


__all__ = ["session_router"]
