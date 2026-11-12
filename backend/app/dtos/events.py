"""Request của API chat và payload các sự kiện SSE (session, step, tool, file, sandbox, commit, question, answer, final, error, done)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FinalStatus = Literal["SUCCESS", "FAILED", "CANCELLED"]
FileActionType = Literal["create", "update", "delete"]


# ============================================================
# Input
# ============================================================
class ChatRequest(BaseModel):
    """Schema dữ liệu đầu vào từ người dùng gửi lên API TurnRunner."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., min_length=1, description="Yêu cầu lập trình của người dùng")
    session_id: str | None = Field(
        default=None,
        # session_id được dùng trực tiếp làm tên thư mục workspace → chặn path traversal.
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Mã phiên; None = tạo phiên mới (trả về qua SSE event `session`)",
    )


class AnswersRequest(BaseModel):
    """Body POST /chat/answers — người dùng trả lời câu hỏi HITL."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    request_id: str = Field(..., min_length=1, max_length=64)
    answers: list[AnswerItemInput] = Field(..., min_length=1, max_length=10)


class AnswerItemInput(BaseModel):
    """1 câu trả lời: chọn 1 lựa chọn (`choice`) hoặc tự nhập (`text`, lựa chọn "Khác")."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., min_length=1, max_length=60)
    choice: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=2000)


# ============================================================
# SSE Event Payloads
# ============================================================


class FileEventPayload(BaseModel):
    """Payload cho event thông báo 1 file đã được ghi/cập nhật/xóa."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Đường dẫn tương đối của file")
    action: FileActionType = Field(..., description="Hành động đã thực thi")
    lines_added: int = Field(default=0, ge=0, description="Số dòng thêm vào")
    lines_removed: int = Field(default=0, ge=0, description="Số dòng xóa đi")


class SandboxEventPayload(BaseModel):
    """Payload cho event thông báo kết quả 1 lần chạy lệnh trong sandbox."""

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(..., description="Lệnh đã thực thi trong container")
    exit_code: int = Field(..., description="Mã thoát của tiến trình")
    duration_ms: int = Field(..., ge=0, description="Thời gian chạy (ms)")
    is_timeout: bool = Field(default=False, description="Cờ báo tiến trình bị ngắt do timeout")
    stdout_summary: str = Field(default="", description="Tóm tắt stdout (tail ~500 ký tự) để UI preview")


class CommitEventPayload(BaseModel):
    """Payload cho event thông báo Git commit thành công."""

    model_config = ConfigDict(extra="forbid")

    hash: str = Field(..., min_length=1, description="Mã SHA rút gọn của commit")
    message: str = Field(..., min_length=1, description="Thông điệp commit")
    files: list[str] = Field(default_factory=list, description="Danh sách file đã thay đổi")


class ErrorEventPayload(BaseModel):
    """Payload lỗi chuẩn — dùng chung cho SSE event `error` và REST `detail`.

    `title`/`message`/`hint`/`retryable` lấy từ catalog theo `code`
    (exceptions/catalog.py) để mỗi mã lỗi luôn có cùng thông báo cho người
    dùng. Thông tin kỹ thuật nằm ở `detail` + `context`.
    Tạo qua `error_payload()` / `error_payload_from_exc()`, không tự điền tay.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, description="Mã định danh lỗi (VD: TEST_MAX_RETRIES)")
    title: str = Field(default="", description="Tiêu đề ngắn cho người dùng")
    message: str = Field(..., min_length=1, description="Thông báo tường minh cho người dùng")
    hint: str = Field(default="", description="Gợi ý hành động tiếp theo")
    retryable: bool = Field(default=False, description="Thử lại có thể thành công không")
    detail: str | None = Field(default=None, description="Chi tiết kỹ thuật (debug)")
    context: dict[str, Any] | None = Field(default=None, description="Metadata kỹ thuật")


# ============================================================
# Vòng lặp agent: step (1 lời gọi LLM) / tool (1 lần chạy công cụ) / final / HITL
# ============================================================


class QuestionOptionPayload(BaseModel):
    """1 lựa chọn của câu hỏi."""

    model_config = ConfigDict(extra="forbid")

    label: str
    description: str = ""
    recommended: bool = False


class QuestionItemPayload(BaseModel):
    """1 câu hỏi. UI luôn thêm lựa chọn "Khác" + ô nhập với other_placeholder."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    options: list[QuestionOptionPayload]
    other_placeholder: str = "Nhập câu trả lời khác..."


class QuestionEventPayload(BaseModel):
    """Human-in-the-loop: pipeline tạm dừng chờ người dùng trả lời.

    Trả lời qua POST /chat/answers {session_id, request_id, answers}.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str
    asked_by: str = Field(default="agent", description="Ai hỏi (luôn là agent)")
    questions: list[QuestionItemPayload]
    timeout_sec: int


class AnswerItemPayload(BaseModel):
    """Câu trả lời 1 câu hỏi."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    choice: str | None = None
    text: str | None = None


class AnswerEventPayload(BaseModel):
    """Đã nhận câu trả lời (hoặc hết hạn → dùng lựa chọn khuyến nghị)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    answers: list[AnswerItemPayload]
    timed_out: bool = False


class ToolCallPayload(BaseModel):
    """1 lời gọi công cụ trong 1 bước."""

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class StepEventPayload(BaseModel):
    """1 lời gọi LLM của agent: model, token, nội dung và các công cụ được gọi."""

    step: int = Field(..., ge=1)
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    duration_ms: int = 0
    content: str = Field(default="", description="Phần chữ model trả lời kèm lời gọi công cụ")
    tool_calls: list[ToolCallPayload] = Field(default_factory=list)


class ToolEventPayload(BaseModel):
    """Kết quả 1 lần chạy công cụ (khớp `ToolCallPayload.id`)."""

    id: str
    name: str
    ok: bool = True
    output: str = Field(default="", description="Kết quả trả cho model (đã cắt)")
    duration_ms: int = 0


class FinalEventPayload(BaseModel):
    """Kết quả cuối của lượt (agent gọi `finish` hoặc bị dừng)."""

    status: Literal["done", "failed"]
    text: str = Field(..., description="Câu trả lời cho người dùng (Markdown)")
    code: str | None = Field(default=None, description="Mã lỗi khi failed")


class DoneEventPayload(BaseModel):
    """Payload cho event kết thúc stream kèm thông số đo kiểm chi tiết."""

    model_config = ConfigDict(extra="forbid")

    status: FinalStatus = Field(..., description="Trạng thái cuối cùng của phiên")
    duration_ms: int = Field(..., ge=0, description="Tổng thời gian thực thi (ms)")
    total_tokens: int = Field(default=0, ge=0, description="Tổng lượng token sử dụng")
    input_tokens: int = Field(default=0, ge=0, description="Số lượng prompt token")
    output_tokens: int = Field(default=0, ge=0, description="Số lượng completion token")
    reasoning_tokens: int = Field(default=0, ge=0, description="Số lượng thinking/reasoning token")
    cache_tokens: int = Field(default=0, ge=0, description="Số lượng cached token")
    cost: float = Field(default=0.0, ge=0.0, description="Chi phí ước tính (USD)")
    trace_id: str = Field(default="", description="Mã định danh trace để UI mở Waterfall")


class SessionEventPayload(BaseModel):
    """Payload SSE event `session` — luôn là event đầu tiên của stream."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    title: str


__all__ = [
    "SessionEventPayload",
    "AnswerEventPayload",
    "AnswerItemInput",
    "AnswerItemPayload",
    "AnswersRequest",
    "CommitEventPayload",
    "DoneEventPayload",
    "ErrorEventPayload",
    "FileActionType",
    "FileEventPayload",
    "FinalEventPayload",
    "FinalStatus",
    "ChatRequest",
    "QuestionEventPayload",
    "QuestionItemPayload",
    "QuestionOptionPayload",
    "SandboxEventPayload",
    "StepEventPayload",
    "ToolCallPayload",
    "ToolEventPayload",
]
