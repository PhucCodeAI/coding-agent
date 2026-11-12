"""Lỗi của ứng dụng: cây exception (`AppError` mang mã lỗi), danh mục mã lỗi và cách dựng sự kiện `error`."""

from __future__ import annotations

import json
from typing import Any

from app.dtos.error import ErrorSpec
from app.dtos.events import ErrorEventPayload


class AppError(Exception):
    """Exception gốc cho mọi lỗi nghiệp vụ trong hệ thống.

    Attributes:
        code: Mã định danh lỗi, dùng để map sang SSE error event.
        message: Mô tả chi tiết lỗi cho người dùng.
        context: Dict metadata bổ sung (VD: file path, attempt number).
    """

    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        """Khởi tạo AppError.

        Args:
            message: Thông báo lỗi.
            code: Override mã lỗi mặc định của class.
            context: Metadata bổ sung.
        """
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.context: dict[str, object] = context or {}

    def to_dict(self) -> dict[str, object]:
        """Chuyển lỗi thành dict để serialize vào SSE event.

        Returns:
            Dict gồm code, message và context (nếu có).
        """
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        if self.context:
            payload["context"] = self.context
        return payload

    def __repr__(self) -> str:
        """Biểu diễn string để debug."""
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class LLMError(AppError):
    """Lỗi gốc cho mọi vấn đề phát sinh từ LLMClient."""

    code = "LLM_ERROR"


class LLMResponseError(LLMError):
    """Response không đúng cấu trúc JSON mong đợi."""

    code = "LLM_RESPONSE_INVALID"


class WorkspaceError(AppError):
    """Lỗi gốc cho mọi vấn đề trong Workspace."""

    code = "WORKSPACE_ERROR"


class PathSecurityError(WorkspaceError):
    """Phát hiện path traversal, symlink escape, hoặc truy cập .git.

    Đây là lỗi BẢO MẬT — không bao giờ được retry tự động.
    """

    code = "WORKSPACE_PATH_UNSAFE"


class EditApplyError(WorkspaceError):
    """Không apply được edit (search/replace) lên file.

    Caller (TurnRunner) thường re-prompt LLM yêu cầu dùng action='create'
    để rewrite toàn bộ file.
    """

    code = "WORKSPACE_EDIT_FAILED"


class FileNotFoundInWorkspaceError(WorkspaceError):
    """File cần đọc/update không tồn tại trong workspace."""

    code = "WORKSPACE_FILE_NOT_FOUND"


class SandboxError(AppError):
    """Lỗi gốc cho mọi vấn đề từ sandbox."""

    code = "SANDBOX_ERROR"


class DockerUnavailableError(SandboxError):
    """Docker daemon không khả dụng hoặc không có quyền truy cập socket."""

    code = "SANDBOX_DOCKER_UNAVAILABLE"


class GitError(AppError):
    """Lỗi gốc cho mọi vấn đề từ thao tác git."""

    code = "GIT_ERROR"


class GitCommandError(GitError):
    """Lệnh git CLI trả về exit code khác 0.

    Attributes:
        args: Danh sách tham số lệnh git đã chạy.
        exit_code: Mã thoát của tiến trình git.
        stderr: Stderr của git.
    """

    code = "GIT_COMMAND_FAILED"

    def __init__(
        self,
        message: str,
        *,
        args: list[str],
        exit_code: int,
        stderr: str,
    ) -> None:
        """Khởi tạo GitCommandError.

        Args:
            message: Thông báo tóm tắt.
            args: Args lệnh git đã chạy.
            exit_code: Mã thoát.
            stderr: Stderr.
        """
        super().__init__(
            message,
            context={"args": args, "exit_code": exit_code, "stderr": stderr[:500]},
        )
        self.args_list = args
        self.exit_code = exit_code
        self.stderr = stderr


class DatabaseError(AppError):
    """Đọc / ghi database (Supabase PostgREST) thất bại."""

    code = "DATABASE_ERROR"


class StorageError(AppError):
    """Đọc / ghi object storage (Supabase Storage) thất bại."""

    code = "STORAGE_ERROR"


class SessionNotFoundError(AppError):
    """Phiên không tồn tại."""

    code = "SESSION_NOT_FOUND"


class TraceNotFoundError(AppError):
    """Trace không tồn tại."""

    code = "TRACE_NOT_FOUND"


class QuestionNotFoundError(AppError):
    """Không có câu hỏi đang chờ khớp session_id + request_id (đã trả lời / hết hạn / sai id)."""

    code = "HUMAN_INPUT_NOT_FOUND"


class AnswerInvalidError(AppError):
    """Câu trả lời không hợp lệ (thiếu câu, lựa chọn không tồn tại)."""

    code = "HUMAN_INPUT_INVALID"


class ToolError(Exception):
    """Lỗi dùng công cụ (sai tham số, vi phạm quy tắc) — trả cho agent dưới dạng KẾT QUẢ LỖI để nó tự sửa."""


class CacheMiss(RuntimeError):
    """Chế độ replay: không có câu trả lời trong cache."""


# ============================================================
# Danh mục mã lỗi (nguồn duy nhất cho API, SSE, UI)
# ============================================================
_SUPABASE_HINT = "Kiểm tra SUPABASE_URL / SUPABASE_KEY trong backend/.env, bảng 'traces' và bucket 'trace-logs' đã được tạo."

_SPECS: tuple[ErrorSpec, ...] = (
    ErrorSpec(
        "SYSTEM_ERROR",
        "general",
        500,
        "Lỗi hệ thống",
        "Máy chủ gặp lỗi không mong đợi khi xử lý yêu cầu.",
        "Thử lại. Nếu lỗi lặp lại, gửi mã trace và phần chi tiết kỹ thuật cho đội phát triển.",
        True,
    ),
    ErrorSpec(
        "APP_ERROR",
        "general",
        500,
        "Lỗi xử lý",
        "Hệ thống không hoàn thành được thao tác.",
        "Thử lại. Nếu lỗi lặp lại, xem phần chi tiết kỹ thuật.",
        True,
    ),
    ErrorSpec(
        "VALIDATION_ERROR",
        "general",
        422,
        "Dữ liệu gửi lên không hợp lệ",
        "Một hoặc nhiều trường trong yêu cầu không đúng định dạng.",
        "Xem danh sách trường lỗi trong phần chi tiết và sửa lại.",
        False,
    ),
    ErrorSpec(
        "EMPTY_PROMPT",
        "general",
        422,
        "Chưa nhập yêu cầu",
        "Nội dung yêu cầu đang trống.",
        "Nhập mô tả việc bạn muốn agent làm rồi gửi lại.",
        False,
    ),
    ErrorSpec(
        "INVALID_SESSION_ID",
        "general",
        422,
        "Mã phiên không hợp lệ",
        "session_id chỉ được chứa chữ, số, '_' hoặc '-' và dài tối đa 64 ký tự.",
        "Đổi tên phiên (VD: 'demo_1') hoặc để trống để dùng phiên mặc định.",
        False,
    ),
    ErrorSpec(
        "NOT_FOUND",
        "general",
        404,
        "Không tìm thấy",
        "Đường dẫn API được gọi không tồn tại.",
        "Kiểm tra lại URL hoặc phiên bản Backend đang chạy.",
        False,
    ),
    ErrorSpec(
        "METHOD_NOT_ALLOWED",
        "general",
        405,
        "Phương thức không được hỗ trợ",
        "API này không hỗ trợ HTTP method đã dùng.",
        "Kiểm tra lại method (GET/POST/DELETE) theo tài liệu API.",
        False,
    ),
    ErrorSpec(
        "SESSION_BUSY",
        "pipeline",
        409,
        "Phiên đang bận",
        "Phiên làm việc này đang chạy một yêu cầu khác.",
        "Đợi yêu cầu hiện tại hoàn tất (hoặc bấm Dừng) rồi gửi lại, hoặc dùng một phiên khác.",
        True,
    ),
    ErrorSpec(
        "PROMPT_NOT_FOUND",
        "pipeline",
        500,
        "Thiếu prompt của agent",
        "Không tìm thấy file system prompt của agent.",
        "Kiểm tra file app/agents/prompts/agent.md có tồn tại trong backend.",
        False,
    ),
    ErrorSpec(
        "AGENT_INCOMPLETE",
        "pipeline",
        422,
        "Agent chưa hoàn thành yêu cầu",
        "Agent kết thúc với trạng thái chưa xong (thất bại hoặc bị chặn).",
        "Đọc câu trả lời cuối của agent để biết đã làm gì / vướng ở đâu, bổ sung yêu cầu cụ thể hơn rồi thử lại.",
        True,
    ),
    ErrorSpec(
        "AGENT_TIMEOUT",
        "pipeline",
        422,
        "Hết thời gian cho lượt này",
        "Agent chạy quá thời gian tối đa của một lượt; công việc dừng giữa chừng.",
        "Chia yêu cầu thành các phần nhỏ hơn, hoặc tăng agent.max_wall_sec trong backend/config/tool_config.toml.",
        True,
    ),
    ErrorSpec(
        "AGENT_STEP_LIMIT",
        "pipeline",
        422,
        "Agent hết số bước cho phép",
        "Agent dùng hết số lời gọi LLM tối đa (agent.max_steps) mà chưa xong việc.",
        "Chia việc nhỏ hơn hoặc tăng agent.max_steps trong backend/config/tool_config.toml.",
        True,
    ),
    ErrorSpec(
        "AGENT_REPEATED_FAILURE",
        "pipeline",
        422,
        "Agent lỗi liên tiếp nhiều lần",
        "Agent dùng công cụ 3 lần liên tiếp đều lỗi nên bị dừng tự động thay vì thử mãi.",
        "Xem 3 lỗi gần nhất trong câu trả lời: thường do môi trường/công cụ (sandbox, đường dẫn, quyền) hơn là do code.",
        True,
    ),
    ErrorSpec(
        "HUMAN_INPUT_NOT_FOUND",
        "pipeline",
        404,
        "Câu hỏi không còn chờ trả lời",
        "Câu hỏi đã được trả lời, đã hết hạn (tự chọn phương án khuyến nghị) hoặc lượt chạy đã kết thúc.",
        "Không cần làm gì thêm — kết quả đã dùng câu trả lời trước đó hoặc phương án khuyến nghị.",
        False,
    ),
    ErrorSpec(
        "HUMAN_INPUT_INVALID",
        "pipeline",
        422,
        "Câu trả lời không hợp lệ",
        "Thiếu câu trả lời cho một câu hỏi hoặc lựa chọn không có trong danh sách.",
        "Chọn một lựa chọn cho mỗi câu hỏi, hoặc chọn 'Khác' và nhập câu trả lời.",
        False,
    ),
    ErrorSpec(
        "SESSION_NOT_FOUND",
        "session",
        404,
        "Không tìm thấy phiên",
        "Phiên chat không tồn tại hoặc đã bị xóa.",
        "Chọn phiên khác ở thanh bên hoặc tạo phiên mới.",
        False,
    ),
    ErrorSpec(
        "LLM_ERROR",
        "llm",
        502,
        "Lỗi mô hình AI",
        "Gọi mô hình ngôn ngữ thất bại.",
        "Thử lại sau ít phút.",
        True,
    ),
    ErrorSpec(
        "LLM_AUTH_FAILED",
        "llm",
        502,
        "API key mô hình AI không hợp lệ",
        "Nhà cung cấp LLM từ chối xác thực (HTTP 401/403).",
        "Kiểm tra OPENROUTER_API_KEY trong backend/.env còn hiệu lực rồi khởi động lại Backend.",
        False,
    ),
    ErrorSpec(
        "LLM_QUOTA_EXCEEDED",
        "llm",
        502,
        "Hết hạn mức sử dụng mô hình AI",
        "Tài khoản LLM đã hết credit hoặc hết số request miễn phí trong ngày (HTTP 402 / 429 free-models-per-day).",
        "Nạp credit OpenRouter (≥ 10 credits mở 1000 request model free/ngày), chờ hạn mức reset, hoặc đổi [llm] model trong backend/config/tool_config.toml.",
        False,
    ),
    ErrorSpec(
        "LLM_RATE_LIMITED",
        "llm",
        503,
        "Mô hình AI đang quá tải",
        "Gửi quá nhiều yêu cầu tới LLM trong thời gian ngắn (HTTP 429).",
        "Đợi khoảng 1 phút rồi thử lại. Model ':free' thường bị giới hạn số request/phút.",
        True,
    ),
    ErrorSpec(
        "LLM_BAD_REQUEST",
        "llm",
        502,
        "Yêu cầu tới mô hình AI bị từ chối",
        "Nhà cung cấp LLM không chấp nhận request (HTTP 400/422).",
        "Prompt có thể quá dài, hoặc model không hỗ trợ tool-calling — đổi model trong [[llm.providers]] (backend/config/tool_config.toml).",
        False,
    ),
    ErrorSpec(
        "LLM_MODEL_NOT_FOUND",
        "llm",
        502,
        "Không tìm thấy mô hình AI",
        "Model được cấu hình không tồn tại hoặc không còn được cung cấp (HTTP 404).",
        "Kiểm tra [llm] model trong backend/config/tool_config.toml.",
        False,
    ),
    ErrorSpec(
        "LLM_PROVIDER_ERROR",
        "llm",
        502,
        "Nhà cung cấp mô hình AI đang lỗi",
        "Máy chủ của nhà cung cấp LLM gặp sự cố (HTTP 5xx hoặc trả lỗi trong body).",
        "Thử lại sau ít phút hoặc đổi sang model khác.",
        True,
    ),
    ErrorSpec(
        "LLM_HTTP_ERROR",
        "llm",
        502,
        "Mô hình AI trả lỗi",
        "Nhà cung cấp LLM trả về mã HTTP lỗi không xác định.",
        "Xem status/body trong chi tiết kỹ thuật.",
        True,
    ),
    ErrorSpec(
        "LLM_TIMEOUT",
        "llm",
        504,
        "Mô hình AI phản hồi quá lâu",
        "LLM không trả kết quả trong thời gian cho phép.",
        "Thử lại, rút gọn yêu cầu, hoặc tăng [llm] timeout_sec trong backend/config/tool_config.toml.",
        True,
    ),
    ErrorSpec(
        "LLM_NETWORK_ERROR",
        "llm",
        502,
        "Không kết nối được mô hình AI",
        "Backend không kết nối được tới nhà cung cấp LLM.",
        "Kiểm tra kết nối Internet của máy chạy Backend và [llm] url trong backend/config/tool_config.toml.",
        True,
    ),
    ErrorSpec(
        "LLM_RESPONSE_INVALID",
        "llm",
        502,
        "Mô hình AI trả dữ liệu không đọc được",
        "Không trích xuất được JSON hợp lệ từ câu trả lời của LLM.",
        "Thử lại. Nếu lặp lại thường xuyên, đổi model.",
        True,
    ),
    ErrorSpec(
        "LLM_OUTPUT_TRUNCATED",
        "llm",
        502,
        "Câu trả lời của mô hình AI bị cắt",
        "LLM dừng giữa chừng vì vượt giới hạn độ dài output.",
        "Chia yêu cầu thành các phần nhỏ hơn hoặc dùng model có context dài hơn.",
        True,
    ),
    ErrorSpec(
        "WORKSPACE_ERROR",
        "workspace",
        500,
        "Lỗi thao tác file",
        "Không thao tác được file trong workspace của phiên.",
        "Kiểm tra quyền ghi thư mục storage/sandboxes của Backend.",
        True,
    ),
    ErrorSpec(
        "WORKSPACE_PATH_UNSAFE",
        "workspace",
        400,
        "Đường dẫn file bị chặn vì lý do an toàn",
        "Agent cố ghi ra ngoài workspace, vào thư mục .git hoặc qua symlink.",
        "Không thử lại tự động. Diễn đạt lại yêu cầu, tránh nhắc đường dẫn tuyệt đối hoặc '..'.",
        False,
    ),
    ErrorSpec(
        "WORKSPACE_EDIT_FAILED",
        "workspace",
        500,
        "Không áp dụng được chỉnh sửa file",
        "Đoạn code agent muốn sửa không khớp với nội dung file hiện tại.",
        "Thử lại — agent sẽ được yêu cầu ghi đè toàn bộ file.",
        True,
    ),
    ErrorSpec(
        "WORKSPACE_FILE_NOT_FOUND",
        "workspace",
        404,
        "File không tồn tại",
        "Agent muốn sửa hoặc xóa một file chưa có trong workspace.",
        "Thử lại — agent sẽ được yêu cầu tạo file mới.",
        True,
    ),
    ErrorSpec(
        "SANDBOX_ERROR",
        "sandbox",
        500,
        "Lỗi môi trường chạy test",
        "Không chạy được test trong Docker sandbox.",
        "Kiểm tra Docker đang chạy và image sandbox đã được pull.",
        True,
    ),
    ErrorSpec(
        "SANDBOX_DOCKER_UNAVAILABLE",
        "sandbox",
        503,
        "Docker không khả dụng",
        "Backend không gọi được Docker để chạy test.",
        "Mở Docker Desktop (hoặc kiểm tra /var/run/docker.sock khi chạy compose) rồi thử lại.",
        True,
    ),
    ErrorSpec(
        "GIT_ERROR",
        "git",
        500,
        "Lỗi git",
        "Thao tác git trong workspace thất bại.",
        "Kiểm tra git đã được cài trên máy chạy Backend.",
        True,
    ),
    ErrorSpec(
        "GIT_COMMAND_FAILED",
        "git",
        500,
        "Lệnh git thất bại",
        "Một lệnh git trong workspace trả về lỗi (code vẫn được giữ nguyên).",
        "Xem stderr của git trong chi tiết kỹ thuật.",
        True,
    ),
    ErrorSpec(
        "DATABASE_ERROR",
        "trace",
        502,
        "Không đọc/ghi được database",
        "Không kết nối được Supabase hoặc bảng chưa đúng cấu trúc.",
        _SUPABASE_HINT,
        True,
    ),
    ErrorSpec(
        "STORAGE_ERROR",
        "trace",
        502,
        "Không đọc/ghi được storage",
        "Không lưu hoặc tải được dữ liệu trace từ Supabase Storage.",
        _SUPABASE_HINT,
        True,
    ),
    ErrorSpec(
        "TRACE_NOT_FOUND",
        "trace",
        404,
        "Không tìm thấy trace",
        "Trace không tồn tại hoặc đã bị xóa.",
        "Làm mới danh sách trace. Trace chỉ xuất hiện sau khi pipeline chạy xong.",
        False,
    ),
    ErrorSpec(
        "CLIENT_NETWORK_ERROR",
        "client",
        0,
        "Không kết nối được Backend",
        "Trình duyệt không gửi/nhận được dữ liệu từ Backend.",
        "Kiểm tra Backend đang chạy và VITE_BACKEND_URL trỏ đúng địa chỉ (mặc định http://localhost:8000).",
        True,
    ),
    ErrorSpec(
        "CLIENT_STREAM_TIMEOUT",
        "client",
        0,
        "Mất tín hiệu từ Backend",
        "Không nhận được dữ liệu nào từ Backend trong 45 giây.",
        "Gửi lại yêu cầu. Kết quả lần trước (nếu có) vẫn được lưu trong trang Traces.",
        True,
    ),
    ErrorSpec(
        "CLIENT_STREAM_INCOMPLETE",
        "client",
        0,
        "Phiên kết thúc bất thường",
        "Kết nối đóng trước khi Backend báo hoàn tất.",
        "Gửi lại yêu cầu. Nếu lặp lại, kiểm tra log Backend.",
        True,
    ),
    ErrorSpec(
        "CLIENT_PARSE_ERROR",
        "client",
        0,
        "Dữ liệu từ Backend không đọc được",
        "Giao diện nhận được dữ liệu sai định dạng.",
        "Phiên bản UI và Backend có thể không khớp — cập nhật cả hai.",
        False,
    ),
    ErrorSpec(
        "CLIENT_HTTP_ERROR",
        "client",
        0,
        "Backend trả lỗi",
        "Backend trả về mã HTTP lỗi không kèm mô tả.",
        "Xem mã HTTP trong chi tiết kỹ thuật và log Backend.",
        True,
    ),
)

ERROR_CATALOG: dict[str, ErrorSpec] = {spec.code: spec for spec in _SPECS}

_PREFIX_FALLBACK: tuple[tuple[str, str], ...] = (
    ("LLM_", "LLM_ERROR"),
    ("WORKSPACE_", "WORKSPACE_ERROR"),
    ("SANDBOX_", "SANDBOX_ERROR"),
    ("GIT_", "GIT_ERROR"),
    ("AGENT_", "AGENT_INCOMPLETE"),
)


def get_error_spec(code: str) -> ErrorSpec:
    """Tra spec theo mã lỗi, fallback theo nhóm rồi về SYSTEM_ERROR.

    Args:
        code: Mã lỗi.

    Returns:
        ErrorSpec tương ứng (không bao giờ None).
    """
    spec = ERROR_CATALOG.get(code)
    if spec is not None:
        return spec
    for prefix, fallback in _PREFIX_FALLBACK:
        if code.startswith(prefix):
            return ERROR_CATALOG[fallback]
    return ERROR_CATALOG["SYSTEM_ERROR"]


def error_payload(
    code: str,
    detail: str | None = None,
    context: dict[str, Any] | None = None,
) -> ErrorEventPayload:
    """Dựng payload lỗi chuẩn từ mã lỗi qua catalog.

    Args:
        code: Mã lỗi.
        detail: Mô tả kỹ thuật (VD: message gốc của exception).
        context: Metadata kỹ thuật bổ sung.

    Returns:
        ErrorEventPayload đã điền title/message/hint/retryable.
    """
    spec = get_error_spec(code)
    return ErrorEventPayload(
        code=code,
        title=spec.title,
        message=spec.message,
        hint=spec.hint,
        retryable=spec.retryable,
        detail=detail,
        context=_jsonable(context) if context else None,
    )


def error_payload_from_exc(exc: Exception) -> ErrorEventPayload:
    """Dựng payload lỗi chuẩn từ exception (AppError hoặc bất kỳ).

    Args:
        exc: Exception cần chuyển đổi.

    Returns:
        ErrorEventPayload.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return error_payload(
            code,
            detail=getattr(exc, "message", None) or str(exc),
            context=getattr(exc, "context", None),
        )
    return error_payload("SYSTEM_ERROR", detail=f"{type(exc).__name__}: {exc}")


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    """Ép context về dạng JSON-serializable (giá trị lạ → str).

    Args:
        value: Dict context thô.

    Returns:
        Dict an toàn để serialize.
    """
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
