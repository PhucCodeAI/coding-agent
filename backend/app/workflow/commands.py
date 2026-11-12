"""Tách tin nhắn người dùng: `/lệnh` ở đầu = lệnh hệ thống, `@đường_dẫn` = đính kèm file trong workspace.

Người dùng gõ nguyên văn (VD `/test thêm case số âm cho @calc.py`); tin nhắn được lưu nguyên văn, chỉ brief gửi model được bổ sung
chỉ dẫn của lệnh và nội dung file đính kèm.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path

from app.dtos.command import SlashCommand

_COMMAND = re.compile(r"^\s*/([a-z][a-z0-9_-]*)\b")
_MENTION = re.compile(r"(?:^|\s)@([\w./-]+)")
MAX_FILES = 4
MAX_FILE_CHARS = 8000


@cache
def commands() -> dict[str, SlashCommand]:
    """Danh sách lệnh trong `prompts/commands.toml`.

    Returns:
        Tên lệnh → lệnh.
    """
    data = tomllib.loads((Path(__file__).parent / "prompts" / "commands.toml").read_text(encoding="utf-8"))
    return {c["name"]: SlashCommand(**c) for c in data["commands"]}


def command_of(prompt: str) -> SlashCommand | None:
    """Lệnh hệ thống ở đầu tin nhắn (lệnh lạ coi như chữ thường).

    Args:
        prompt: Tin nhắn người dùng.

    Returns:
        Lệnh, hoặc None.
    """
    m = _COMMAND.match(prompt)
    return commands().get(m.group(1)) if m else None


def mentions(prompt: str, files: list[str]) -> list[str]:
    """File được `@` trong tin nhắn và có trong workspace (giữ thứ tự, tối đa `MAX_FILES`).

    Args:
        prompt: Tin nhắn người dùng.
        files: File trong workspace.

    Returns:
        Đường dẫn file đính kèm.
    """
    existing = set(files)
    found = [p.rstrip(".,") for p in _MENTION.findall(prompt)]
    return list(dict.fromkeys(p for p in found if p in existing))[:MAX_FILES]


__all__ = ["MAX_FILE_CHARS", "command_of", "commands", "mentions"]
