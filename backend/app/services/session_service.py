"""Phiên chat: tạo phiên, lưu từng lượt (prompt + toàn bộ sự kiện), lịch sử cho agent, đọc workspace, xóa phiên.

Mỗi phiên = 1 dòng `sessions` + các dòng `session_messages` + 1 thư mục workspace (git repo riêng) + các trace.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import configs, tool_configs
from app.dtos.session import CommitDiffDTO, CommitDTO, CommitListDTO, SessionDetailDTO, SessionDTO, SessionMessageDTO, WorkingChangesDTO
from app.dtos.workspace import ExecutionResult
from app.errors import AppError, FileNotFoundInWorkspaceError, GitCommandError, SessionNotFoundError, get_error_spec
from app.integrations import git, sandbox
from app.repositories.database import Database
from app.repositories.workspace import Workspace
from app.services.trace_service import TraceService

_SESSION_COLUMNS = "session_id,title,last_status,message_count,total_tokens,cost,last_trace_id,created_at,updated_at"


def new_session_id() -> str:
    """Mã phiên mới (`ses_` + 16 hex)."""
    return f"ses_{uuid.uuid4().hex[:16]}"


def title_from_prompt(prompt: str, max_len: int = 60) -> str:
    """Tiêu đề phiên từ prompt đầu tiên (1 dòng, cắt theo ranh giới từ).

    Args:
        prompt: Prompt.
        max_len: Độ dài tối đa.

    Returns:
        Tiêu đề.
    """
    line = " ".join(prompt.split())
    if len(line) <= max_len:
        return line or "Phiên mới"
    cut = line[:max_len]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"


def summarize_turn(status: str, events: list[dict[str, Any]]) -> str:
    """Tóm tắt 1 lượt (làm ngữ cảnh cho lượt sau): câu trả lời cuối, hoặc 1 dòng trạng thái.

    Args:
        status: SUCCESS | FAILED | CANCELLED.
        events: Sự kiện của lượt (`{event, data}`).

    Returns:
        Bản tóm tắt.
    """
    final = next((e["data"] for e in reversed(events) if e.get("event") == "final"), None)
    answer = str((final or {}).get("text", "")).strip()
    if answer and status == "SUCCESS":
        return answer[:4000]
    if status == "CANCELLED":
        return "Người dùng đã dừng lượt chạy."
    error = next((e["data"] for e in reversed(events) if e.get("event") == "error"), None)
    if error:
        code = str(error.get("code", "SYSTEM_ERROR"))
        return f"Thất bại [{code}]: {get_error_spec(code).title}."
    return answer[:4000] or "Thất bại."


def _now() -> str:
    """Thời điểm hiện tại dạng ISO UTC."""
    return datetime.now(UTC).isoformat()


class SessionService:
    """Nghiệp vụ phiên chat."""

    def __init__(self, db: Database, traces: TraceService, workspace_base: str) -> None:
        """Tạo service.

        Args:
            db: Repository database.
            traces: Service trace (xóa trace khi xóa phiên).
            workspace_base: Thư mục chứa workspace các phiên.
        """
        self.db = db
        self.traces = traces
        self.workspace_base = Path(workspace_base).resolve()
        self.sessions = configs.database.sessions_table
        self.messages = configs.database.messages_table

    async def start_turn(self, session_id: str, prompt: str, history_turns: int) -> tuple[str, list[dict[str, str]]]:
        """Tạo phiên nếu chưa có, lấy lịch sử, lưu prompt.

        Args:
            session_id: Mã phiên.
            prompt: Prompt của lượt mới.
            history_turns: Số lượt trước cần lấy.

        Returns:
            (tiêu đề phiên, lịch sử `{role, content}` cũ → mới).
        """
        history = []
        if history_turns > 0:
            rows = await self.db.select(self.messages, {"session_id": session_id}, columns="role,content", order="created_at.desc", limit=history_turns * 2)
            history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        now = _now()
        await self.db.insert(
            self.sessions,
            {"session_id": session_id, "title": title_from_prompt(prompt), "message_count": 0, "created_at": now, "updated_at": now},
            ignore_duplicates=True,
        )
        row = await self.db.get(self.sessions, {"session_id": session_id}, columns="title")
        await self._add_message(session_id, "user", prompt)
        return (row or {}).get("title") or title_from_prompt(prompt), history

    async def finish_turn(self, session_id: str, events: list[dict[str, Any]], status: str, trace_id: str, duration_ms: int, metrics: dict[str, float]) -> None:
        """Lưu kết quả lượt (toàn bộ sự kiện) và cập nhật thống kê phiên.

        Args:
            session_id: Mã phiên.
            events: Sự kiện SSE của lượt.
            status: Trạng thái cuối.
            trace_id: Mã trace.
            duration_ms: Thời gian.
            metrics: Token và chi phí.
        """
        await self._add_message(
            session_id, "assistant", summarize_turn(status, events), status=status, trace_id=trace_id, duration_ms=duration_ms, events=events
        )
        current = await self.db.get(self.sessions, {"session_id": session_id}) or {}
        await self.db.update(
            self.sessions,
            {"session_id": session_id},
            {
                "last_status": status,
                "last_trace_id": trace_id,
                "updated_at": _now(),
                "message_count": int(current.get("message_count") or 0) + 2,
                "total_tokens": int(current.get("total_tokens") or 0) + int(metrics.get("total_tokens") or 0),
                "cost": float(current.get("cost") or 0.0) + float(metrics.get("cost") or 0.0),
            },
        )

    async def _add_message(self, session_id: str, role: str, content: str, **extra: Any) -> None:
        """Thêm 1 tin nhắn.

        Args:
            session_id: Mã phiên.
            role: user | assistant.
            content: Nội dung (assistant: bản tóm tắt).
            **extra: status, trace_id, duration_ms, events.
        """
        await self.db.insert(
            self.messages,
            {
                "message_id": f"msg_{uuid.uuid4().hex[:16]}",
                "session_id": session_id,
                "role": role,
                "content": content,
                "events": [],
                "created_at": _now(),
                **extra,
            },
        )

    async def list(self, limit: int = 50, offset: int = 0) -> list[SessionDTO]:
        """Danh sách phiên, mới cập nhật trước.

        Args:
            limit: Số phiên tối đa.
            offset: Bỏ qua bao nhiêu phiên đầu.

        Returns:
            Danh sách phiên.
        """
        rows = await self.db.select(self.sessions, columns=_SESSION_COLUMNS, order="updated_at.desc", limit=limit, offset=offset)
        return [SessionDTO(**r) for r in rows]

    async def detail(self, session_id: str) -> SessionDetailDTO:
        """Phiên kèm toàn bộ tin nhắn.

        Args:
            session_id: Mã phiên.

        Returns:
            Phiên chi tiết.

        Raises:
            SessionNotFoundError: Không có phiên.
        """
        row = await self.db.get(self.sessions, {"session_id": session_id}, columns=_SESSION_COLUMNS)
        if row is None:
            raise SessionNotFoundError(f"Không tìm thấy phiên: {session_id}", context={"session_id": session_id})
        messages = await self.db.select(self.messages, {"session_id": session_id}, order="created_at.asc")
        return SessionDetailDTO(**row, messages=[SessionMessageDTO(**m) for m in messages])

    async def rename(self, session_id: str, title: str) -> SessionDTO:
        """Đổi tên phiên.

        Args:
            session_id: Mã phiên.
            title: Tiêu đề mới (gộp khoảng trắng thừa).

        Returns:
            Phiên sau cập nhật.

        Raises:
            SessionNotFoundError: Không có phiên.
        """
        rows = await self.db.update(self.sessions, {"session_id": session_id}, {"title": " ".join(title.split()) or "Phiên mới", "updated_at": _now()})
        if not rows:
            raise SessionNotFoundError(f"Không tìm thấy phiên: {session_id}", context={"session_id": session_id})
        return SessionDTO(**rows[0])

    async def delete(self, session_id: str) -> None:
        """Xóa phiên: tin nhắn (cascade), trace, thư mục workspace.

        Args:
            session_id: Mã phiên.

        Raises:
            SessionNotFoundError: Không có phiên.
        """
        if not await self.db.delete(self.sessions, {"session_id": session_id}):
            raise SessionNotFoundError(f"Không tìm thấy phiên: {session_id}", context={"session_id": session_id})
        await self.traces.delete_session(session_id)
        workspace = (self.workspace_base / session_id).resolve()
        if workspace.parent == self.workspace_base and workspace.exists():
            await asyncio.to_thread(shutil.rmtree, workspace, True)
        logger.info("Đã xóa phiên {}", session_id)

    async def list_files(self, session_id: str) -> list[dict[str, Any]]:
        """File trong workspace của phiên.

        Args:
            session_id: Mã phiên.

        Returns:
            Danh sách `{path, size}` (workspace chưa có → rỗng).
        """
        root = self.workspace_base / session_id
        if not root.exists():
            return []
        return [{"path": f, "size": (root / f).stat().st_size} for f in await Workspace(root).list_files()]

    async def read_file(self, session_id: str, path: str) -> dict[str, Any]:
        """Nội dung 1 file trong workspace (tối đa `max_file_view_bytes`, file nhị phân trả rỗng).

        Args:
            session_id: Mã phiên.
            path: Đường dẫn tương đối.

        Returns:
            `{path, content, size, truncated, binary}`.

        Raises:
            FileNotFoundInWorkspaceError: Không có file.
            PathSecurityError: Đường dẫn thoát khỏi workspace.
        """
        root = self.workspace_base / session_id
        target = Workspace(root)._resolve_safe_path(path) if root.exists() else None
        if target is None or not target.is_file():
            raise FileNotFoundInWorkspaceError(f"File không tồn tại: {path}", context={"path": path})
        raw = await asyncio.to_thread(target.read_bytes)
        limit = tool_configs.tools.workspace.max_file_view_bytes
        if b"\x00" in raw[:8192]:
            return {"path": path, "content": "", "size": len(raw), "truncated": False, "binary": True}
        return {"path": path, "content": raw[:limit].decode("utf-8", errors="replace"), "size": len(raw), "truncated": len(raw) > limit, "binary": False}

    def _workspace(self, session_id: str) -> Path:
        """Thư mục workspace đã tồn tại của phiên.

        Args:
            session_id: Mã phiên.

        Returns:
            Đường dẫn workspace.

        Raises:
            AppError: NOT_FOUND khi phiên chưa có workspace.
        """
        root = self.workspace_base / session_id
        if not root.is_dir():
            raise AppError(f"Phiên chưa có workspace: {session_id}", code="NOT_FOUND", context={"session_id": session_id})
        return root

    async def write_file(self, session_id: str, path: str, content: str) -> dict[str, Any]:
        """Ghi (tạo hoặc thay) 1 file trong workspace — sửa code từ UI, không tự commit.

        Args:
            session_id: Mã phiên.
            path: Đường dẫn tương đối.
            content: Nội dung mới.

        Returns:
            `{path, size}`.

        Raises:
            PathSecurityError: Đường dẫn thoát khỏi workspace hoặc chạm .git.
        """
        workspace = Workspace(self._workspace(session_id))
        target = workspace._resolve_safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await workspace._atomic_write(target, content)
        logger.info("UI ghi file {} | session={}", path, session_id)
        return {"path": path, "size": len(content.encode("utf-8"))}

    async def exec_command(self, session_id: str, command: str) -> ExecutionResult:
        """Chạy 1 lệnh shell trong sandbox của phiên (`sh -c`, không mạng, giới hạn như lệnh của agent).

        Args:
            session_id: Mã phiên.
            command: Lệnh shell.

        Returns:
            Mã thoát, stdout, stderr, thời gian.
        """
        root = self._workspace(session_id)
        logger.info("UI chạy lệnh sandbox | session={} | {}", session_id, command[:120])
        return await sandbox.run_command(root, ["sh", "-c", command])

    async def list_commits(self, session_id: str, limit: int = 50) -> CommitListDTO:
        """Lịch sử commit workspace của phiên (mới nhất trước).

        Args:
            session_id: Mã phiên.
            limit: Số commit tối đa.

        Returns:
            Danh sách commit (workspace chưa có → rỗng).
        """
        root = self.workspace_base / session_id
        if not root.exists():
            return CommitListDTO()
        return CommitListDTO(commits=[CommitDTO(**c) for c in await git.log(root, limit)])

    async def working_changes(self, session_id: str) -> WorkingChangesDTO:
        """Thay đổi chưa commit của workspace (file + diff).

        Args:
            session_id: Mã phiên.

        Returns:
            File thay đổi và diff (workspace chưa có → rỗng).
        """
        root = self.workspace_base / session_id
        if not root.exists():
            return WorkingChangesDTO()
        res = await git.working_changes(root)
        diff, limit = str(res["diff"]), tool_configs.tools.workspace.max_diff_chars
        return WorkingChangesDTO(files=res["files"], diff=diff[:limit], truncated=len(diff) > limit)

    async def commit_diff(self, session_id: str, commit_hash: str) -> CommitDiffDTO:
        """Unified diff của 1 commit.

        Args:
            session_id: Mã phiên.
            commit_hash: Hash commit (hex, đã validate ở router).

        Returns:
            Diff của commit.

        Raises:
            AppError: NOT_FOUND khi workspace hoặc commit không tồn tại.
        """
        root = self.workspace_base / session_id
        not_found = AppError(f"Không tìm thấy commit: {commit_hash}", code="NOT_FOUND", context={"session_id": session_id, "hash": commit_hash})
        if not (root / ".git").exists():
            raise not_found
        try:
            diff = await git.show_diff(root, commit_hash)
        except GitCommandError as e:
            raise not_found from e
        limit = tool_configs.tools.workspace.max_diff_chars
        return CommitDiffDTO(hash=commit_hash, diff=diff[:limit], truncated=len(diff) > limit)


__all__ = ["SessionService", "new_session_id", "summarize_turn", "title_from_prompt"]
