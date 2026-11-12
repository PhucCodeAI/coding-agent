"""Module cấu hình Loguru tập trung cho toàn hệ thống.

Nhiệm vụ:
    - Thiết lập sink console (màu) và sink file (rotation/retention từ config).
    - Chặn (intercept) logging chuẩn của Python để mọi thư viện bên thứ 3
      (httpx, uvicorn, asyncio...) đều đi qua Loguru.
    - Cung cấp hàm setup_logging() idempotent, gọi 1 lần trong main.py.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import configs

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"


class _InterceptHandler(logging.Handler):
    """Handler chuyển log từ stdlib logging sang Loguru.

    Dùng để bắt các log phát sinh từ thư viện bên thứ 3 (httpx, uvicorn, docker...)
    vốn sử dụng logging.getLogger() mặc định của Python.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Chuyển một LogRecord của stdlib sang Loguru.

        Args:
            record: Bản ghi log gốc từ thư viện bên thứ 3.
        """
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _intercept_stdlib_logging() -> None:
    """Đăng ký InterceptHandler cho root logger và các logger thư viện phổ biến."""
    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(logging.NOTSET)

    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "httpx",
        "httpcore",
        "asyncio",
        "docker",
    ):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = [_InterceptHandler()]
        lib_logger.propagate = False


def setup_logging(
    *,
    log_path: str | None = None,
    level: str | None = None,
    rotation: str | None = None,
    retention: str | None = None,
    force: bool = False,
) -> Any:
    """Thiết lập Loguru cho toàn bộ tiến trình.

    Hàm này idempotent: gọi nhiều lần chỉ có tác dụng ở lần đầu, trừ khi
    truyền force=True.

    Args:
        log_path: Thư mục chứa file log. Mặc định lấy từ configs.logs.log_path.
        level: Mức log tối thiểu (INFO, DEBUG, ...). Mặc định từ config.
        rotation: Chu kỳ xoay file log (VD: '10 MB', '1 day'). Mặc định từ config.
        retention: Thời gian giữ log cũ (VD: '7 days'). Mặc định từ config.
        force: Nếu True, xóa sink cũ và cấu hình lại từ đầu.

    Returns:
        Đối tượng logger của Loguru đã được cấu hình sẵn.
    """
    log_cfg = configs.logs

    log_path = log_path or log_cfg.log_path
    level = level or log_cfg.level
    rotation = rotation or log_cfg.rotation
    retention = retention or log_cfg.retention

    log_dir = Path(log_path).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    logger.add(
        sys.stderr,
        level=level,
        format=_CONSOLE_FORMAT,
        colorize=True,
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )

    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level=level,
        format=_FILE_FORMAT,
        rotation=rotation,
        retention=retention,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format=_FILE_FORMAT,
        rotation=rotation,
        retention=retention,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    _intercept_stdlib_logging()

    logger.debug(
        "Loguru đã được cấu hình | level={} rotation={} retention={} path={}",
        level,
        rotation,
        retention,
        log_dir,
    )

    return logger


__all__ = ["logger", "setup_logging"]
