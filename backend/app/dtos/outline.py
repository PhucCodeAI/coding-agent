"""Cấu trúc rút gọn của 1 file Python (dùng cho công cụ read_outline)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ImportGroup:
    """1 dòng import.

    Attributes:
        module: Module nguồn ("" = `import x` thuần).
        names: Tên được import.
        type_only: Nằm trong `if TYPE_CHECKING:`.
    """

    module: str
    names: list[str]
    type_only: bool = False


@dataclass
class FuncInfo:
    """1 hàm / method.

    Attributes:
        name: Tên.
        params: Tham số (đã bỏ self/cls).
        returns: Kiểu trả về.
        is_async: Hàm async.
        decorators: Decorator.
        doc: Dòng đầu docstring.
        line: Dòng bắt đầu.
        end_line: Dòng kết thúc.
    """

    name: str
    params: str
    returns: str = ""
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)
    doc: str = ""
    line: int = 0
    end_line: int = 0


@dataclass
class ClassInfo:
    """1 class.

    Attributes:
        name: Tên.
        bases: Lớp cha.
        decorators: Decorator.
        doc: Dòng đầu docstring.
        line: Dòng bắt đầu.
        end_line: Dòng kết thúc.
        init: Tham số khởi tạo (từ __init__, hoặc field của dataclass/NamedTuple).
        attrs: Thuộc tính lớp.
        methods: Method.
        classes: Class lồng.
    """

    name: str
    bases: list[str]
    decorators: list[str]
    doc: str
    line: int
    end_line: int
    init: str | None
    attrs: list[str] = field(default_factory=list)
    methods: list[FuncInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)


@dataclass
class LooseCode:
    """Đoạn code cấp module không thuộc hàm/class.

    Attributes:
        line: Dòng bắt đầu.
        text: Tóm tắt 1 dòng.
    """

    line: int
    text: str


@dataclass
class FileOutline:
    """Cấu trúc rút gọn của 1 file Python.

    Attributes:
        path: Đường dẫn.
        lines: Số dòng.
        doc: Dòng đầu docstring module.
        imports: Import.
        classes: Class.
        functions: Hàm cấp module.
        code: Code lẻ cấp module.
        error: Lỗi phân tích (cú pháp sai).
    """

    path: str
    lines: int
    doc: str
    imports: list[ImportGroup]
    classes: list[ClassInfo]
    functions: list[FuncInfo]
    code: list[LooseCode]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Dạng dict (JSON-able) của toàn bộ cấu trúc."""
        return asdict(self)


__all__ = ["ClassInfo", "FileOutline", "FuncInfo", "ImportGroup", "LooseCode"]
