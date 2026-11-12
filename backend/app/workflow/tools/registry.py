"""Đăng ký công cụ của agent: decorator `@tool`, kiểm tra tham số, danh mục `TOOLS`.

Quy ước (mọi công cụ trong dự án PHẢI theo):
    - Định nghĩa trong `tools/definitions/<nhóm>.py` bằng `@tool(group=...)` trên 1 hàm async
      `(ctx: ToolContext, <tham số>...)`. Thân hàm CHỈ gọi sang 1 hàm trong `tools/handlers/` — không chứa logic.
    - Docstring bắt buộc, kiểu Google, tiếng Việt:
        dòng đầu = mô tả công cụ · `Dùng khi:` = khi nào nên dùng · `Args:` = mỗi tham số 1 dòng `tên: ý nghĩa`.
      Thiếu mô tả/`Dùng khi`/tham số nào chưa được giải thích → lỗi ngay lúc import (không thể đăng ký công cụ thiếu tài liệu).
    - Chữ ký + type hint (`str`, `int`, `bool`, `list[str]`) chính là schema: decorator tự sinh chữ ký đưa vào prompt
      và tự kiểm tra thiếu/thừa/sai kiểu tham số trước khi gọi hàm.
Xem danh mục: `python -m app.workflow.tools`.
"""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.dtos.tool import Param, ToolContext, ToolOutput
from app.errors import ToolError

if TYPE_CHECKING:
    pass

GROUPS = frozenset(
    {
        "workspace_read",
        "workspace_write",
        "test",
        "shell",
        "git_read",
        "git_write",
        "format",
        "install",
        "inspect",
        "finish",
        "ask_user",
    }
)


@dataclass(frozen=True)
class ToolSpec:
    """Mô tả 1 công cụ đã đăng ký."""

    name: str
    group: str
    desc: str
    when: str
    params: tuple[Param, ...]
    func: Callable[..., Awaitable[ToolOutput]] = field(repr=False)

    @property
    def usage(self) -> str:
        """Chữ ký đưa vào prompt, VD `read_file(path, start_line=1, max_lines=200)`."""
        return f"{self.name}({', '.join(p.usage for p in self.params)})"

    @property
    def args_doc(self) -> str:
        """Giải thích tham số, 1 dòng gọn."""
        return "; ".join(f"{p.name}: {p.doc}" for p in self.params)

    def schema(self) -> dict[str, Any]:
        """Tool schema kiểu OpenAI (`tools=[...]`) sinh từ chữ ký + docstring."""
        props = {p.name: {**_json_type(p.type), "description": p.doc} for p in self.params}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.desc,
                "parameters": {"type": "object", "properties": props, "required": [p.name for p in self.params if p.required]},
            },
        }

    async def call(self, ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
        """Kiểm tra tham số theo schema rồi gọi hàm công cụ.

        Tham số không có trong schema bị BỎ QUA (kèm nhắc nhẹ) — model hay thêm tham số tự nghĩ ra (end_line, encoding...),
        báo lỗi chỉ tốn thêm 1 lượt.

        Args:
            ctx: Ngữ cảnh lượt chạy.
            args: Tham số model gửi.

        Returns:
            Kết quả công cụ.
        """
        extra = sorted(set(args) - {p.name for p in self.params})
        out = await self.func(ctx, **self._bind({k: v for k, v in args.items() if k not in extra}))
        if extra:
            out.text += f"\n(Đã bỏ qua tham số không tồn tại: {', '.join(extra)}. Tham số hợp lệ của {self.name}: {', '.join(p.name for p in self.params) or '(không có)'})"
        return out

    def _bind(self, args: dict[str, Any]) -> dict[str, Any]:
        """Điền mặc định, kiểm thiếu tham số bắt buộc, ép kiểu.

        Args:
            args: Tham số model gửi (đã bỏ tham số lạ).

        Returns:
            Tham số để gọi hàm công cụ.

        Raises:
            ToolError: Thiếu tham số bắt buộc hoặc sai kiểu.
        """
        out: dict[str, Any] = {}
        for p in self.params:
            if args.get(p.name) is None:
                if p.required:
                    raise ToolError(f"thiếu tham số bắt buộc `{p.name}` ({p.doc})")
                continue
            out[p.name] = _coerce(p, args[p.name])
        return out


def _coerce(p: Param, value: Any) -> Any:
    """Ép giá trị model gửi về kiểu của tham số (model hay gửi số dạng chuỗi, list dạng JSON).

    Args:
        p: Tham số.
        value: Giá trị thô.

    Returns:
        Giá trị đúng kiểu.

    Raises:
        ToolError: Không ép được.
    """
    t = p.type
    try:
        if t is str:
            if not isinstance(value, str):
                raise TypeError
            return value
        if t is int:
            if isinstance(value, bool):
                raise TypeError
            return int(value)
        if t is bool:
            if isinstance(value, bool):
                return value
            raise TypeError
        if t == list[str]:
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise TypeError
            return value
    except (TypeError, ValueError) as e:
        raise ToolError(f"tham số `{p.name}` phải là {_type_name(t)} ({p.doc})") from e
    return value


def _json_type(t: Any) -> dict[str, Any]:
    """JSON schema của 1 kiểu Python (str, int, bool, list[str])."""
    if t == list[str]:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": {str: "string", int: "integer", bool: "boolean"}.get(t, "string")}


def _type_name(t: Any) -> str:
    """Tên kiểu Tiếng Việt cho thông báo lỗi."""
    return {str: "chuỗi", int: "số nguyên", bool: "true/false", list[str]: "danh sách chuỗi"}.get(t, str(t))


_SECTION = re.compile(r"^(Args|Dùng khi)\s*:\s*(.*)$")


def parse_doc(doc: str, name: str) -> tuple[str, str, dict[str, str]]:
    """Tách docstring thành (mô tả, dùng khi, {tham số: giải thích})."""
    lines = inspect.cleandoc(doc).splitlines()
    desc: list[str] = []
    when: list[str] = []
    args: dict[str, str] = {}
    section, current = "desc", None
    for raw in lines:
        line = raw.rstrip()
        m = _SECTION.match(line.strip()) if not raw.startswith((" ", "\t")) else None
        if m:
            section = "when" if m.group(1) == "Dùng khi" else "args"
            if section == "when" and m.group(2):
                when.append(m.group(2))
            continue
        if not line.strip():
            if section == "desc" and desc:
                section = "after"
            continue
        if section == "desc":
            desc.append(line.strip())
        elif section == "when":
            when.append(line.strip())
        elif section == "args":
            am = re.match(r"^\s*(\w+)\s*:\s*(.+)$", line)
            if am and len(line) - len(line.lstrip()) <= 4:
                current = am.group(1)
                args[current] = am.group(2).strip()
            elif current:
                args[current] += " " + line.strip()
    if not desc or not when:
        raise TypeError(f"công cụ `{name}`: docstring cần dòng mô tả và mục `Dùng khi:`")
    return " ".join(desc), " ".join(when), args


TOOLS: dict[str, ToolSpec] = {}


def tool(group: str) -> Callable[[Callable[..., Awaitable[ToolOutput]]], Callable[..., Awaitable[ToolOutput]]]:
    """Đăng ký 1 công cụ cho nhóm `group` (xem quy ước ở đầu file)."""
    if group not in GROUPS:
        raise ValueError(f"nhóm công cụ lạ '{group}'; hợp lệ: {sorted(GROUPS)}")

    def deco(fn: Callable[..., Awaitable[ToolOutput]]) -> Callable[..., Awaitable[ToolOutput]]:
        """Đăng ký hàm công cụ sau khi kiểm chữ ký và docstring.

        Args:
            fn: Hàm công cụ async `(ctx, ...)`.

        Returns:
            Chính hàm đó.
        """
        name = fn.__name__
        if name in TOOLS:
            raise ValueError(f"công cụ '{name}' đã được đăng ký")
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"công cụ `{name}` phải là hàm async")
        desc, when, doc_args = parse_doc(fn.__doc__ or "", name)
        hints = typing.get_type_hints(fn)
        sig = list(inspect.signature(fn).parameters.values())
        if not sig or sig[0].name != "ctx":
            raise TypeError(f"công cụ `{name}`: tham số đầu tiên phải là `ctx: ToolContext`")
        params: list[Param] = []
        for p in sig[1:]:
            if p.name not in doc_args:
                raise TypeError(f"công cụ `{name}`: tham số `{p.name}` chưa được giải thích trong mục `Args:` của docstring")
            if p.name not in hints:
                raise TypeError(f"công cụ `{name}`: tham số `{p.name}` thiếu type hint")
            required = p.default is inspect.Parameter.empty
            params.append(Param(p.name, hints[p.name], required, None if required else p.default, doc_args[p.name]))
        stale = set(doc_args) - {p.name for p in params}
        if stale:
            raise TypeError(f"công cụ `{name}`: `Args:` nhắc tham số không tồn tại: {sorted(stale)}")
        TOOLS[name] = ToolSpec(name, group, desc, when, tuple(params), fn)
        return fn

    return deco


def tools_for_groups(groups: frozenset[str] | set[str]) -> list[ToolSpec]:
    """Công cụ agent được dùng theo nhóm được cấp (giữ thứ tự khai báo)."""
    return [t for t in TOOLS.values() if t.group in groups]
