"""Lệnh hệ thống (`/tên`) người dùng gõ ở đầu tin nhắn."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SlashCommand(BaseModel):
    """1 lệnh hệ thống.

    Attributes:
        name: Tên lệnh (gõ `/name`).
        title: Tên hiển thị.
        description: Mô tả ngắn cho menu gợi ý.
        mode: code = được sửa file · chat = chỉ đọc, trả lời.
        arg_hint: Gợi ý nội dung gõ sau lệnh.
        instruction: Chỉ dẫn thêm vào brief gửi model.
    """

    name: str
    title: str
    description: str
    mode: Literal["code", "chat"]
    arg_hint: str = ""
    instruction: str


__all__ = ["SlashCommand"]
