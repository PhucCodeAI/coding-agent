#!/usr/bin/env python
"""Chạy agent theo case gần như KHÔNG tốn token: ghi / phát lại LLM theo nội dung hội thoại.

    uv run python test.py cases                     # case có sẵn + đã có bản ghi chưa
    uv run python test.py run easy                  # auto: có cache dùng cache, miss gọi LLM thật rồi ghi
    uv run python test.py run easy --mode replay    # chỉ cache (miss → báo lỗi, 0 token)
    uv run python test.py run easy --mode record    # luôn gọi LLM thật, ghi đè cache
    uv run python test.py run easy --fake-sandbox   # coi test luôn pass (không cần Docker) — chỉ kiểm luồng
    uv run python test.py run mycase --prompt "..." # case mới
    uv run python test.py turns easy                # các lượt LLM của lần chạy gần nhất (model · công cụ · token)
    uv run python test.py clean [case]              # xóa workspace + bản ghi lần chạy (cache LLM giữ nguyên)

Mỗi lần `run` ghi toàn bộ context (hội thoại từng lượt, sự kiện SSE, tổng token) ra storage/lab/cases/<case>/run.json.
Cache LLM dùng chung với UI khi `[llm] dev_mode = "cache"` (storage/lab/cache).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from app.dtos.events import ChatRequest
from app.dtos.workspace import ExecutionResult
from app.errors import CacheMiss
from app.integrations import sandbox as sandbox_cmds
from app.integrations.llm_dev import CachedLLM
from app.services.chat_service import ChatService
from app.services.trace_service import TraceCollector
from app.utils.sse import parse_sse

BACKEND = Path(__file__).resolve().parent
CASES_DIR = BACKEND / "storage" / "lab" / "cases"
CASES = {
    "general": "Xin chào, bạn làm được gì?",
    "add": "Viết hàm add(a, b) trả về tổng hai số trong file calc.py, kèm test pytest.",
    "easy": "Viết hàm is_prime(n) tối ưu cho số lớn, kèm unit test đầy đủ các trường hợp biên.",
    "hard": "Xây dựng module quản lý thư viện sách: model Book và Member, repository lưu JSON file, service mượn/trả sách (mỗi member mượn tối đa 3 cuốn, tính phí trễ hạn)",
}


class StubTracer:
    """Trace service giả: thu span trong bộ nhớ, không lưu Supabase."""

    def create_collector(self, session_id: str, prompt: str) -> TraceCollector:
        """Tạo collector trong bộ nhớ.

        Args:
            session_id: Mã phiên.
            prompt: Yêu cầu.

        Returns:
            Collector.
        """
        return TraceCollector(session_id, prompt)

    async def save(self, *args: Any) -> None:
        """Bỏ qua lưu trace.

        Args:
            *args: Tham số của TraceService.save.
        """


async def fake_run_command(workspace_dir: Any, command: list[str], **kw: Any) -> ExecutionResult:
    """Sandbox giả: mọi lệnh thành công.

    Args:
        workspace_dir: Thư mục workspace.
        command: Lệnh.
        **kw: Tham số khác của sandbox.run_command.

    Returns:
        Kết quả thành công.
    """
    return ExecutionResult(exit_code=0, stdout="1 passed (fake sandbox)", duration_ms=1)


def prompt_of(case: str, prompt: str | None) -> str:
    """Prompt của case: tham số `--prompt` > case có sẵn > case đã chạy trước đó.

    Args:
        case: Tên case.
        prompt: Prompt truyền qua dòng lệnh.

    Returns:
        Prompt.
    """
    saved = CASES_DIR / case / "run.json"
    if prompt or case in CASES:
        return prompt or CASES[case]
    if saved.exists():
        return json.loads(saved.read_text())["prompt"]
    sys.exit(f"Không có case '{case}'. Dùng --prompt để tạo case mới.")


async def run(case: str, prompt: str, mode: str, fake_sandbox: bool) -> None:
    """Chạy 1 case từ đầu (workspace trống), in tiến trình, ghi run.json.

    Args:
        case: Tên case.
        prompt: Yêu cầu.
        mode: auto | replay | record.
        fake_sandbox: Thay sandbox bằng bản giả.
    """
    case_dir = CASES_DIR / case
    shutil.rmtree(case_dir / "workspace", ignore_errors=True)
    if fake_sandbox:
        sandbox_cmds.run_command = fake_run_command  # type: ignore[assignment]
    llm = CachedLLM(mode=mode)
    runner = ChatService(llm, StubTracer(), None, str(case_dir))
    events: list[dict[str, Any]] = []
    print(f"▶ {case} [{mode}]: {prompt}")
    try:
        async for raw in runner.stream(ChatRequest(prompt=prompt, session_id="workspace")):
            name, data = parse_sse(raw)
            if not name:
                continue
            events.append({"event": name, "data": data})
            if name == "step":
                tools = ", ".join(c["name"] for c in data["tool_calls"]) or "(văn bản)"
                print(f"  #{data['step']} {data['model']} · {data['input_tokens']:,} vào · {data['output_tokens']:,} ra · {tools}")
            elif name == "tool" and not data["ok"]:
                print(f"     ✗ {data['name']}: {data['output'][:120]}")
            elif name in ("final", "error"):
                print(f"  {name}: {json.dumps(data, ensure_ascii=False)[:300]}")
            elif name == "done":
                print(f"■ {data['status']} · {data['total_tokens']:,} token · {data['duration_ms'] / 1000:.1f}s")
    except CacheMiss as e:
        print(f"✗ {e}")
    finally:
        await llm.aclose()
    hits = sum(1 for c in llm.calls if c.hit)
    print(f"  cache: {hits} hit / {len(llm.calls) - hits} miss")
    (case_dir / "run.json").write_text(
        json.dumps({"case": case, "mode": mode, "prompt": prompt, "turns": llm.turns, "events": events}, ensure_ascii=False, indent=1)
    )


def turns(case: str) -> None:
    """In các lượt LLM của lần chạy gần nhất.

    Args:
        case: Tên case.
    """
    f = CASES_DIR / case / "run.json"
    if not f.exists():
        sys.exit(f"Case '{case}' chưa chạy.")
    for t in json.loads(f.read_text())["turns"]:
        tools = ", ".join(c["name"] for c in t["response"].get("tool_calls") or []) or "(văn bản)"
        print(f"{t['i']:>3}  {t['model']:<28}{tools:<40}{t['tokens']:>7,} tok {'(cache)' if t['cached'] else ''}")


def main() -> None:
    """Điểm vào dòng lệnh."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cases")
    r = sub.add_parser("run")
    r.add_argument("case")
    r.add_argument("--prompt")
    r.add_argument("--mode", choices=["auto", "replay", "record"], default="auto")
    r.add_argument("--fake-sandbox", action="store_true")
    sub.add_parser("turns").add_argument("case")
    sub.add_parser("clean").add_argument("case", nargs="?")
    args = parser.parse_args()
    if args.cmd == "cases":
        for name, prompt in CASES.items():
            print(f"{name:<9}{'(đã chạy) ' if (CASES_DIR / name / 'run.json').exists() else ''}{prompt}")
    elif args.cmd == "run":
        asyncio.run(run(args.case, prompt_of(args.case, args.prompt), args.mode, args.fake_sandbox))
    elif args.cmd == "turns":
        turns(args.case)
    elif args.cmd == "clean":
        target = CASES_DIR / args.case if args.case else CASES_DIR
        shutil.rmtree(target, ignore_errors=True)
        print(f"Đã xóa {target}")


if __name__ == "__main__":
    main()
