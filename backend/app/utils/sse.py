"""Đóng gói / đọc chuỗi Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from pydantic import BaseModel


def format_sse(
    event: str,
    payload: BaseModel | dict[str, Any],
    event_id: str | None = None,
) -> str:
    r"""Đóng gói dữ liệu thành chuỗi chuẩn Server-Sent Events (SSE).

    Tuân thủ SSE spec: mỗi dòng của data phải có prefix 'data: '. Nếu payload
    chứa ký tự xuống dòng literal, hàm sẽ tách thành nhiều dòng data tương ứng.

    Args:
        event: Tên sự kiện SSE (status, plan, file, sandbox, token, commit,
            error, done).
        payload: Dữ liệu cần chuyển thành chuỗi JSON (BaseModel hoặc dict).
        event_id: ID tùy chọn của event, dùng cho client resume stream.

    Returns:
        Chuỗi định dạng chuẩn 'event: ...\\nid: ...\\ndata: ...\\n\\n'.
    """
    if isinstance(payload, BaseModel):
        raw_json = payload.model_dump_json()
    else:
        raw_json = json.dumps(payload, ensure_ascii=False)

    lines: list[str] = [f"event: {event}"]
    if event_id:
        lines.append(f"id: {event_id}")

    for line in raw_json.splitlines() or [""]:
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


def parse_sse(chunk: str) -> tuple[str | None, dict | None]:
    """Parse một chuỗi SSE thành (event_name, data_dict).

    Args:
        chunk: Chuỗi SSE đầy đủ (có thể gồm nhiều dòng).

    Returns:
        Tuple (event_name, payload_dict). Trả về (None, None) nếu là ping
        hoặc không parse được.
    """
    if not chunk or chunk.startswith(":"):
        return None, None

    event_name: str | None = None
    data_parts: list[str] = []

    for line in chunk.split("\n"):
        if line.startswith("event: "):
            event_name = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            data_parts.append(line[len("data: ") :])

    if event_name is None or not data_parts:
        return None, None

    raw = "\n".join(data_parts)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, None

    return event_name, payload


def format_sse_ping() -> str:
    r"""Tạo comment ping chuẩn SSE để giữ kết nối qua proxy.

    Returns:
        Chuỗi comment ':\\n\\n' — SSE client sẽ bỏ qua nhưng proxy vẫn thấy byte.
    """
    return ": ping\n\n"


async def with_heartbeat(source: AsyncGenerator[str, None], interval: float = 15.0) -> AsyncGenerator[str, None]:
    """Chèn `: ping` khi `source` im lặng quá `interval` giây (giữ kết nối qua proxy, VD lúc chờ người dùng trả lời).

    Dùng `asyncio.wait` thay vì `wait_for` để không hủy `__anext__()` đang chờ khi hết giờ — hủy nó sẽ lan
    CancelledError vào agent và bị hiểu nhầm là client ngắt kết nối.

    Args:
        source: Generator SSE gốc.
        interval: Số giây tối đa giữa 2 sự kiện.

    Yields:
        Chuỗi SSE (kèm ping).
    """
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            pending = pending or asyncio.create_task(anext(source))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield format_sse_ping()
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield chunk
    finally:
        if pending and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await pending
        with suppress(Exception):
            await source.aclose()


__all__ = ["format_sse", "format_sse_ping", "parse_sse", "with_heartbeat"]
