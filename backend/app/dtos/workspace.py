"""Thao tác file trong workspace, kết quả format (ruff) và kết quả lệnh sandbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FileActionType = Literal["create", "update", "delete"]


_EDIT_KEY_ALIASES = {
    "search": "old_text",
    "find": "old_text",
    "old": "old_text",
    "before": "old_text",
    "original": "old_text",
    "replace": "new_text",
    "new": "new_text",
    "after": "new_text",
    "replacement": "new_text",
}


def _unescape_flat(text: str) -> str:
    r"""Nội dung nhiều dòng bị escape 2 lần ('\\n' dạng chữ, KHÔNG có xuống dòng thật) → giải escape.

    Đã gặp: model ghi cả file test thành 1 dòng chứa '\\n' → SyntaxError, tốn 1 vòng gỡ lỗi.
    Chỉ áp dụng khi không có newline thật và có ≥ 2 chuỗi '\\n' (tránh đụng code hợp lệ 1 dòng).
    """
    if "\n" in text or text.count("\\n") < 2:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def _rename_edit_keys(edit: Any) -> Any:
    """Đổi tên khóa edit lạ (search/replace, old/new...) về `old_text`/`new_text`."""
    if not isinstance(edit, dict):
        return edit
    return {_EDIT_KEY_ALIASES.get(k, k): v for k, v in edit.items()}


def _merge_split_edits(edits: list[Any]) -> list[Any]:
    """[{old_text}, {new_text}] liền nhau (thứ tự bất kỳ) → [{old_text, new_text}]."""
    out: list[Any] = []
    i = 0
    while i < len(edits):
        cur = edits[i]
        nxt = edits[i + 1] if i + 1 < len(edits) else None
        if isinstance(cur, dict) and isinstance(nxt, dict):
            only_old = "old_text" in cur and "new_text" not in cur
            only_new = "new_text" in cur and "old_text" not in cur
            if only_old and "new_text" in nxt and "old_text" not in nxt:
                out.append({**cur, "new_text": nxt["new_text"]})
                i += 2
                continue
            if only_new and "old_text" in nxt and "new_text" not in nxt:
                out.append({**nxt, "new_text": cur["new_text"]})
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


class FileEdit(BaseModel):
    """Một cặp search/replace để chỉnh sửa cục bộ file đã tồn tại.

    Thuật toán apply (thực thi tại Workspace):
        1. Thử khớp exact `old_text` trong file.
        2. Nếu fail, thử khớp không phân biệt trailing whitespace mỗi dòng.
        3. Nếu vẫn fail → raise EditApplyError để TurnRunner re-prompt LLM.
    """

    model_config = ConfigDict(extra="ignore")

    old_text: str = Field(
        ...,
        min_length=1,
        description="Đoạn text gốc cần tìm. Phải unique trong file để tránh sửa nhầm.",
    )
    new_text: str = Field(
        ...,
        description="Đoạn text thay thế. Cho phép rỗng để biểu diễn hành vi xóa.",
    )


class FileAction(BaseModel):
    """Mô tả một thao tác tạo, sửa đổi hoặc xóa file trong workspace.

    Quy tắc sử dụng theo `action`:
        - create: bắt buộc có `content` (toàn bộ nội dung file mới).
        - update: bắt buộc có `edits` (danh sách search/replace). Nếu LLM muốn
          rewrite toàn bộ file cũ, dùng action="create" để ghi đè.
        - delete: chỉ cần `path`, bỏ trống `content` và `edits`.
    """

    model_config = ConfigDict(extra="ignore")

    path: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Đường dẫn tương đối của file (VD: 'app/calc.py', 'test_calc.py')",
    )
    action: FileActionType = Field(
        default="create",
        description="Hành động: create | update | delete",
    )
    content: str | None = Field(
        default=None,
        description="Toàn bộ nội dung file. Bắt buộc khi action='create'.",
    )
    edits: list[FileEdit] | None = Field(
        default=None,
        description="Danh sách search/replace. Bắt buộc khi action='update'.",
    )
    language: str | None = Field(
        default=None,
        description="Ngôn ngữ để UI syntax highlight (VD: 'python'). None = tự đoán.",
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        """Chuẩn hóa và kiểm tra đường dẫn an toàn.

        Args:
            v: Đường dẫn thô do LLM sinh ra.

        Returns:
            Đường dẫn đã chuẩn hóa (dùng '/' làm separator).

        Raises:
            ValueError: Khi path tuyệt đối, chứa '..', hoặc trỏ vào .git.
        """
        normalized = v.replace("\\", "/").strip()
        if not normalized:
            raise ValueError("Đường dẫn không được rỗng")

        pure = PurePosixPath(normalized)
        if pure.is_absolute():
            raise ValueError(f"Đường dẫn tuyệt đối không được phép: '{v}'")
        if ".." in pure.parts:
            raise ValueError(f"Đường dẫn chứa '..' không được phép: '{v}'")
        if ".git" in pure.parts:
            raise ValueError(f"Không được thao tác trong .git: '{v}'")

        return str(pure)

    @model_validator(mode="before")
    @classmethod
    def _repair_llm_shape(cls, data: Any) -> Any:
        """Sửa các lỗi hình dạng output LLM hay gặp TRƯỚC khi validate (đỡ tốn lượt thử).

        - 1 edit bị tách thành 2 object `{old_text}` + `{new_text}` (đã gặp với model free,
          tự sửa 2 lần vẫn sai → lượt fail) → ghép lại thành 1 cặp.
        - Tên khóa khác: `search/replace`, `find/replace`, `old/new`, `before/after`.
        - `action="update"` nhưng gửi `content` đầy đủ, không có `edits` → coi là ghi đè (`create`).
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        edits = data.get("edits")
        if isinstance(edits, list):
            data["edits"] = _merge_split_edits([_rename_edit_keys(e) for e in edits])
        if isinstance(data.get("content"), str):
            data["content"] = _unescape_flat(data["content"])
        if isinstance(data.get("edits"), list):
            data["edits"] = [
                {k: _unescape_flat(v) if k in ("old_text", "new_text") and isinstance(v, str) else v for k, v in e.items()} if isinstance(e, dict) else e
                for e in data["edits"]
            ]
        if data.get("action") == "update" and data.get("content") and not data.get("edits"):
            data["action"] = "create"
            data["edits"] = None
        return data

    @model_validator(mode="after")
    def _validate_action_fields(self) -> FileAction:
        """Kiểm tra tính nhất quán giữa `action` và các field đi kèm.

        Returns:
            Instance đã validate.

        Raises:
            ValueError: Khi field không khớp với action.
        """
        if self.action == "create":
            if self.content is None:
                raise ValueError("action='create' bắt buộc phải có 'content' (được phép rỗng, VD __init__.py)")
            if self.edits:
                raise ValueError("action='create' không được có 'edits'")
        elif self.action == "update":
            if not self.edits:
                raise ValueError("action='update' bắt buộc phải có 'edits'")
            if self.content is not None:
                raise ValueError("action='update' không nhận 'content'. Muốn rewrite toàn bộ file, dùng action='create'.")
        elif self.action == "delete":
            if self.content or self.edits:
                raise ValueError("action='delete' chỉ nhận 'path'")
        return self


class ApplyResult(BaseModel):
    """Kết quả của một thao tác FileAction đã được thực thi lên workspace.

    Dùng để emit FileEventPayload và ghi trace span.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Đường dẫn tương đối của file")
    action: FileActionType = Field(..., description="Hành động đã thực thi")
    lines_added: int = Field(default=0, ge=0, description="Số dòng thêm vào")
    lines_removed: int = Field(default=0, ge=0, description="Số dòng xóa đi")
    bytes_written: int = Field(default=0, ge=0, description="Số byte ghi vào đĩa")


@dataclass
class FormatResult:
    """Kết quả 1 lần format."""

    formatted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    output: str = ""


class ExecutionResult(BaseModel):
    """Kết quả thực thi lệnh bên trong Docker Sandbox."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int = Field(..., description="Mã thoát của tiến trình (0 là thành công)")
    stdout: str = Field(default="", description="Dữ liệu đầu ra chuẩn")
    stderr: str = Field(default="", description="Thông báo lỗi chuẩn")
    duration_ms: int = Field(..., description="Thời gian thực thi tính bằng mili-giây")
    is_timeout: bool = Field(default=False, description="Đánh dấu tiến trình bị ngắt do quá thời gian")


__all__ = ["ApplyResult", "ExecutionResult", "FileAction", "FileEdit", "FormatResult"]
