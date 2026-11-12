"""Thao tác Git tự động trong workspace bằng Git CLI qua asyncio — thuần hàm, cấu hình đọc từ `[tools.git]`.

Đặc điểm chính:
    - Parse `git status --porcelain -z` (null-separated) để xử lý đúng filename chứa space và ký tự đặc biệt.
    - Raise GitCommandError khi exit code != 0 (không silent failure).
    - Tự sinh .gitignore chuẩn Python (nội dung ở `[tools.git] gitignore`) để tránh add rác vào commit.
    - core.autocrlf=false để ổn định line ending giữa host/sandbox.
Công cụ cho agent (`git_status`, `git_diff`, `git_commit`) gọi các hàm này qua `tools/handlers/git.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from app.config import tool_configs
from app.errors import GitCommandError

_IGNORED_PARTS = frozenset({".git", ".deps", "__pycache__", ".pytest_cache", ".venv"})


def _cfg():
    """Cấu hình `[tools.git]` (đọc mỗi lần gọi, không giữ trạng thái)."""
    return tool_configs.tools.git


def _gitignore_content() -> str:
    """Nội dung .gitignore mặc định cho workspace mới."""
    return (_cfg().gitignore or "").lstrip("\n") or "__pycache__/\n*.py[cod]\n.venv/\n.env\n"


# --------------------------------------------------------
# Public API
# --------------------------------------------------------
async def init_repo_if_needed(workspace_path: Path) -> bool:
    """Khởi tạo Git repo và cấu hình identity nếu chưa tồn tại.

    Args:
        workspace_path: Thư mục cần kiểm tra hoặc khởi tạo.

    Returns:
        True nếu vừa khởi tạo repo mới, False nếu đã tồn tại.
    """
    if (workspace_path / ".git").exists():
        return False

    logger.info("Khởi tạo git repo tại {}", workspace_path)
    await _exec_git(workspace_path, ["init"])

    await _exec_git(workspace_path, ["config", "user.name", _cfg().user_name])
    await _exec_git(workspace_path, ["config", "user.email", _cfg().user_email])

    await _exec_git(workspace_path, ["config", "core.autocrlf", "false"])
    await _write_gitignore(workspace_path)
    return True


async def commit_changes(
    workspace_path: Path,
    message: str,
) -> tuple[str, list[str]]:
    """Stage toàn bộ thay đổi và tạo commit mới.

    Args:
        workspace_path: Thư mục git repository.
        message: Thông điệp commit.

    Returns:
        Tuple (commit_hash_short, changed_files). Nếu không có gì thay đổi,
        trả về ("", []).

    Raises:
        GitCommandError: Khi git add hoặc git commit thất bại.
    """
    await init_repo_if_needed(workspace_path)

    changed_files = await _get_changed_files(workspace_path)
    if not changed_files:
        logger.debug("Không có file thay đổi để commit")
        return "", []

    await _exec_git(workspace_path, ["add", "-A"])

    safe_message = _sanitize_commit_message(message, _cfg().commit_subject_max_len)
    await _exec_git(workspace_path, ["commit", "-m", safe_message])

    commit_hash = await _get_head_short_hash(workspace_path)
    logger.info(
        "Đã commit {} file(s) | hash={} | message={!r}",
        len(changed_files),
        commit_hash,
        safe_message,
    )
    return commit_hash, changed_files


async def log(workspace_path: Path, limit: int = 50) -> list[dict[str, object]]:
    """Lịch sử commit (mới nhất trước) kèm danh sách file mỗi commit.

    Args:
        workspace_path: Thư mục repo.
        limit: Số commit tối đa.

    Returns:
        List {hash, message, author, date, files}. Chưa có repo/commit → [].
    """
    if not (workspace_path / ".git").exists():
        return []
    try:
        _, out = await _exec_git(
            workspace_path,
            ["log", f"-n{limit}", "--name-only", "--pretty=format:%x1e%h%x1f%s%x1f%an%x1f%aI"],
        )
    except GitCommandError:
        return []
    commits: list[dict[str, object]] = []
    for record in out.split("\x1e"):
        lines = [ln for ln in record.strip("\n").split("\n") if ln]
        if not lines:
            continue
        fields = lines[0].split("\x1f")
        if len(fields) < 4:
            continue
        commits.append(
            {
                "hash": fields[0],
                "message": fields[1],
                "author": fields[2],
                "date": fields[3],
                "files": lines[1:],
            }
        )
    return commits


async def show_diff(workspace_path: Path, commit_hash: str) -> str:
    """Unified diff của 1 commit.

    Args:
        workspace_path: Thư mục repo.
        commit_hash: Hash (đã validate hex ở router).

    Returns:
        Diff dạng text.

    Raises:
        GitCommandError: Hash không tồn tại.
    """
    _, out = await _exec_git(workspace_path, ["show", "--format=", "--no-color", "--no-ext-diff", commit_hash])
    return out


async def working_changes(workspace_path: Path) -> dict[str, object]:
    """Thay đổi CHƯA commit (file mới / sửa / xóa) + unified diff.

    Lượt fail không commit → trước đây tab "Thay đổi" trống dù agent đã ghi file.
    Workspace chưa có repo / chưa có commit nào → mọi file là file mới.

    Returns:
        {"files": [{"path", "status": added|modified|deleted}], "diff": str}.
    """
    root = Path(workspace_path)
    has_repo = (root / ".git").exists()
    has_head = False
    if has_repo:
        code, _ = await _exec_git_soft(root, ["rev-parse", "--verify", "HEAD"])
        has_head = code == 0

    files: list[dict[str, str]] = []
    untracked: set[str] = set()
    if has_repo:
        _, raw = await _exec_git(root, ["status", "--porcelain", "-z", "-uall"])
        entries = raw.split("\0") if raw else []
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            code2, path = entry[:2], entry[3:]
            if "R" in code2 or "C" in code2:
                i += 1
            status = "deleted" if "D" in code2 else "added" if code2 in ("??", "A ") or not has_head else "modified"
            if code2 == "??" or not has_head:
                untracked.add(path)
            files.append({"path": path, "status": status})
    else:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and rel != ".gitignore" and not _IGNORED_PARTS.intersection(p.relative_to(root).parts) and p.suffix not in (".pyc", ".pyo"):
                files.append({"path": rel, "status": "added"})
                untracked.add(rel)

    parts: list[str] = []
    if has_head:
        _, tracked = await _exec_git_soft(root, ["diff", "HEAD", "--no-color", "--no-ext-diff"])
        if tracked:
            parts.append(tracked)
    for f in files:
        if f["path"] in untracked and f["status"] != "deleted":
            parts.append(_new_file_diff(root, f["path"]))
    return {"files": files, "diff": "\n".join(p.rstrip("\n") for p in parts if p) + ("\n" if parts else "")}


async def _exec_git_soft(workspace_path: Path, args: list[str]) -> tuple[int, str]:
    """Như _exec_git nhưng không raise khi exit != 0 (trả code)."""
    try:
        return await _exec_git(workspace_path, args)
    except GitCommandError as e:
        return e.exit_code if isinstance(getattr(e, "exit_code", None), int) else 1, ""


# --------------------------------------------------------
# Internal helpers
# --------------------------------------------------------
async def _get_changed_files(workspace_path: Path) -> list[str]:
    """Lấy danh sách file thay đổi qua `git status --porcelain -z`.

    Dùng -z để separator là null byte — an toàn với filename chứa space,
    newline, dấu ngoặc kép.

    Args:
        workspace_path: Thư mục git repository.

    Returns:
        Danh sách đường dẫn tương đối.

    Raises:
        GitCommandError: Khi git status thất bại.
    """
    _, raw = await _exec_git(
        workspace_path,
        ["status", "--porcelain", "-z", "-uall"],
    )

    if not raw:
        return []

    files: list[str] = []
    entries = raw.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status = entry[:2]
        files.append(entry[3:])
        if "R" in status or "C" in status:
            i += 1

    return files


async def _get_head_short_hash(workspace_path: Path) -> str:
    """Đọc hash ngắn của HEAD.

    Args:
        workspace_path: Thư mục git repository.

    Returns:
        Hash ngắn 7 ký tự.

    Raises:
        GitCommandError: Khi repo không có commit nào.
    """
    _, out = await _exec_git(
        workspace_path,
        ["rev-parse", "--short", "HEAD"],
    )
    if not out:
        raise GitCommandError(
            "Không đọc được HEAD hash sau khi commit",
            args=["rev-parse", "--short", "HEAD"],
            exit_code=0,
            stderr="empty output",
        )
    return out.strip()


async def _write_gitignore(workspace_path: Path) -> None:
    """Ghi file .gitignore mặc định nếu chưa tồn tại.

    Args:
        workspace_path: Thư mục git repository.
    """
    gitignore = workspace_path / ".gitignore"
    if gitignore.exists():
        return
    await asyncio.to_thread(
        gitignore.write_text,
        _gitignore_content(),
        encoding="utf-8",
    )


async def _exec_git(
    workspace_path: Path,
    args: list[str],
) -> tuple[int, str]:
    """Chạy lệnh git trong thư mục chỉ định.

    Args:
        workspace_path: Thư mục chứa repository.
        args: Danh sách tham số lệnh git.

    Returns:
        Tuple (exit_code, stdout). Nếu exit_code != 0, raise GitCommandError.

    Raises:
        GitCommandError: Khi git trả về mã thoát khác 0 hoặc CLI không tồn tại.
    """
    safe_dir = f"safe.directory={Path(workspace_path).resolve()}"
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            safe_dir,
            *args,
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise GitCommandError(
            "Không tìm thấy git CLI trên hệ thống",
            args=args,
            exit_code=-1,
            stderr=str(e),
        ) from e

    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    code = process.returncode if process.returncode is not None else -1

    if code != 0:
        raise GitCommandError(
            f"git {' '.join(args)} thất bại (exit {code})",
            args=args,
            exit_code=code,
            stderr=stderr,
        )

    return code, stdout.rstrip("\n")


# ============================================================
# Module-level helpers
# ============================================================
def _new_file_diff(root: Path, rel: str, max_chars: int = 60_000) -> str:
    """Unified diff "file mới" cho file chưa được git theo dõi."""
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return f"diff --git a/{rel} b/{rel}\nnew file mode 100644\nBinary files /dev/null and b/{rel} differ\n"
    text = text[:max_chars]
    lines = text.splitlines()
    body = "".join(f"+{ln}\n" for ln in lines)
    return f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def _sanitize_commit_message(message: str, max_len: int = 72) -> str:
    """Chuẩn hóa commit message: bỏ newline, cắt an toàn theo từ.

    Args:
        message: Message gốc.
        max_len: Độ dài tối đa của subject line.

    Returns:
        Message đã chuẩn hóa.
    """
    one_line = " ".join(message.split())
    if len(one_line) <= max_len:
        return one_line
    cut = one_line[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "..."


__all__ = ["commit_changes", "init_repo_if_needed", "log", "show_diff", "working_changes"]
