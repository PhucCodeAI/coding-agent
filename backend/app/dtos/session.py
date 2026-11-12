"""DTOs cho quản lý phiên chat (session) và lịch sử tin nhắn.

Mô hình:
    - Session: 1 cuộc hội thoại = 1 workspace + 1 git repo riêng
      (storage/sandboxes/<session_id>/).
    - SessionMessage: 1 tin nhắn trong phiên. Tin nhắn `assistant` lưu toàn bộ
      SSE events của lượt chạy để UI replay lại y hệt lúc stream live.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

MessageRole = Literal["user", "assistant"]


class SessionDTO(BaseModel):
    """Metadata một phiên — dùng cho danh sách ở thanh bên."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    title: str
    last_status: str | None = Field(default=None, description="Trạng thái lượt chạy gần nhất")
    message_count: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    last_trace_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


class SessionMessageDTO(BaseModel):
    """Một tin nhắn trong phiên."""

    model_config = ConfigDict(extra="ignore")

    message_id: str
    session_id: str
    role: MessageRole
    content: str = ""
    status: str | None = Field(default=None, description="assistant: SUCCESS/FAILED/CANCELLED")
    trace_id: str | None = None
    duration_ms: int | None = None
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="assistant: list {event, data} theo thứ tự stream (không gồm ping)",
    )
    created_at: str = ""


class SessionDetailDTO(SessionDTO):
    """Phiên kèm toàn bộ tin nhắn (sắp xếp theo thời gian)."""

    messages: list[SessionMessageDTO] = Field(default_factory=list)


class SessionUpdateRequest(BaseModel):
    """Body đổi tên phiên.

    Attributes:
        title: Tiêu đề mới.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)


class FileWriteRequest(BaseModel):
    """Body ghi file từ UI.

    Attributes:
        path: Đường dẫn tương đối trong workspace.
        content: Nội dung mới (UTF-8).
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., max_length=1_000_000)


class ExecRequest(BaseModel):
    """Body chạy lệnh shell trong sandbox của phiên.

    Attributes:
        command: Lệnh shell, chạy bằng `sh -c` (không mạng).
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., min_length=1, max_length=2000)


class CommitDTO(BaseModel):
    """1 commit trong workspace của phiên.

    Attributes:
        hash: Hash ngắn.
        message: Thông điệp commit.
        author: Tác giả.
        date: Thời điểm (ISO).
        files: File thay đổi trong commit.
    """

    hash: str
    message: str = ""
    author: str = ""
    date: str = ""
    files: list[str] = Field(default_factory=list)


class CommitListDTO(BaseModel):
    """Lịch sử commit (mới nhất trước).

    Attributes:
        commits: Danh sách commit.
    """

    commits: list[CommitDTO] = Field(default_factory=list)


class CommitDiffDTO(BaseModel):
    """Unified diff của 1 commit.

    Attributes:
        hash: Hash commit được hỏi.
        diff: Diff (đã cắt theo `max_diff_chars`).
        truncated: Diff có bị cắt không.
    """

    hash: str
    diff: str = ""
    truncated: bool = False


class ChangedFileDTO(BaseModel):
    """1 file thay đổi chưa commit.

    Attributes:
        path: Đường dẫn tương đối.
        status: added | modified | deleted.
    """

    path: str
    status: Literal["added", "modified", "deleted"]


class WorkingChangesDTO(BaseModel):
    """Thay đổi chưa commit của workspace.

    Attributes:
        files: File thay đổi.
        diff: Unified diff (đã cắt theo `max_diff_chars`).
        truncated: Diff có bị cắt không.
    """

    files: list[ChangedFileDTO] = Field(default_factory=list)
    diff: str = ""
    truncated: bool = False


__all__ = [
    "ChangedFileDTO",
    "CommitDTO",
    "CommitDiffDTO",
    "CommitListDTO",
    "MessageRole",
    "SessionDTO",
    "SessionDetailDTO",
    "SessionMessageDTO",
    "SessionUpdateRequest",
    "WorkingChangesDTO",
]
