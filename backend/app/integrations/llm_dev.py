"""LLM cho DEV (không dùng khi chạy thật): cache theo nội dung hội thoại + chế độ demo kịch bản có sẵn.

    CachedLLM : bọc LLMClient. Hội thoại giống hệt (đã chuẩn hóa id/thời gian) → lấy câu trả lời đã ghi, chờ `delay_sec`
                rồi trả (để UI kịp hiển thị), 0 token. Chưa có → gọi LLM thật rồi TỰ GHI vào cache. Hội thoại khác (do bước
                trước đổi kết quả) → tự miss → chỉ các lượt có input khác mới gọi lại LLM.
    DemoLLM   : kịch bản cố định (xem dự án → ghi 2 file → chạy test → kết thúc), 0 token, không cần quota — để thử giao diện.
Bật bằng `[llm] dev_mode = "cache" | "demo"` trong backend/config/tool_config.toml hoặc biến môi trường `LLM_DEV_MODE` (mặc định "off"). Cache dùng chung với `test.py`
(backend/storage/lab/cache). ⚠ Logic này CHỈ cho bản dev — xem docs/ARCHITECTURE.md; khi dùng thật đặt `dev_mode = "off"`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from app.config import BACKEND_DIR
from app.dtos.llm import CallRecord
from app.errors import CacheMiss

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR") or BACKEND_DIR / "storage" / "lab" / "cache")


_NORMALIZERS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}\b"), "<NOW>"),
    (re.compile(r"\d+(?:\.\d+)?\s?(?:ms|s)\b"), "<T>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<HEX>"),
    (re.compile(r"\b(?:tr|ses|msg|sp|hitl)_[0-9a-f]{6,}\b"), "<ID>"),
    (re.compile(r"/(?:private/)?(?:tmp|var/folders)/[\w./-]+"), "<TMP>"),
]
_VOLATILE = re.compile(r"\d\d:\d\d:\d\d \d\d:\d\d:\d\d")


def _norm(text: str) -> str:
    """Chuẩn hóa phần hay đổi giữa các lần chạy (id, thời gian) để khóa cache ổn định."""
    for rx, rep in _NORMALIZERS:
        text = rx.sub(rep, text)
    return text


def _msg_key(m: dict[str, Any]) -> dict[str, Any]:
    """Phần của 1 message dùng làm khóa cache: vai trò, nội dung, lệnh gọi công cụ (bỏ id ngẫu nhiên)."""
    calls = [(c["function"]["name"], c["function"]["arguments"]) for c in m.get("tool_calls") or []]
    return {"role": m["role"], "content": _norm(str(m.get("content") or "")), "calls": calls}


def cache_keys(messages: list[dict[str, Any]], model_name: str, reasoning: str | None, tag: str = "c") -> tuple[str, str]:
    """(khóa chính, khóa lỏng). Khóa chính = nội dung chuẩn hóa; khóa lỏng bỏ thêm phần phụ thuộc timing."""
    norm = [_msg_key(m) for m in messages]
    loose = [{**m, "content": _VOLATILE.sub("<~>", m["content"])} for m in norm]

    def h(payload: Any) -> str:
        """Băm payload thành khóa cache."""
        blob = json.dumps({"t": tag, "m": model_name, "r": reasoning or "", "msgs": payload}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    return h(norm), h(loose)


@dataclass
class CachedLLM:
    """Thay LLMClient: cache theo nội dung hội thoại. Chỉ gọi LLM thật khi miss.

    Args:
        mode: auto (hit dùng cache, miss gọi thật + ghi) | record (luôn gọi thật, ghi đè) | replay (chỉ cache, miss → CacheMiss).
        delay_sec: chờ thêm chừng này giây trước khi trả kết quả từ cache (dev: để UI kịp hiển thị tiến trình).
        real: LLMClient thật (None = tạo khi cần).
    """

    mode: str = "auto"
    delay_sec: float = 0.0
    real: Any = None
    calls: list[CallRecord] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Nạp cấu hình LLM, tạo thư mục cache."""
        from app.config import tool_configs

        self.cfg = tool_configs.llm
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def providers(self) -> list[Any]:
        """Nhà cung cấp của LLM thật (cho log)."""
        return self._client().providers

    def _client(self) -> Any:
        """LLMClient thật (tạo khi cần)."""
        if self.real is None:
            from app.integrations.llm import LLMClient

            self.real = LLMClient()
        return self.real

    def _load(self, key: str, loose: str) -> dict[str, Any] | None:
        """Đọc câu trả lời đã ghi theo khóa chính, rồi khóa lỏng.

        Args:
            key: Khóa chính.
            loose: Khóa lỏng (bỏ phần hay đổi).

        Returns:
            Bản ghi, hoặc None nếu chưa có (hoặc đang ở chế độ record).
        """
        if self.mode == "record":
            return None
        for name in (key, f"loose-{loose}"):
            f = CACHE_DIR / f"{name}.json"
            if f.exists():
                data = json.loads(f.read_text())
                if "ref" in data:
                    f = CACHE_DIR / f"{data['ref']}.json"
                    if not f.exists():
                        continue
                    data = json.loads(f.read_text())
                return data
        return None

    def _save(self, key: str, loose: str, entry: dict[str, Any]) -> None:
        """Ghi câu trả lời theo khóa chính và khóa lỏng.

        Args:
            key: Khóa chính.
            loose: Khóa lỏng.
            entry: Bản ghi.
        """
        (CACHE_DIR / f"{key}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
        (CACHE_DIR / f"loose-{loose}.json").write_text(json.dumps({"ref": key}))

    def _note(self, hit: bool, info: dict[str, Any], ms: int) -> None:
        """Ghi thống kê 1 lời gọi (hit/miss, token, thời gian)."""
        self.calls.append(CallRecord("chat", hit, int(info.get("input_token") or 0), int(info.get("output_token") or 0), int(info.get("total_token") or 0), ms))

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, reasoning: str | None = None) -> dict[str, Any]:
        """Trả lời 1 lượt chat (cache hoặc kịch bản).

        Args:
            messages: Hội thoại.
            tools: Schema công cụ.
            reasoning: Mức suy luận.

        Returns:
            `message` + `info` như LLMClient.chat.
        """
        tool_names = ",".join(t["function"]["name"] for t in tools or [])
        key, loose = cache_keys(messages, f"chat[{tool_names}]", reasoning)
        t0 = time.perf_counter()
        entry = self._load(key, loose)
        if entry is not None:
            if self.delay_sec:
                await asyncio.sleep(self.delay_sec)
            info = {**entry["info"], "cached": True}
            self._note(True, info, 0)
            self._turn(messages, tools, reasoning, entry["response"], info)
            logger.debug("LLM cache HIT {}", key)
            return {"message": entry["response"], "info": info}
        if self.mode == "replay":
            raise CacheMiss(f"cache miss (khóa {key}) — chạy `test.py run` ở chế độ auto để ghi")
        res = await self._client().chat(messages, tools, reasoning)
        self._save(key, loose, {"kind": "chat", "response": res["message"], "info": res["info"], "created": time.time()})
        self._note(False, res["info"], int((time.perf_counter() - t0) * 1000))
        self._turn(messages, tools, reasoning, res["message"], res["info"])
        logger.info("LLM cache MISS {} → đã gọi LLM thật và ghi cache", key)
        return res

    def _turn(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, reasoning: str | None, response: Any, info: dict[str, Any]) -> None:
        """Ghi lại 1 lượt LLM (hội thoại, công cụ, câu trả lời, token) cho `test.py turns`."""
        self.turns.append(
            {
                "i": len(self.turns) + 1,
                "model": info.get("model", "?"),
                "reasoning": reasoning,
                "messages": messages,
                "tools": tools or [],
                "response": response,
                "tokens": int(info.get("total_token") or 0),
                "cached": bool(info.get("cached")),
            }
        )

    async def generate(self, messages: list[dict[str, Any]], reasoning: str | None = None, **kw: Any) -> dict[str, Any]:
        """Sinh văn bản (không cache).

        Args:
            messages: Hội thoại.
            reasoning: Mức suy luận.
            **kw: Tham số thêm cho LLMClient.generate.

        Returns:
            `response` + `info`.
        """
        return await self._client().generate(messages, reasoning, **kw)

    async def aclose(self) -> None:
        """Đóng kết nối HTTP của LLM thật (nếu có)."""
        if self.real is not None:
            await self.real.aclose()


# ------------------------------------------------------------------ chế độ demo
class _DemoProvider:
    """Nhà cung cấp giả của chế độ demo (cho log)."""

    name = "Demo"
    model = "scripted"


class DemoLLM:
    """LLM giả theo kịch bản cố định (không phụ thuộc yêu cầu): xem dự án → ghi 2 file → chạy test → kết thúc."""

    providers: ClassVar[list[Any]] = [_DemoProvider()]

    def __init__(self, delay_sec: float = 1.5) -> None:
        """Tạo LLM demo.

        Args:
            delay_sec: Độ trễ mỗi lượt (để UI kịp hiển thị).
        """
        self.delay_sec = delay_sec

    @staticmethod
    def _call(_tool: str, **args: Any) -> dict[str, Any]:
        """Tạo 1 lời gọi công cụ với id ổn định."""
        return {"id": f"call_{hashlib.md5((_tool + json.dumps(args, sort_keys=True)).encode()).hexdigest()[:8]}", "name": _tool, "arguments": args}

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, reasoning: str | None = None) -> dict[str, Any]:
        """Trả lời 1 lượt chat (cache hoặc kịch bản).

        Args:
            messages: Hội thoại.
            tools: Schema công cụ.
            reasoning: Mức suy luận.

        Returns:
            `message` + `info` như LLMClient.chat.
        """
        await asyncio.sleep(self.delay_sec)
        step = sum(1 for m in messages if m["role"] == "assistant")
        calls = self._script(step)
        info = {
            "input_token": 900 + 120 * step,
            "output_token": 60 + 15 * len(calls),
            "total_token": 960 + 120 * step + 15 * len(calls),
            "llm_provider": "Demo",
            "model": "scripted",
        }
        return {"message": {"content": "", "tool_calls": calls}, "info": info}

    def _script(self, step: int) -> list[dict[str, Any]]:
        """Lượt thứ `step` của kịch bản demo.

        Args:
            step: Số lượt assistant đã có.

        Returns:
            Danh sách tool call.
        """
        c = self._call
        script = [
            [c("list_files")],
            [
                c("write_file", path="calc.py", content='def add(a: int, b: int) -> int:\n    """Cộng hai số."""\n    return a + b\n'),
                c(
                    "write_file",
                    path="test_calc.py",
                    content='from calc import add\n\n\ndef test_add() -> None:\n    """Cộng đúng."""\n    assert add(1, 2) == 3\n',
                ),
            ],
            [c("run_tests")],
            [c("finish", status="done", answer="**Demo xong.** Tạo `calc.py` (hàm `add`) và `test_calc.py`; chạy `run_tests` để kiểm tra.")],
        ]
        return script[min(step, len(script) - 1)]

    async def generate(self, messages: list[dict[str, Any]], reasoning: str | None = None, **kw: Any) -> dict[str, Any]:
        """Sinh văn bản (không cache).

        Args:
            messages: Hội thoại.
            reasoning: Mức suy luận.
            **kw: Tham số thêm cho LLMClient.generate.

        Returns:
            `response` + `info`.
        """
        return {"response": "(demo)", "info": {}}

    async def aclose(self) -> None:
        """Đóng kết nối HTTP của LLM thật (nếu có)."""
        return None


def wrap_dev_llm(client: Any) -> Any:
    """Áp `[llm] dev_mode`: off → giữ nguyên · cache → CachedLLM bọc client · demo → DemoLLM."""
    from app.config import tool_configs

    cfg = tool_configs.llm
    mode = (os.getenv("LLM_DEV_MODE") or cfg.dev_mode).strip().lower()
    if mode not in ("off", "cache", "demo"):
        logger.warning("LLM_DEV_MODE='{}' không hợp lệ (off | cache | demo) → coi như off", mode)
        mode = "off"
    if mode == "cache":
        logger.warning("⚠ DEV: LLM cache BẬT (delay {}s khi hit) — chỉ dùng cho dev, xem docs/ARCHITECTURE.md", cfg.dev_cache_delay_sec)
        return CachedLLM(mode="auto", delay_sec=cfg.dev_cache_delay_sec, real=client)
    if mode == "demo":
        logger.warning("⚠ DEV: chế độ DEMO — LLM giả theo kịch bản, không gọi LLM thật")
        return DemoLLM(cfg.dev_demo_delay_sec)
    return client


__all__ = ["CACHE_DIR", "CacheMiss", "CachedLLM", "CallRecord", "DemoLLM", "cache_keys", "wrap_dev_llm"]
