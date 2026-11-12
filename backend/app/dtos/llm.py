"""Thống kê 1 lời gọi LLM qua cache dev."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CallRecord:
    """1 lời gọi LLM qua cache.

    Attributes:
        model: "chat" | "generate".
        hit: Lấy từ cache.
        input_token: Token vào.
        output_token: Token ra.
        total_token: Tổng token.
        ms: Thời gian (ms).
    """

    model: str
    hit: bool
    input_token: int
    output_token: int
    total_token: int
    ms: int


__all__ = ["CallRecord"]
