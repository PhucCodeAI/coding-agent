"""Logic thật của các công cụ (được gọi từ `definitions.py`)."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from time import perf_counter
from typing import Any

from app.dtos.events import CommitEventPayload, FileEventPayload, SandboxEventPayload
from app.dtos.tool import ToolContext, ToolOutput
from app.dtos.workspace import FileAction, FileEdit
from app.errors import AppError, ToolError
from app.integrations import git as git_cmds
from app.integrations import ruff
from app.integrations import sandbox as sandbox_cmds
from app.repositories.workspace import preview_edits
from app.utils.log_summary import summarize_log
from app.utils.sse import format_sse


def tail(text: str, n: int) -> str:
    """Giữ `n` ký tự cuối của `text` (thêm … nếu bị cắt)."""
    return text if len(text) <= n else "…" + text[-n:]


def syntax_error(path: str, source: str) -> str | None:
    """Thông báo lỗi cú pháp nếu `path` là .py và `source` không compile được; ngược lại None."""
    if not path.endswith(".py"):
        return None
    try:
        compile(source, path, "exec", dont_inherit=True)
    except SyntaxError as e:
        line = (e.text or "").strip()[:100]
        return f"{path}:{e.lineno}: {e.msg}" + (f" — `{line}`" if line else "")
    except ValueError as e:
        return f"{path}: {e}"
    return None


def emit_file_event(ctx: ToolContext, res: Any) -> None:
    """Phát SSE `file` cho kết quả ghi/xóa file `res`."""
    ctx.agent.emit(
        format_sse(
            "file",
            FileEventPayload(
                path=res.path,
                action=res.action,
                lines_added=res.lines_added,
                lines_removed=res.lines_removed,
            ),
        )
    )


def check_fresh(ctx: ToolContext, path: str, exists: bool) -> None:
    """Chặn ghi đè/sửa file đã có mà agent chưa đọc, hoặc đã đổi (VD ruff format) sau lần đọc."""
    if not exists:
        return
    agent = ctx.agent
    seen = agent.state.read_versions.get(path)
    if seen is None:
        raise ToolError(f"'{path}' đã tồn tại — hãy read_file trước khi sửa/ghi đè để không làm mất nội dung")
    if seen != agent.file_versions.get(path, 0):
        raise ToolError(f"'{path}' đã thay đổi sau lần đọc của bạn — read_file lại rồi mới sửa")


async def list_files(ctx: ToolContext, path_prefix: str) -> ToolOutput:
    """Liệt kê file trong workspace.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path_prefix: Lọc theo tiền tố đường dẫn.

    Returns:
        Kết quả cho agent.
    """
    files = [f for f in await ctx.agent.context.workspace.list_files() if f.startswith(path_prefix) and f != ".gitignore"]
    if not files:
        return ToolOutput("(không có file nào)" + (f" với tiền tố '{path_prefix}'" if path_prefix else ""))
    shown = files[:200]
    return ToolOutput("\n".join(shown) + (f"\n… và {len(files) - 200} file nữa" if len(files) > 200 else ""))


async def read_file(ctx: ToolContext, path: str, start_line: int, max_lines: int) -> ToolOutput:
    """Đọc file (kèm số dòng, không thuộc nội dung).

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path: Đường dẫn file.
        start_line: Dòng bắt đầu.
        max_lines: Số dòng (≤400).

    Returns:
        Kết quả cho agent.
    """
    agent = ctx.agent
    if not path.strip():
        raise ToolError("`path` không được rỗng")
    start = max(1, start_line)
    count = min(400, max(1, max_lines))
    try:
        content = await agent.context.workspace.read_file(path)
    except AppError as e:
        raise ToolError(e.message) from e
    except UnicodeDecodeError as e:
        raise ToolError("file nhị phân, không đọc được dạng text") from e
    ctx.agent.state.read_versions[path] = agent.file_versions.get(path, 0)
    lines = content.splitlines()
    chunk = lines[start - 1 : start - 1 + count]
    head = f"{path} — {len(lines)} dòng, hiển thị {start}–{start + len(chunk) - 1}"
    body = "\n".join(f"{start + i:>4}| {ln}" for i, ln in enumerate(chunk))
    more = f"\n… còn {len(lines) - (start - 1 + len(chunk))} dòng (đọc tiếp bằng start_line)" if start - 1 + len(chunk) < len(lines) else ""
    return ToolOutput(f"{head}\n(số dòng ở đầu chỉ để tham chiếu — KHÔNG nằm trong nội dung file)\n{body}{more}")


async def search_text(ctx: ToolContext, query: str, path_prefix: str) -> ToolOutput:
    """Tìm chuỗi trong mọi file.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        query: Chuỗi cần tìm.
        path_prefix: Lọc theo tiền tố đường dẫn.

    Returns:
        Kết quả cho agent.
    """
    if not query.strip():
        raise ToolError("`query` không được rỗng")
    needle = query.lower()
    ws = ctx.agent.context.workspace
    hits: list[str] = []
    for f in await ws.list_files():
        if not f.startswith(path_prefix) or f == ".gitignore":
            continue
        try:
            text = await ws.read_file(f)
        except (AppError, UnicodeDecodeError):
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            if needle in ln.lower():
                hits.append(f"{f}:{i}: {ln.strip()[:140]}")
                if len(hits) >= 40:
                    return ToolOutput("\n".join(hits) + "\n… (đủ 40 kết quả, thu hẹp từ khóa để xem thêm)")
    return ToolOutput("\n".join(hits) if hits else f"không tìm thấy '{needle}'")


async def _after_write(ctx: ToolContext, res: Any, path: str) -> str:
    """Cập nhật phiên bản file, phát event, kiểm cú pháp, format ruff; trả ghi chú cho agent."""
    agent, state = ctx.agent, ctx.agent.state
    agent.file_versions[path] = agent.file_versions.get(path, 0) + 1
    emit_file_event(ctx, res)
    notes: list[str] = []
    content = await agent.context.workspace.read_file(path)
    err = syntax_error(path, content)
    changed = False
    if path.endswith(".py") and not err:
        fmt = await ruff.format_files(agent.context.workspace_dir, [path])
        after = await agent.context.workspace.read_file(path)
        changed = after != content
        if changed:
            agent.file_versions[path] += 1
            notes.append("đã format bằng ruff — read_file lại nếu cần sửa tiếp")
        if fmt.failed:
            notes.append("ruff không format được (có thể lỗi cú pháp)")
    if not changed:
        state.read_versions[path] = agent.file_versions[path]
    if err:
        notes.append(f"⚠ LỖI CÚ PHÁP: {err} — sửa ngay")
    return f"+{res.lines_added}/-{res.lines_removed} dòng" + ("; " + "; ".join(notes) if notes else "")


_PLACEHOLDER = re.compile(r"^\s*<\s*\d+\s*dòng[^>]{0,80}>\s*$|Ghi chú hệ thống: để tiết kiệm")


def _reject_placeholder(content: str) -> None:
    """Từ chối nội dung là chuỗi giữ chỗ (model chép lại ghi chú rút gọn thay vì code thật)."""
    if _PLACEHOLDER.search(content):
        raise ToolError("nội dung là chuỗi giữ chỗ / ghi chú hệ thống, không phải code thật — hãy gửi TOÀN BỘ nội dung file")


_TRUNCATED = ("was never closed", "unexpected EOF", "unterminated triple-quoted", "unterminated string literal")


def _looks_truncated(path: str, content: str, err: str) -> bool:
    """Lỗi cú pháp có phải do nội dung bị cắt cụt không.

    Args:
        path: Đường dẫn file.
        content: Nội dung model gửi.
        err: Thông báo lỗi cú pháp (từ `syntax_error`).

    Returns:
        True khi lỗi là ngoặc/chuỗi chưa đóng, file kết thúc bằng rào Markdown ``` hoặc lỗi nằm ở dòng cuối file.
    """
    if any(s in err for s in _TRUNCATED):
        return True
    lines = [x for x in content.splitlines() if x.strip()]
    if lines and lines[-1].strip().startswith("```"):
        return True
    m = re.match(rf"{re.escape(path)}:(\d+):", err)
    return bool(m and lines and int(m.group(1)) >= len(content.rstrip().splitlines()))


async def _reject_bad_python(ctx: ToolContext, path: str, content: str, whole: bool) -> None:
    """Chặn ghi code Python lỗi: kiểm bằng ruff TRƯỚC khi ghi, có lỗi thì không ghi (file cũ giữ nguyên).

    Nội dung có dấu hiệu bị cắt cụt (model dừng giữa chừng) → hạ ưu tiên nhà cung cấp vừa trả lời để lần gọi lại dùng model khác.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path: Đường dẫn file.
        content: Nội dung file SAU khi ghi/sửa.
        whole: True = write_file (gửi cả file), False = edit_file.

    Raises:
        ToolError: Code lỗi cú pháp / lỗi ruff.
    """
    if not path.endswith(".py"):
        return
    err = syntax_error(path, content)
    if err and whole and _looks_truncated(path, content, err):
        ctx.agent.demote_provider(f"tham số write_file bị cắt cụt ({path})")
        raise ToolError(f"nội dung có vẻ bị CẮT CỤT ({err}) nên file KHÔNG được ghi — gửi lại TOÀN BỘ nội dung file, đầy đủ tới dòng cuối")
    lint = await ruff.check_source(path, content)
    if lint or err:
        what = "nội dung" if whole else "file sau khi sửa"
        raise ToolError(f"ruff báo lỗi ở {what} nên KHÔNG ghi (file giữ nguyên) — sửa rồi gọi lại:\n{lint or err}")


async def write_file(ctx: ToolContext, path: str, content: str) -> ToolOutput:
    """Tạo/ghi đè cả file (file đã có: read_file trước). .py tự kiểm cú pháp + format.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path: Đường dẫn file.
        content: TOÀN BỘ nội dung file (được rỗng).

    Returns:
        Kết quả cho agent.
    """
    agent = ctx.agent
    if not path.strip():
        raise ToolError("`path` không được rỗng")
    _reject_placeholder(content)
    await _reject_bad_python(ctx, path, content, whole=True)
    async with agent.file_lock(path):
        exists = path in set(await agent.context.workspace.list_files())
        check_fresh(ctx, path, exists)
        try:
            res = await agent.context.workspace.apply_file_action(FileAction(path=path, action="create", content=content))
        except (AppError, ValueError) as e:
            raise ToolError(getattr(e, "message", str(e))) from e
        info = await _after_write(ctx, res, path)
    return ToolOutput(f"{'Đã ghi đè' if exists else 'Đã tạo'} {path}: {info}")


async def edit_file(ctx: ToolContext, path: str, old_text: str, new_text: str) -> ToolOutput:
    """Thay 1 đoạn trong file đã có (read_file trước).

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path: File đã có.
        old_text: Đoạn gốc, chép CHÍNH XÁC, duy nhất trong file.
        new_text: Đoạn thay thế (rỗng = xóa).

    Returns:
        Kết quả cho agent.
    """
    agent = ctx.agent
    _reject_placeholder(new_text)
    if not path.strip() or not old_text:
        raise ToolError("`path` và `old_text` (đoạn gốc, chép CHÍNH XÁC từ file, duy nhất trong file) không được rỗng")
    async with agent.file_lock(path):
        exists = path in set(await agent.context.workspace.list_files())
        if not exists:
            raise ToolError(f"'{path}' chưa tồn tại — dùng write_file để tạo")
        check_fresh(ctx, path, True)
        edits = [FileEdit(old_text=old_text, new_text=new_text)]
        try:
            after = preview_edits(await agent.context.workspace.read_file(path), edits, path)
        except (AppError, ValueError) as e:
            raise ToolError(getattr(e, "message", str(e))) from e
        await _reject_bad_python(ctx, path, after, whole=False)
        try:
            res = await agent.context.workspace.apply_file_action(FileAction(path=path, action="update", edits=edits))
        except (AppError, ValueError) as e:
            raise ToolError(getattr(e, "message", str(e))) from e
        info = await _after_write(ctx, res, path)
    return ToolOutput(f"Đã sửa {path}: {info}")


async def delete_file(ctx: ToolContext, path: str) -> ToolOutput:
    """Xóa file.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path: File cần xóa.

    Returns:
        Kết quả cho agent.
    """
    agent = ctx.agent
    if not path.strip():
        raise ToolError("`path` không được rỗng")
    async with agent.file_lock(path):
        try:
            res = await agent.context.workspace.apply_file_action(FileAction(path=path, action="delete"))
        except (AppError, ValueError) as e:
            raise ToolError(getattr(e, "message", str(e))) from e
        agent.file_versions[path] = agent.file_versions.get(path, 0) + 1
        emit_file_event(ctx, res)
    return ToolOutput(f"Đã xóa {path}")


async def format_files(ctx: ToolContext, paths: list[str]) -> ToolOutput:
    """Format file .py bằng ruff.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        paths: Danh sách file .py.

    Returns:
        Kết quả cho agent.
    """
    agent = ctx.agent
    if not paths:
        raise ToolError('`paths` phải là danh sách đường dẫn .py, VD ["app/a.py"]')
    res = await ruff.format_files(agent.context.workspace_dir, paths)
    for p in paths:
        agent.file_versions[p] = agent.file_versions.get(p, 0) + 1
    return ToolOutput(f"đã format: {res.formatted or '(không đổi)'}; lỗi: {res.failed or 'không'}")


_PYTHON_ONLY = ("python", "python3")


async def _run(ctx: ToolContext, command: list[str], label: str, *, network: str | None = None, timeout_sec: int | None = None) -> ToolOutput:
    """Chạy lệnh trong sandbox: ghi span + sự kiện `sandbox`, trả PASS/FAIL kèm log tóm tắt.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        command: Lệnh (argv).
        label: Nhãn ngắn cho span.
        network: Mạng của container (None = theo cấu hình).
        timeout_sec: Thời gian tối đa (None = theo cấu hình).

    Returns:
        Kết quả cho agent; lỗi chỉ gửi bản tóm tắt, log đầy đủ lấy bằng `read_log`.
    """
    agent = ctx.agent
    t0 = perf_counter()
    result = await sandbox_cmds.run_command(agent.context.workspace_dir, command, network=network, timeout_sec=timeout_sec)
    log = "\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p)
    ctx.agent.state.last_log = log
    ran_none = "Ran 0 tests" in log or "no tests ran" in log or (result.exit_code == 5 and "pytest" in " ".join(command))
    passed = result.exit_code == 0 and not ran_none
    agent.context.collector.add_span(
        name=f"Sandbox · {label}",
        span_type="sandbox",
        duration_ms=int((perf_counter() - t0) * 1000),
        input_data={"command": command},
        output_data={"log": tail(log, 3000)},
        metadata={"exit_code": result.exit_code, "is_timeout": result.is_timeout},
        status="OK" if passed else "ERROR",
    )
    agent.emit(
        format_sse(
            "sandbox",
            SandboxEventPayload(
                command=command,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                is_timeout=result.is_timeout,
                stdout_summary=tail(log, 500),
            ),
        )
    )
    head = "PASS ✓" if passed else ("TIMEOUT" if result.is_timeout else f"FAIL ✗ (exit {result.exit_code})")
    if ran_none:
        head = "KHÔNG CÓ TEST NÀO CHẠY (pattern không khớp file test, hoặc lỗi import) ✗"
    if passed:
        return ToolOutput(f"{head}\n{tail(log, 800)}", ok=True)
    return ToolOutput(f"{head}\n{summarize_log(log)}\n(log đầy đủ: read_log)", ok=False)


async def ensure_pytest(ctx: ToolContext) -> str | None:
    """Pytest chưa có trong `.deps` → cài (sandbox có mạng, chỉ lệnh pip). Trả thông báo lỗi nếu cài không được."""
    if (ctx.agent.context.workspace_dir / ".deps" / "pytest").is_dir():
        return None
    out = await install(ctx, ["pytest"], record=False)
    return None if out.ok else out.text


async def run_tests(ctx: ToolContext, pattern: str) -> ToolOutput:
    """Chạy test (pytest) trong sandbox; lỗi trả bản tóm tắt: nơi sai + lỗi gốc.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        pattern: Mẫu tên file test (VD test_unit_*.py).

    Returns:
        Kết quả cho agent.
    """
    pattern = pattern.strip() or "test_*.py"
    if not pattern.endswith(".py"):
        pattern = pattern.removesuffix("*") + ("*.py" if pattern.endswith("*") else ".py")
    if not re.fullmatch(r"[\w*?./-]{1,60}", pattern):
        raise ToolError("`pattern` chỉ gồm chữ/số/_ . - * ? (VD test_unit_*.py)")
    files = await ctx.agent.context.workspace.list_files()
    matched = sorted(f for f in files if f.endswith(".py") and (fnmatch(f, pattern) or fnmatch(f.rsplit("/", 1)[-1], pattern)))
    if not matched:
        shown = ", ".join(f for f in files if f.endswith(".py") and "test" in f.rsplit("/", 1)[-1]) or "(không có file test nào)"
        raise ToolError(f"không có file test khớp '{pattern}'. File test hiện có: {shown}")
    if (err := await ensure_pytest(ctx)) is not None:
        raise ToolError(f"chưa có pytest và cài không được: {err}")
    return await _run(ctx, ["python", "-m", "pytest", "-q", "--no-header", "-rfE", "--tb=short", "-p", "no:cacheprovider", *matched[:30]], f"pytest {pattern}")


_PKG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?((==|>=|<=|~=|!=|<|>)[A-Za-z0-9_.*+!\-]+)?")


async def install(ctx: ToolContext, packages: list[str], *, record: bool = True) -> ToolOutput:
    """Pip install vào `.deps` của workspace (container có mạng) và ghi vào requirements.txt."""
    pkgs = [p.strip() for p in packages if p.strip()]
    if not pkgs or len(pkgs) > 10 or not all(_PKG.fullmatch(p) for p in pkgs):
        raise ToolError('`packages` gồm 1–10 tên thư viện hợp lệ trên PyPI, VD ["requests==2.32.3", "pydantic"]')
    cfg = sandbox_cmds._cfg()
    out = await _run(
        ctx,
        ["python", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "--target", ".deps", *pkgs],
        "pip install " + " ".join(pkgs)[:50],
        network=cfg.install_network,
        timeout_sec=cfg.install_timeout_sec,
    )
    if out.ok:
        root = ctx.agent.context.workspace_dir
        req = root / "requirements.txt"
        pkgs_to_record = pkgs if record else []
        have = req.read_text(encoding="utf-8").splitlines() if req.exists() else []
        names = {re.split(r"[=<>!~\[]", x, maxsplit=1)[0].strip().lower() for x in have}
        add = [p for p in pkgs_to_record if re.split(r"[=<>!~\[]", p, maxsplit=1)[0].lower() not in names]
        if add:
            req.write_text("\n".join([*have, *add]) + "\n", encoding="utf-8")
        ign = root / ".gitignore"
        text = ign.read_text(encoding="utf-8") if ign.exists() else ""
        if ".deps/" not in text:
            ign.write_text(text + ("\n" if text and not text.endswith("\n") else "") + ".deps/\n", encoding="utf-8")
        out = ToolOutput(f"Đã cài {', '.join(pkgs)} vào .deps (chạy test/lệnh tự thấy thư viện) và ghi requirements.txt", ok=True)
    return out


async def install_packages(ctx: ToolContext, packages: list[str]) -> ToolOutput:
    """Cài thư viện Python (pip, có mạng) vào workspace và ghi requirements.txt.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        packages: 1–10 tên thư viện PyPI, có thể kèm phiên bản (VD "requests==2.32.3").

    Returns:
        Kết quả cho agent.
    """
    return await install(ctx, packages)


async def run_command(ctx: ToolContext, argv: list[str]) -> ToolOutput:
    """Chạy 1 lệnh python trong sandbox (không docker/pip/mạng).

    Args:
        ctx: Ngữ cảnh lượt chạy.
        argv: Danh sách, bắt đầu bằng python, VD ["python","-c","print(1)"].

    Returns:
        Kết quả cho agent.
    """
    if not argv:
        raise ToolError('`argv` phải là danh sách chuỗi không rỗng, VD ["python", "-m", "unittest", "-v"]')
    if argv[0] not in _PYTHON_ONLY:
        raise ToolError("sandbox chỉ chạy được python/python3 (không docker, pip, npm, shell, mạng). Dùng `python ...`; cần thư viện → install_packages")
    return await _run(ctx, argv, " ".join(argv)[:60])


async def git_status(ctx: ToolContext) -> ToolOutput:
    """Danh sách file thay đổi chưa commit.

    Args:
        ctx: Ngữ cảnh lượt chạy.

    Returns:
        Kết quả cho agent.
    """
    res = await git_cmds.working_changes(ctx.agent.context.workspace_dir)
    files = [f for f in res["files"] if f["path"] != ".gitignore"]  # type: ignore[index]
    if not files:
        return ToolOutput("không có thay đổi chưa commit")
    return ToolOutput("\n".join(f"{f['status']:<9}{f['path']}" for f in files))


async def git_diff(ctx: ToolContext, path: str) -> ToolOutput:
    """Diff các thay đổi chưa commit.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        path: Chỉ file này.

    Returns:
        Kết quả cho agent.
    """
    res = await git_cmds.working_changes(ctx.agent.context.workspace_dir)
    diff = str(res["diff"])
    if path:
        blocks = re.split(r"(?m)^(?=diff --git )", diff)
        diff = "".join(b for b in blocks if f"b/{path}" in b.split("\n", 1)[0])
    return ToolOutput(diff or "(không có thay đổi)")


async def git_commit(ctx: ToolContext, message: str) -> ToolOutput:
    """Commit mọi thay đổi trong workspace.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        message: Commit message.

    Returns:
        Kết quả cho agent.
    """
    if not message.strip():
        raise ToolError("`message` không được rỗng")
    agent = ctx.agent
    h, files = await git_cmds.commit_changes(workspace_path=agent.context.workspace_dir, message=message)
    if not h:
        return ToolOutput("không có thay đổi để commit")
    agent.emit(format_sse("commit", CommitEventPayload(hash=h, message=message, files=files)))
    agent.committed = True
    return ToolOutput(f"đã commit {h} ({len(files)} file)")


_STATUSES = ("done", "failed", "blocked")


def _status(status: str) -> str:
    """Chuẩn hóa `status` của `finish` (done | failed | blocked)."""
    s = status.strip().lower()
    if s not in _STATUSES:
        raise ToolError("`status` phải là done | failed | blocked")
    return s


async def finish(ctx: ToolContext, status: str, answer: str, commit_message: str) -> ToolOutput:
    """Kết thúc lượt và gửi câu trả lời cuối cho người dùng.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        status: Done | failed | blocked.
        answer: Câu trả lời cho người dùng (Markdown ngắn: đã làm gì, file chính, cách chạy test, lưu ý).
        commit_message: Commit message (tuỳ chọn; bỏ trống = tự sinh).

    Returns:
        Kết quả cho agent.
    """
    if not answer.strip():
        raise ToolError("`answer` không được rỗng")
    return ToolOutput(ctx.agent.finish(_status(status), answer.strip(), commit_message.strip()), stop=True)


def _outline_body(target: Any, rel: str, **kw: bool) -> str:
    """Outline của file, thay dòng tiêu đề (đường dẫn tuyệt đối) bằng đường dẫn tương đối."""
    from app.utils.py_outline import outline

    lines = outline(target, **kw).splitlines()
    head = lines[0].split(" · ", 1)
    return "\n".join([rel + (" · " + head[1] if len(head) > 1 else ""), *lines[1:]])


async def read_outline(ctx: ToolContext, path: str) -> ToolOutput:
    """Cấu trúc rút gọn 1 file Python (có số dòng) để quyết định đọc phần nào."""
    target = ctx.agent.context.workspace_dir / path
    if not path.endswith(".py") or not target.is_file():
        raise ToolError(f"'{path}' không phải file .py có sẵn — dùng read_file")
    return ToolOutput(_outline_body(target, path, docs=True, types=True, lines=True, private=True))


async def ask_user(ctx: ToolContext, question: str, options: list[str], recommended: str) -> ToolOutput:
    """Hỏi người dùng 1 câu trắc nghiệm và chờ trả lời (người dùng luôn có thể tự nhập câu khác).

    Args:
        ctx: Ngữ cảnh lượt chạy.
        question: Câu hỏi ngắn.
        options: 2–4 lựa chọn.
        recommended: Lựa chọn khuyến nghị (phải nằm trong options; bỏ trống = lựa chọn đầu).

    Returns:
        Kết quả cho agent.
    """
    opts = [o.strip() for o in options if o.strip()][:4]
    if not question.strip() or len(opts) < 2:
        raise ToolError("cần `question` và 2–4 `options`")
    return ToolOutput(await ctx.agent.ask_user(question.strip(), opts, recommended.strip()))


async def read_log(ctx: ToolContext, grep: str, tail_lines: int) -> ToolOutput:
    """Log đầy đủ của lệnh sandbox gần nhất, lọc theo `grep` (không phân biệt hoa thường) hoặc N dòng cuối."""
    log = ctx.agent.state.last_log
    if not log:
        raise ToolError("chưa có log nào — hãy chạy test / lệnh trước")
    lines = log.splitlines()
    if grep.strip():
        rx = re.compile(re.escape(grep.strip()), re.I)
        hits = [f"{i + 1}: {ln}" for i, ln in enumerate(lines) if rx.search(ln)]
        return ToolOutput("\n".join(hits[:60]) or f"không có dòng nào chứa '{grep}' ({len(lines)} dòng log)")
    n = max(1, min(tail_lines, 120))
    return ToolOutput(f"(dòng {max(1, len(lines) - n + 1)}–{len(lines)}/{len(lines)})\n" + "\n".join(lines[-n:]))


async def find_files(ctx: ToolContext, pattern: str) -> ToolOutput:
    """Tìm file theo mẫu tên (glob).

    Args:
        ctx: Ngữ cảnh lượt chạy.
        pattern: VD "*.py", "test_*.py", "services/*".

    Returns:
        Kết quả cho agent.
    """
    if not pattern.strip():
        raise ToolError("`pattern` không được rỗng (VD *.py, test_*.py, services/*)")
    files = await ctx.agent.context.workspace.list_files()
    hit = [f for f in files if fnmatch(f, pattern) or fnmatch(f.rsplit("/", 1)[-1], pattern)]
    return ToolOutput("\n".join(hit[:100]) + (f"\n… +{len(hit) - 100}" if len(hit) > 100 else "") if hit else f"không có file khớp '{pattern}'")


async def check_syntax(ctx: ToolContext, paths: list[str]) -> ToolOutput:
    """Kiểm tra cú pháp Python nhanh (không chạy code).

    Args:
        ctx: Ngữ cảnh lượt chạy.
        paths: Danh sách file .py; bỏ trống = mọi file .py.

    Returns:
        Kết quả cho agent.
    """
    root = ctx.agent.context.workspace_dir
    targets = [p for p in paths if p.strip()] or [f for f in await ctx.agent.context.workspace.list_files() if f.endswith(".py")]
    bad: list[str] = []
    for p in targets[:80]:
        f = root / p
        if p.endswith(".py") and f.is_file() and (e := syntax_error(p, f.read_text(encoding="utf-8", errors="replace"))):
            bad.append(e)
    return ToolOutput("\n".join(bad) if bad else f"cú pháp OK ({min(len(targets), 80)} file)", ok=not bad)


async def pip_list(ctx: ToolContext) -> ToolOutput:
    """Liệt kê thư viện đã cài thêm trong workspace.

    Args:
        ctx: Ngữ cảnh lượt chạy.

    Returns:
        Kết quả cho agent.
    """
    deps = ctx.agent.context.workspace_dir / ".deps"
    names = sorted(p.name[:-10] for p in deps.glob("*.dist-info")) if deps.is_dir() else []
    return ToolOutput("Python 3.12 (thư viện chuẩn) + đã cài trong .deps:\n" + ("\n".join(names) if names else "(chưa cài gì)"))


async def git_log(ctx: ToolContext, limit: int) -> ToolOutput:
    """Lịch sử commit gần đây của workspace.

    Args:
        ctx: Ngữ cảnh lượt chạy.
        limit: Số commit (≤30).

    Returns:
        Kết quả cho agent.
    """
    commits = await git_cmds.log(ctx.agent.context.workspace_dir, max(1, min(limit, 30)))
    if not commits:
        return ToolOutput("chưa có commit nào")
    return ToolOutput("\n".join(f"{c['hash']} {c['message']} ({len(c['files'])} file)" for c in commits))  # type: ignore[arg-type]


async def system_info(ctx: ToolContext) -> ToolOutput:
    """Xem tài nguyên hệ thống và giới hạn của sandbox: CPU, RAM, tải, ổ đĩa, Python, mạng.

    Args:
        ctx: Ngữ cảnh lượt chạy.

    Returns:
        Kết quả cho agent.
    """
    import os
    import platform
    import shutil

    root = ctx.agent.context.workspace_dir
    disk = shutil.disk_usage(root)
    rows = [f"OS: {platform.system()} {platform.release()} · Python {platform.python_version()}", f"CPU: {os.cpu_count()} lõi"]
    try:
        rows.append("Tải 1/5/15 phút: " + " / ".join(f"{x:.2f}" for x in os.getloadavg()))
    except OSError:
        pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        rows.append(f"RAM tổng: {total / 2**30:.1f} GiB")
    except (ValueError, OSError, AttributeError):
        pass
    rows.append(f"Ổ đĩa workspace: trống {disk.free / 2**30:.1f} / {disk.total / 2**30:.1f} GiB")
    sb = sandbox_cmds._cfg()
    rows.append(
        f"Sandbox: {sb.image}, RAM {sb.memory_limit}, {sb.cpu_limit} CPU, tối đa {sb.timeout_sec}s, mạng '{sb.network}' (cài thư viện: '{sb.install_network}')"
    )
    return ToolOutput("\n".join(rows))
