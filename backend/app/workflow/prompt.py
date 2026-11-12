"""Dựng thứ gửi cho LLM: system prompt (từ `prompts/*.md`) và bản hội thoại đã rút gọn (`compact`).

Tách khỏi vòng lặp agent (agent.py) để đọc/sửa prompt và chiến lược rút gọn mà không đụng logic điều khiển.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from app.config import tool_configs
from app.dtos.config import AgentConfig
from app.errors import AppError

_PROMPTS = Path(__file__).parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Đọc `prompts/<name>.md`.

    Args:
        name: Tên prompt (không đuôi).

    Returns:
        Nội dung prompt.

    Raises:
        AppError: Không có file prompt.
    """
    path = _PROMPTS / f"{name}.md"
    if not path.exists():
        raise AppError(f"Không tìm thấy prompt: {name}", code="PROMPT_NOT_FOUND", context={"path": str(path)})
    return path.read_text(encoding="utf-8").strip()


def sandbox_env_block() -> str:
    """Mô tả môi trường sandbox chạy test (sinh từ [tools.sandbox]) để nhúng vào prompt.

    Trước đây prompt không nói sandbox có gì → LLM viết test gọi `docker compose`,
    `import yaml`... → exit 127 / ModuleNotFoundError, phí hết lượt thử.
    """
    sb = tool_configs.tools.sandbox
    net = "KHÔNG có mạng" if sb.network == "none" else f"mạng `{sb.network}`"
    return (
        f"## Sandbox chạy code bạn được cung cấp\n"
        f"- `{sb.image}`, {net}, tối đa {sb.timeout_sec}s, Python + thư viện chuẩn + thư viện đã cài trong `.deps` "
        "(không docker, DB, Node, dịch vụ ngoài; cần tới thì dùng dữ liệu mock). **Test chuẩn = pytest** (tự cài khi chạy lần đầu; file `test_*.py`, hàm `test_*` hoặc `unittest.TestCase` đều chạy được).\n"
        "- File hạ tầng/cấu hình (Dockerfile, compose, YAML, SQL, .env...): viết đủ, test bằng kiểm tra tĩnh nội dung "
        "(chuỗi/`re`, `tomllib`, `json`), không khởi động service.\n"
        "- Code gọi dịch vụ ngoài: tách lớp truy cập, test bằng `unittest.mock`; import thư viện ngoài đặt trong hàm/try."
    )


def system_prompt(cfg: AgentConfig) -> str:
    """System prompt của agent (`prompts/agent.md` + mô tả sandbox khi được chạy lệnh).

    Args:
        cfg: Cấu hình agent.

    Returns:
        System prompt.
    """
    sandbox = sandbox_env_block() if {"test", "shell"} & set(cfg.tools) else ""
    return load_prompt("agent").replace("<<MAX_STEPS>>", str(cfg.max_steps)).replace("<<SANDBOX>>", sandbox)


_READ_TOOLS = frozenset({"read_file", "read_outline"})
_FLOOR = 150
_READ_KEEP = 3000


def compact(messages: list[dict[str, Any]], keep: int, limit: int, budget: int = 0) -> list[dict[str, Any]]:
    """Bản gửi LLM: kết quả công cụ ngoài `keep` lượt gần nhất rút còn `limit` ký tự (bản gốc giữ nguyên).

    Kết quả đọc file (`read_file`/`read_outline`) được giữ tới `_READ_KEEP` ký tự, trừ khi cùng lệnh đã được gọi lại sau đó.
    `budget` > 0: tổng ký tự tin nhắn không được vượt mức này (request quá lớn bị nhà cung cấp từ chối 413) → rút các kết quả công cụ
    cũ nhất xuống `_FLOOR` ký tự cho tới khi vừa.
    """
    turns = [i for i, x in enumerate(messages) if x["role"] == "assistant"]
    cutoff = turns[-keep] if len(turns) >= keep else 0
    calls: dict[str, tuple[str, str]] = {}
    last_call: dict[tuple[str, str], str] = {}
    for x in messages:
        for c in x.get("tool_calls") or []:
            key = (c["function"]["name"], c["function"]["arguments"])
            calls[c["id"]] = key
            last_call[key] = c["id"]
    out = []
    for i, x in enumerate(messages):
        c = x.get("content") or ""
        if x["role"] == "tool" and i < cutoff:
            key = calls.get(x.get("tool_call_id") or "")
            cap = _READ_KEEP if key and key[0] in _READ_TOOLS and last_call.get(key) == x.get("tool_call_id") else limit
            if len(c) > cap:
                x = {**x, "content": c[:cap] + "\n…[kết quả cũ đã rút gọn — gọi lại công cụ nếu cần chi tiết]"}
        out.append(x)
    if budget:
        size = sum(len(x.get("content") or "") + sum(len(c["function"]["arguments"]) for c in x.get("tool_calls") or []) for x in out)
        for i, x in enumerate(out):
            if size <= budget:
                break
            c = x.get("content") or ""
            if x["role"] == "tool" and i < cutoff and len(c) > _FLOOR:
                out[i] = {**x, "content": c[:_FLOOR] + "\n…[rút gọn do giới hạn kích thước request — gọi lại công cụ nếu cần]"}
                size -= len(c) - len(out[i]["content"])
    return out


__all__ = ["compact", "system_prompt"]
