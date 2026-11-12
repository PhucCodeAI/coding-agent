"""Nạp cấu hình: `config/config.toml` (hạ tầng) + `config/tool_config.toml` (LLM, tool, agent) + `.env` (secret).

Model cấu hình ở `app/dtos/config.py`. Sai cấu hình → lỗi ngay lúc import (backend không khởi động).
Đổi đường dẫn: biến môi trường APP_CONFIG_PATH / TOOL_CONFIG_PATH.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from app.dtos.config import AppConfig, ToolConfig

BACKEND_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BACKEND_DIR / "config"
_ENV_PATH = BACKEND_DIR / ".env"
load_dotenv(_ENV_PATH, override=False)


def _resolve_config_path(path: Path | None = None) -> Path:
    """Xác định đường dẫn file config.toml cần đọc.

    Ưu tiên: tham số truyền vào > biến môi trường APP_CONFIG_PATH >
    backend/config/config.toml.

    Args:
        path: Đường dẫn tường minh do caller chỉ định.

    Returns:
        Đường dẫn tuyệt đối tới file config.toml.

    Raises:
        FileNotFoundError: Khi file config không tồn tại.
    """
    if path is None:
        env_path = os.getenv("APP_CONFIG_PATH")
        path = Path(env_path) if env_path else CONFIG_DIR / "config.toml"
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file config tại: {path}")
    return path


def load_config(path: Path | None = None) -> AppConfig:
    """Đọc và validate cấu hình từ file TOML, hợp nhất với secrets từ env.

    Args:
        path: Đường dẫn tùy chọn tới file config.toml.

    Returns:
        AppConfig đã được validate.

    Raises:
        FileNotFoundError: Khi file config không tồn tại.
        RuntimeError: Khi thiếu biến môi trường bắt buộc.
        ValidationError: Khi cấu trúc TOML không khớp schema.
    """
    resolved = _resolve_config_path(path)
    with resolved.open("rb") as f:
        data = tomllib.load(f)
    return AppConfig(**data)


@lru_cache(maxsize=1)
def get_configs() -> AppConfig:
    """Trả về singleton AppConfig đã được cache.

    Returns:
        AppConfig instance.
    """
    return load_config()


configs = get_configs()


def _resolve_path(path: Path | None = None) -> Path:
    """Xác định đường dẫn tool_config.toml.

    Args:
        path: Đường dẫn tường minh.

    Returns:
        Path tuyệt đối.

    Raises:
        FileNotFoundError: Không tìm thấy file.
    """
    if path is None:
        env_path = os.getenv("TOOL_CONFIG_PATH")
        path = Path(env_path) if env_path else CONFIG_DIR / "tool_config.toml"
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy tool config tại: {path}")
    return path


def load_tool_config(path: Path | None = None) -> ToolConfig:
    """Đọc + validate tool_config.toml.

    Args:
        path: Đường dẫn tùy chọn.

    Returns:
        ToolConfig.
    """
    with _resolve_path(path).open("rb") as f:
        return ToolConfig(**tomllib.load(f))


@lru_cache(maxsize=1)
def get_tool_configs() -> ToolConfig:
    """Singleton ToolConfig.

    Returns:
        ToolConfig đã cache.
    """
    return load_tool_config()


tool_configs = get_tool_configs()

__all__ = ["BACKEND_DIR", "CONFIG_DIR", "configs", "tool_configs"]
