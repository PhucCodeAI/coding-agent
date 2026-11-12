"""Chạy lệnh trong sandbox Docker (Docker CLI qua asyncio) — thuần hàm, cấu hình `[tools.sandbox]`. Mỗi lệnh 1 container mới, cô lập, timeout kép."""

from __future__ import annotations

import asyncio
import os
import uuid
from functools import lru_cache
from pathlib import Path

from loguru import logger

from app.config import tool_configs
from app.dtos.workspace import ExecutionResult
from app.errors import DockerUnavailableError

_MAX_OUTPUT_BYTES = 256 * 1024  # 256 KB


def _cfg():
    """Cấu hình `[tools.sandbox]` (đọc mỗi lần gọi, không giữ trạng thái)."""
    return tool_configs.tools.sandbox


@lru_cache(maxsize=1)
def _mounts() -> tuple[Path, Path] | None:
    """(thư mục workspace trên HOST, thư mục workspace trong container backend) hoặc None khi chạy local.

    Docker-out-of-Docker: backend chạy trong container và gọi docker daemon của host qua
    /var/run/docker.sock → path mount `-v` phải là path trên HOST. `HOST_WORKSPACE_DIR` dễ sai
    (phụ thuộc thư mục chạy `docker compose`) nên ưu tiên mount thật dò bằng `docker inspect`.
    """
    host_dir = os.getenv("HOST_WORKSPACE_DIR", "").strip()
    container_dir = os.getenv("WORKSPACE_BASE", "").strip()
    if container_dir:
        detected = _detect_host_mount(container_dir)
        if detected and detected != host_dir:
            logger.warning("HOST_WORKSPACE_DIR={} khác mount thật của container ({}) → dùng mount thật", host_dir or "(trống)", detected)
            host_dir = detected
    if not host_dir or not container_dir:
        return None
    return Path(host_dir), Path(container_dir).resolve()


def _detect_host_mount(container_dir: str) -> str | None:
    """Đường dẫn trên host của mount `container_dir` trong container backend (None nếu không dò được)."""
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "inspect", os.getenv("HOSTNAME", ""), "--format", "{{range .Mounts}}{{.Destination}}|{{.Source}}\n{{end}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        dest, _, src = line.partition("|")
        if dest.rstrip("/") == container_dir.rstrip("/") and src:
            return src
    return None


# --------------------------------------------------------
# Public API
# --------------------------------------------------------
async def run_command(
    workspace_path: Path,
    command: list[str],
    *,
    network: str | None = None,
    timeout_sec: int | None = None,
) -> ExecutionResult:
    """Thực thi một câu lệnh bên trong container Docker mount tới workspace.

    Args:
        workspace_path: Đường dẫn thư mục làm việc trên host cần mount.
        command: Lệnh và tham số cần chạy (VD: ['python', '-m', 'pytest']).
        network: ghi đè `--network` (mặc định `[tools.sandbox].network`); chỉ dùng cho lệnh cài thư viện.
        timeout_sec: ghi đè thời gian tối đa.

    Returns:
        ExecutionResult chứa mã thoát, stdout, stderr và thời gian chạy.

    Raises:
        DockerUnavailableError: Khi docker CLI không tồn tại hoặc daemon chết.
    """
    cfg = _cfg()
    container_name = f"sandbox_{uuid.uuid4().hex[:12]}"
    abs_path = _to_host_path(workspace_path.resolve())
    user = await asyncio.to_thread(_sandbox_user, workspace_path.resolve())
    docker_args = _build_docker_args(container_name, abs_path, command, network or cfg.network, user)
    limit = timeout_sec or cfg.timeout_sec

    logger.debug(
        "Sandbox exec | container={} cmd={}",
        container_name,
        " ".join(command),
    )

    process: asyncio.subprocess.Process | None = None
    loop = asyncio.get_running_loop()
    start = loop.time()

    try:
        process = await asyncio.create_subprocess_exec(
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise DockerUnavailableError(
            "Không tìm thấy docker CLI trên hệ thống",
            context={"image": cfg.image},
        ) from e
    except OSError as e:
        raise DockerUnavailableError(
            f"Không khởi tạo được tiến trình docker: {e}",
            context={"image": cfg.image},
        ) from e

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=limit,
        )
        elapsed_ms = int((loop.time() - start) * 1000)

        return ExecutionResult(
            exit_code=_normalize_returncode(process.returncode),
            stdout=_truncate(stdout_bytes, cfg.max_output_bytes),
            stderr=_truncate(stderr_bytes, cfg.max_output_bytes),
            duration_ms=elapsed_ms,
            is_timeout=False,
        )

    except asyncio.CancelledError:
        logger.info("Sandbox bị cancel, kill container {}", container_name)
        await asyncio.shield(_kill_container(container_name, process))
        raise

    except TimeoutError:
        elapsed_ms = int((loop.time() - start) * 1000)
        logger.warning(
            "Sandbox timeout sau {}s | container={}",
            limit,
            container_name,
        )
        await _kill_container(container_name, process)
        return ExecutionResult(
            exit_code=-1,
            stdout="",
            stderr=f"Lệnh bị hủy do chạy vượt quá {limit}s.",
            duration_ms=elapsed_ms,
            is_timeout=True,
        )


async def cleanup_orphans(prefix: str = "sandbox_") -> int:
    """Dọn dẹp các container rác còn sót lại từ lần chạy trước.

    Gọi trong lifespan shutdown để đảm bảo không leak container.

    Args:
        prefix: Prefix tên container cần dọn.

    Returns:
        Số container đã kill.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"name={prefix}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except (FileNotFoundError, OSError) as e:
        logger.warning("Không dọn được orphan container: {}", e)
        return 0

    container_ids = [c for c in stdout.decode().split() if c]
    if not container_ids:
        return 0

    logger.info("Dọn {} orphan container", len(container_ids))
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            *container_ids,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except (FileNotFoundError, OSError) as e:
        logger.warning("Kill orphan container thất bại: {}", e)
        return 0

    return len(container_ids)


# --------------------------------------------------------
# Internal helpers
# --------------------------------------------------------
def _to_host_path(path: Path) -> str:
    """Đổi path trong container backend sang path tương ứng trên host.

    Chỉ áp dụng khi có cả HOST_WORKSPACE_DIR và WORKSPACE_BASE (chạy qua
    docker-compose). Chạy local trực tiếp thì giữ nguyên path.

    Args:
        path: Path tuyệt đối đã resolve.

    Returns:
        Path dạng string dùng cho `docker run -v`.
    """
    mounts = _mounts()
    if mounts is None:
        return str(path)
    try:
        relative = path.relative_to(mounts[1])
    except ValueError:
        return str(path)
    return str(mounts[0] / relative)


_FALLBACK_USER = (1000, 1000)


def _sandbox_user(workspace: Path) -> str:
    """User chạy container sandbox (`--user`), bảo đảm user đó ghi được vào workspace.

    `[tools.sandbox] user = "auto"`: backend chạy bằng user thường → sandbox dùng đúng uid:gid đó (file trong workspace
    cùng chủ); backend chạy bằng root (trong Docker) → sandbox dùng 1000:1000 và workspace được chuyển chủ sang user này.
    Trên Linux, bind mount giữ nguyên quyền sở hữu nên lệch uid là pip/pytest không ghi được vào workspace.

    Args:
        workspace: Thư mục workspace trên máy chạy backend.

    Returns:
        Giá trị cho `--user`.
    """
    user = _cfg().user
    if user != "auto":
        return user
    if not hasattr(os, "getuid"):
        return f"{_FALLBACK_USER[0]}:{_FALLBACK_USER[1]}"
    if os.getuid() != 0:
        return f"{os.getuid()}:{os.getgid()}"
    uid, gid = _FALLBACK_USER
    for root, dirs, files in os.walk(workspace):
        for name in [".", *dirs, *files]:
            path = os.path.join(root, name)
            st = os.lstat(path)
            if (st.st_uid, st.st_gid) != (uid, gid):
                os.lchown(path, uid, gid)
    return f"{uid}:{gid}"


def _build_docker_args(
    container_name: str,
    workspace_abs_path: str,
    command: list[str],
    network: str,
    user: str,
) -> list[str]:
    """Đóng gói danh sách tham số docker run với đầy đủ security flags.

    Args:
        container_name: Tên container unique.
        workspace_abs_path: Đường dẫn tuyệt đối tới workspace trên host.
        command: Lệnh cần chạy trong container.
        network: Chế độ mạng của container (`none` khi chạy test, bridge khi cài thư viện).
        user: Giá trị `--user` (từ `_sandbox_user`).

    Returns:
        List tham số để truyền vào `docker run`.
    """
    cfg = _cfg()
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        network,
        "--memory",
        cfg.memory_limit,
        "--memory-swap",
        cfg.memory_limit,
        "--cpus",
        cfg.cpu_limit,
        "--pids-limit",
        str(cfg.pids_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        user,
        "-e",
        "PYTHONPATH=/workspace:/workspace/.deps",
        "-e",
        "HOME=/tmp",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-v",
        f"{workspace_abs_path}:/workspace",
        "-w",
        "/workspace",
        cfg.image,
        *command,
    ]


async def _kill_container(
    container_name: str,
    process: asyncio.subprocess.Process | None,
) -> None:
    """Kill container và tiến trình docker CLI một cách triệt để.

    Thứ tự:
        1. Gửi `docker kill <name>` để dừng container ngay lập tức.
        2. Terminate docker CLI process.
        3. Nếu CLI không chết trong 2s, kill cứng.

    Args:
        container_name: Tên container cần kill.
        process: Tiến trình docker CLI đang chạy.
    """
    try:
        kill_proc = await asyncio.create_subprocess_exec(
            "docker",
            "kill",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(kill_proc.wait(), timeout=5)
    except (TimeoutError, FileNotFoundError, OSError) as e:
        logger.warning(
            "docker kill {} thất bại: {} — fallback kill CLI",
            container_name,
            e,
        )

    if process is not None and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()


# ============================================================
# Module-level helpers
# ============================================================
def _normalize_returncode(returncode: int | None) -> int:
    """Chuẩn hóa returncode None (process chưa exit) thành -1.

    Args:
        returncode: Mã thoát thô từ asyncio.subprocess.

    Returns:
        Mã thoát hợp lệ (-1 nếu None).
    """
    if returncode is None:
        return -1
    return returncode


def _truncate(data: bytes, limit: int = _MAX_OUTPUT_BYTES) -> str:
    """Decode bytes UTF-8 và truncate nếu vượt quá giới hạn.

    Giữ đầu và cuối output (quan trọng cho traceback), bỏ giữa nếu quá dài.

    Args:
        data: Bytes thô từ stdout/stderr.
        limit: Số byte tối đa giữ lại.

    Returns:
        Chuỗi đã decode và truncate (có marker '...[truncated]...' nếu cắt).
    """
    if not data:
        return ""

    truncated = False
    if len(data) > limit:
        half = limit // 2
        data = data[:half] + b"\n...[truncated]...\n" + data[-half:]
        truncated = True

    text = data.decode("utf-8", errors="replace")
    if truncated:
        logger.debug("Output bị truncate xuống {} byte", limit)
    return text
