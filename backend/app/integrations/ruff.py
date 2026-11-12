"""Auto-format code agent sinh ra bằng ruff (`[tools.formatter]`) — thuần hàm.

Chạy ở phía Backend (ruff chỉ phân tích cú pháp, không thực thi code) trên
đúng các file .py vừa được ghi trong workspace:
    - `ruff format`                       : định dạng (line length cấu hình).
    - `ruff check --select I --fix` (tùy) : sắp xếp import.
Lỗi (VD file sai cú pháp) KHÔNG raise — trả về trong FormatResult để agent
tiếp tục; test sẽ bắt lỗi thật.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from loguru import logger

from app.config import tool_configs
from app.dtos.workspace import FormatResult

_PY_SUFFIXES = (".py", ".pyi")
_ERROR_PATH_RE = re.compile(r"(?:-->|Failed to parse)\s+([^\s:]+):\d+")


def _cfg():
    """Cấu hình `[tools.formatter]` (đọc mỗi lần gọi, không giữ trạng thái)."""
    return tool_configs.tools.formatter


async def format_files(workspace: Path, paths: list[str]) -> FormatResult:
    """Format các file .py (đường dẫn tương đối, đã qua path jail).

    Args:
        workspace: Thư mục workspace.
        paths: Đường dẫn tương đối các file vừa ghi.

    Returns:
        FormatResult (file nào format được / không được).
    """
    cfg = _cfg()
    targets = sorted({p for p in paths if p.endswith(_PY_SUFFIXES) and (workspace / p).is_file()})
    if not targets:
        return FormatResult()

    outputs: list[str] = []
    if cfg.sort_imports:
        _, out = await _run(
            workspace,
            ["check", "--select", "I", "--fix", "--quiet", "--no-cache", f"--line-length={cfg.line_length}", "--", *targets],
        )
        if out:
            outputs.append(out)

    code, out = await _run(
        workspace,
        ["format", "--quiet", "--no-cache", f"--line-length={cfg.line_length}", "--", *targets],
    )
    if out:
        outputs.append(out)
    failed = sorted({m for m in _ERROR_PATH_RE.findall("\n".join(outputs)) if m in targets})
    if code != 0 and not failed:
        failed = targets
    result = FormatResult(
        formatted=[p for p in targets if p not in failed],
        failed=failed,
        output="\n".join(outputs).strip(),
    )
    if result.failed:
        logger.info("ruff không format được {}: {}", result.failed, result.output[:300])
    return result


async def _run(cwd: Path, args: list[str]) -> tuple[int, str]:
    """Chạy ruff với `args` trong thư mục `cwd`.

    Args:
        cwd: Thư mục chạy.
        args: Tham số của ruff.

    Returns:
        (mã thoát, output gộp).
    """
    cfg = _cfg()
    base = [cfg.executable] if cfg.executable else [sys.executable, "-m", "ruff"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *base,
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.timeout_sec)
    except (TimeoutError, OSError) as e:
        return -1, f"ruff không chạy được: {type(e).__name__}: {e}"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace").strip()


LINT_RULES = "E9,F63,F7,F82"


async def check_source(path: str, source: str) -> str | None:
    """Kiểm code Python bằng `ruff check` (qua stdin, không ghi file) trước khi ghi vào workspace.

    Args:
        path: Đường dẫn tương đối (ruff dùng để nhận diện .py/.pyi và in vị trí lỗi).
        source: Nội dung cần kiểm.

    Returns:
        Danh sách lỗi (mỗi dòng 1 lỗi, kèm số dòng) hoặc None nếu sạch / không phải file Python.
    """
    if not path.endswith(_PY_SUFFIXES):
        return None
    cfg = _cfg()
    base = [cfg.executable] if cfg.executable else [sys.executable, "-m", "ruff"]
    args = ["check", "--isolated", "--no-cache", "--select", LINT_RULES, "--output-format", "concise", "--stdin-filename", path, "-"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *base, *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(source.encode("utf-8")), timeout=cfg.timeout_sec)
    except (TimeoutError, OSError) as e:
        logger.warning("ruff check không chạy được: {}", e)
        return None
    if proc.returncode == 0:
        return None
    lines = [ln for ln in stdout.decode("utf-8", errors="replace").splitlines() if ln.startswith(path + ":")]
    return "\n".join(lines[:15]) or None


__all__ = ["LINT_RULES", "FormatResult", "check_source", "format_files"]
