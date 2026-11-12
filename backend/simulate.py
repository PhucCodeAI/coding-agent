#!/usr/bin/env python
"""Chạy trọn 1 lượt agent và ghi chi tiết từng bước ra JSON — offline (LLM giả, 0 token) hoặc online (LLM thật).

    uv run python simulate.py                               # offline: kịch bản is_prime có sẵn (test fail 1 lần rồi sửa)
    uv run python simulate.py --scenario my.json            # offline: kịch bản tự viết
    uv run python simulate.py --docker                      # offline nhưng chạy test thật trong Docker
    uv run python simulate.py --dump-scenario my.json       # xuất kịch bản mặc định ra file để sửa
    uv run python simulate.py --online                      # ONLINE: gọi LLM thật (TỐN TOKEN) với prompt của kịch bản, sandbox Docker thật
    uv run python simulate.py --online --prompt "..."       # online với prompt khác
    uv run python simulate.py --online --provider Groq-OSS  # chỉ dùng 1 nhà cung cấp (so sánh giữa các lần không bị lẫn model)
    uv run python simulate.py --online --fake-sandbox       # online nhưng sandbox giả (không cần Docker)

Online: mỗi bước ghi đúng body HTTP gửi đi + phản hồi thô của nhà cung cấp (mọi lần thử, kể cả retry/failover; không ghi header/API key)
và token thật theo `usage`. Dùng để sửa prompt (workflow/prompts/), mô tả công cụ, cấu hình [agent] rồi chạy lại, so summary.json.

Kịch bản (JSON): {"prompt": "...", "steps": [[{"name": "write_file", "args": {...}}, ...], ...],
                  "sandbox": [{"exit_code": 1, "stdout": "..."}, ...]}
    - mỗi phần tử `steps` = các tool call model trả ở 1 bước; hết kịch bản thì gọi `finish`.
    - `sandbox` = kết quả lần lượt của các lệnh không phải pip (pytest/python); hết danh sách thì coi như pass.

Kết quả ở storage/lab/sim/<tên>/:
    summary.json      bảng tổng: mỗi bước gửi bao nhiêu ký tự / ước lượng token, chia theo phần (system, brief, tool result...)
    steps/NN.json     bước NN:
                        stats          ký tự / token ước lượng theo phần, message nào đã bị rút gọn
                        new_messages   phần hội thoại mới so với bước trước
                        llm            http_request (body gửi AI: model, messages đã rút gọn, tools, tool_choice),
                                       http_response (phản hồi thô kiểu OpenAI: tool_calls.arguments là chuỗi JSON, usage, finish_reason),
                                       returned_to_agent (dữ liệu LLMClient trả cho agent sau khi parse)
                        tool_results   tham số + output từng công cụ
                        events         sự kiện SSE của bước
    workspace/        file agent tạo ra
Offline: token là ƯỚC LƯỢNG (ký tự / 3.5) — đủ so sánh giữa các bước / các lần tối ưu. Online: token thật từ nhà cung cấp.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path
from time import perf_counter
from typing import Any

from app.dtos.events import ChatRequest
from app.dtos.workspace import ExecutionResult
from app.integrations import sandbox as sandbox_cmds
from app.services.chat_service import ChatService
from app.services.trace_service import TraceCollector
from app.utils.sse import parse_sse

BACKEND = Path(__file__).resolve().parent
SIM_DIR = BACKEND / "storage" / "lab" / "sim"
CHARS_PER_TOKEN = 3.5

_SOLUTION = '''"""Kiểm tra số nguyên tố."""


def is_prime(n: int) -> bool:
    """True nếu n là số nguyên tố.

    Args:
        n: Số cần kiểm tra.

    Returns:
        n có phải số nguyên tố không.
    """
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i < n:
        if n % i == 0:
            return False
        i += 2
    return True
'''

_TESTS = '''"""Test cho is_prime."""

import pytest

from solution import is_prime


@pytest.mark.parametrize("n", [2, 3, 5, 7, 11, 97, 7919])
def test_primes(n: int) -> None:
    """Số nguyên tố trả True."""
    assert is_prime(n)


@pytest.mark.parametrize("n", [-7, 0, 1, 4, 9, 25, 9797])
def test_composites(n: int) -> None:
    """Hợp số và số < 2 trả False."""
    assert not is_prime(n)
'''

_FAIL_LOG = """....F..F......                                                           [100%]
=================================== FAILURES ===================================
____________________________ test_composites[9] ________________________________
test_solution.py:17: in test_composites
    assert not is_prime(n)
E   assert not True
____________________________ test_composites[25] _______________________________
test_solution.py:17: in test_composites
    assert not is_prime(n)
E   assert not True
=========================== short test summary info ============================
FAILED test_solution.py::test_composites[9] - assert not True
FAILED test_solution.py::test_composites[25] - assert not True
2 failed, 12 passed in 0.04s"""

DEFAULT_SCENARIO: dict[str, Any] = {
    "prompt": "Viết hàm is_prime(n) trong solution.py kèm test pytest đầy đủ trường hợp biên.",
    "steps": [
        [{"name": "list_files", "args": {}}],
        [{"name": "write_file", "args": {"path": "solution.py", "content": _SOLUTION}}],
        [{"name": "write_file", "args": {"path": "test_solution.py", "content": _TESTS}}],
        [{"name": "run_tests", "args": {}}],
        [{"name": "edit_file", "args": {"path": "solution.py", "old_text": "    while i * i < n:", "new_text": "    while i * i <= n:"}}],
        [{"name": "run_tests", "args": {}}],
        [
            {
                "name": "finish",
                "args": {
                    "status": "done",
                    "answer": "Đã viết `is_prime` trong `solution.py` (loại số chẵn, thử ước lẻ tới √n) và 14 test trong `test_solution.py`; tất cả pass.",
                    "commit_message": "feat: is_prime kèm test",
                },
            }
        ],
    ],
    "sandbox": [
        {"exit_code": 1, "stdout": _FAIL_LOG},
        {"exit_code": 0, "stdout": "..............                                                           [100%]\n14 passed in 0.03s"},
    ],
}


def chars(obj: Any) -> int:
    """Số ký tự khi serialize JSON như request thật.

    Args:
        obj: Dữ liệu.

    Returns:
        Số ký tự.
    """
    return len(json.dumps(obj, ensure_ascii=False))


def est_tokens(n_chars: int) -> int:
    """Ước lượng token từ số ký tự.

    Args:
        n_chars: Số ký tự.

    Returns:
        Token ước lượng.
    """
    return round(n_chars / CHARS_PER_TOKEN)


def breakdown(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, int]:
    """Ký tự request chia theo phần: system, brief (user đầu), tool result, lời gọi công cụ của model, nhắc thêm, schema.

    Args:
        messages: Messages gửi model.
        tools: Schema công cụ.

    Returns:
        Phần → số ký tự.
    """
    out = {"system": 0, "brief": 0, "assistant_tool_calls": 0, "tool_results": 0, "other_user": 0, "tool_schemas": chars(tools)}
    first_user = True
    for m in messages:
        if m["role"] == "system":
            out["system"] += chars(m)
        elif m["role"] == "user":
            out["brief" if first_user else "other_user"] += chars(m)
            first_user = False
        elif m["role"] == "assistant":
            out["assistant_tool_calls"] += chars(m)
        else:
            out["tool_results"] += chars(m)
    return out


class MockLLM:
    """LLM giả theo kịch bản; ghi lại nguyên văn request mỗi bước."""

    providers: list[Any] = []

    def __init__(self, steps: list[list[dict[str, Any]]]) -> None:
        """Tạo LLM giả.

        Args:
            steps: Tool call của từng bước.
        """
        self.steps = steps
        self.requests: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, reasoning: str | None = None) -> dict[str, Any]:
        """Trả bước kế tiếp của kịch bản.

        Args:
            messages: Messages gửi model (đã rút gọn).
            tools: Schema công cụ.
            reasoning: Mức suy luận.

        Returns:
            `message` + `info` như LLMClient.chat.
        """
        i = len(self.requests)
        script = self.steps[i] if i < len(self.steps) else [{"name": "finish", "args": {"status": "failed", "answer": "Hết kịch bản mô phỏng."}}]
        calls = [{"id": f"call_{i + 1}_{j}", "name": c["name"], "arguments": c.get("args", {})} for j, c in enumerate(script)]
        tools = tools or []
        # Body HTTP y như LLMClient gửi lên nhà cung cấp kiểu OpenAI (POST /chat/completions).
        http_request = {"model": "mock", "messages": json.loads(json.dumps(messages)), "tools": tools, "tool_choice": "auto"}
        if reasoning:
            http_request["reasoning_effort"] = reasoning
        # Phản hồi thô kiểu OpenAI: `arguments` là CHUỖI JSON (LLMClient tự json.loads).
        raw_calls = [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}} for c in calls
        ]
        in_chars, out_chars = chars(http_request), chars(raw_calls)
        usage = {"prompt_tokens": est_tokens(in_chars), "completion_tokens": est_tokens(out_chars), "total_tokens": est_tokens(in_chars + out_chars)}
        http_response = {
            "id": f"mock-{i + 1}",
            "model": "mock",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": raw_calls}, "finish_reason": "tool_calls"}],
            "usage": usage,
        }
        info = {
            "input_token": usage["prompt_tokens"],
            "output_token": usage["completion_tokens"],
            "total_token": usage["total_tokens"],
            "llm_provider": "Mock",
            "model": "mock",
            "finish_reason": "tool_calls",
        }
        returned = {"message": {"content": "", "tool_calls": calls}, "info": info}
        self.requests.append(
            {
                "messages": http_request["messages"],
                "tools": tools,
                "in_chars": in_chars,
                "out_chars": out_chars,
                "in_tokens": usage["prompt_tokens"],
                "out_tokens": usage["completion_tokens"],
                "http_request": http_request,
                "http_response": http_response,
                "returned": json.loads(json.dumps(returned)),
            }
        )
        return returned

    async def aclose(self) -> None:
        """Không có gì để đóng."""


class OnlineLLM:
    """LLMClient thật, chặn `httpx.post` để ghi nguyên body gửi đi và phản hồi thô của mọi lần thử."""

    def __init__(self, provider: str | None) -> None:
        """Tạo client thật (API key lấy từ .env).

        Args:
            provider: Chỉ dùng nhà cung cấp này (None = tất cả, có failover như production).
        """
        from app.integrations.llm import LLMClient

        self.real = LLMClient()
        if provider:
            self.real.providers = [p for p in self.real.providers if p.name == provider]
            if not self.real.providers:
                raise SystemExit(f"Không có nhà cung cấp '{provider}' (hoặc thiếu API key). Có: {', '.join(p.cfg.name for p in LLMClient().providers)}")
        self.requests: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []  # lời gọi hỏng hẳn (mọi nhà cung cấp lỗi) — không thành bước
        self._attempts: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []  # lần thử của lời gọi hỏng, gắn vào bước thành công kế tiếp
        post = self.real.client.post

        async def recording_post(*args: Any, **kw: Any) -> Any:
            """Gọi thật và ghi lại request/response (không ghi header)."""
            rec: dict[str, Any] = {"url": kw.get("url"), "request": kw.get("json")}
            t0 = perf_counter()
            try:
                resp = await post(*args, **kw)
            except Exception as e:
                rec.update(error=f"{type(e).__name__}: {e}", ms=int((perf_counter() - t0) * 1000))
                self._attempts.append(rec)
                raise
            try:
                body: Any = resp.json()
            except ValueError:
                body = resp.text[:5000]
            rec.update(status=resp.status_code, response=body, ms=int((perf_counter() - t0) * 1000))
            self._attempts.append(rec)
            return resp

        self.real.client.post = recording_post  # type: ignore[method-assign]

    @property
    def providers(self) -> list[Any]:
        """Nhà cung cấp của client thật."""
        return self.real.providers

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, reasoning: str | None = None) -> dict[str, Any]:
        """Gọi LLM thật, ghi lại mọi lần thử.

        Args:
            messages: Messages gửi model (đã rút gọn).
            tools: Schema công cụ.
            reasoning: Mức suy luận.

        Returns:
            Như LLMClient.chat.
        """
        self._attempts = []
        try:
            res = await self.real.chat(messages, tools, reasoning=reasoning)
        except Exception as e:
            self._pending += self._attempts
            self.failed.append({"error": f"{type(e).__name__}: {getattr(e, 'message', e)}", "attempts": self._attempts})
            raise
        attempts, self._pending = self._pending + self._attempts, []
        ok = next((a for a in reversed(attempts) if a.get("status") == 200), attempts[-1] if attempts else {})
        info = res.get("info") or {}
        self.requests.append(
            {
                "messages": json.loads(json.dumps(messages)),
                "tools": tools or [],
                "in_chars": chars(ok.get("request")),
                "out_chars": chars(((ok.get("response") or {}).get("choices") or [{}])[0].get("message")) if isinstance(ok.get("response"), dict) else 0,
                "in_tokens": int(info.get("input_token") or 0),
                "out_tokens": int(info.get("output_token") or 0),
                "http_request": ok.get("request"),
                "http_response": ok.get("response"),
                "returned": json.loads(json.dumps(res, default=str)),
                "attempts": [{k: v for k, v in a.items() if k not in ("request",)} | {"request_chars": chars(a.get("request"))} for a in attempts],
                "provider": info.get("llm_provider"),
                "model": info.get("model"),
            }
        )
        return res

    async def aclose(self) -> None:
        """Đóng HTTP client."""
        await self.real.aclose()


class StubTracer:
    """Trace service giả: giữ collector trong bộ nhớ để đọc span sau khi chạy."""

    def __init__(self) -> None:
        """Tạo tracer."""
        self.collector: TraceCollector | None = None

    def create_collector(self, session_id: str, prompt: str) -> TraceCollector:
        """Tạo collector trong bộ nhớ.

        Args:
            session_id: Mã phiên.
            prompt: Yêu cầu.

        Returns:
            Collector.
        """
        self.collector = TraceCollector(session_id, prompt)
        return self.collector

    async def save(self, *args: Any) -> None:
        """Bỏ qua lưu trace.

        Args:
            *args: Tham số của TraceService.save.
        """


def fake_sandbox(results: list[dict[str, Any]]) -> Any:
    """Sandbox giả: lệnh pip luôn thành công; lệnh khác lấy lần lượt từ `results` (hết thì pass).

    Args:
        results: Kết quả theo thứ tự.

    Returns:
        Hàm thay cho sandbox.run_command.
    """
    queue = list(results)

    async def run_command(workspace_dir: Any, command: list[str], **kw: Any) -> ExecutionResult:
        """Kết quả giả cho 1 lệnh."""
        if "pip" in command:
            return ExecutionResult(exit_code=0, stdout="Successfully installed pytest (giả lập)", duration_ms=1)
        r = queue.pop(0) if queue else {"exit_code": 0, "stdout": "passed (giả lập)"}
        return ExecutionResult(exit_code=r.get("exit_code", 0), stdout=r.get("stdout", ""), stderr=r.get("stderr", ""), duration_ms=1)

    return run_command


async def simulate(name: str, scenario: dict[str, Any], docker: bool, online: bool = False, provider: str | None = None) -> Path:
    """Chạy 1 lượt và ghi kết quả.

    Args:
        name: Tên lần chạy (thư mục kết quả).
        scenario: Kịch bản (online chỉ dùng `prompt`).
        docker: Chạy lệnh thật trong Docker thay vì sandbox giả.
        online: Gọi LLM thật thay vì kịch bản.
        provider: Online: chỉ dùng nhà cung cấp này.

    Returns:
        Thư mục kết quả.
    """
    out = SIM_DIR / name
    shutil.rmtree(out, ignore_errors=True)
    (out / "steps").mkdir(parents=True)
    if not docker:
        sandbox_cmds.run_command = fake_sandbox(scenario.get("sandbox", []))  # type: ignore[assignment]
    llm: Any = OnlineLLM(provider) if online else MockLLM(scenario["steps"])
    tracer = StubTracer()
    service = ChatService(llm, tracer, None, str(out))

    # Sự kiện SSE gom theo bước: mọi sự kiện sau `step` N (tới `step` kế tiếp) thuộc bước N.
    by_step: list[list[dict[str, Any]]] = [[]]
    try:
        async for raw in service.stream(ChatRequest(prompt=scenario["prompt"], session_id="workspace")):
            ev, data = parse_sse(raw)
            if not ev:
                continue
            if ev == "step":
                by_step.append([])
                if online:
                    print(
                        f"  #{data['step']} {data['model']} · {data['input_tokens']:,} vào · {', '.join(c['name'] for c in data['tool_calls']) or '(văn bản)'}",
                        flush=True,
                    )
            by_step[-1].append({"event": ev, "data": data})
    finally:
        await llm.aclose()

    rows = []
    prev_len = 0
    for i, req in enumerate(llm.requests, start=1):
        events = by_step[i] if i < len(by_step) else []
        step_ev = next((e["data"] for e in events if e["event"] == "step"), {})
        calls = {c["id"]: c for c in step_ev.get("tool_calls", [])}
        results = [
            {
                "name": e["data"]["name"],
                "args": calls.get(e["data"]["id"], {}).get("args"),
                "ok": e["data"]["ok"],
                "output": e["data"]["output"],
                "duration_ms": e["data"]["duration_ms"],
            }
            for e in events
            if e["event"] == "tool"
        ]
        parts = breakdown(req["messages"], req["tools"])
        compacted = [j for j, m in enumerate(req["messages"]) if "kết quả cũ đã rút gọn" in (m.get("content") or "")]
        detail = {
            "step": i,
            "stats": {
                "in_chars": req["in_chars"],
                "input_tokens": req["in_tokens"],
                "output_tokens": req["out_tokens"],
                "parts_chars": parts,
                "message_count": len(req["messages"]),
                "compacted_message_indexes": compacted,
            },
            # Phần mới so với bước trước (kết quả công cụ bước trước + lời gọi của model) — đọc nhanh thay vì cả `messages`.
            "new_messages": req["messages"][prev_len:] if i > 1 else req["messages"],
            "llm": {
                **({"provider": req["provider"], "model": req["model"], "attempts": req["attempts"]} if online else {}),
                "http_request": req["http_request"],
                "http_response": req["http_response"],
                "returned_to_agent": req["returned"],
            },
            "tool_results": results,
            "events": events,
        }
        prev_len = len(req["messages"])
        (out / "steps" / f"{i:02d}.json").write_text(json.dumps(detail, ensure_ascii=False, indent=1))
        rows.append(
            {
                "step": i,
                "tools": [f"{r['name']}{'' if r['ok'] else ' ✗'}" for r in results] or [c["name"] for c in step_ev.get("tool_calls", [])],
                "model": req.get("model", "mock"),
                "input_tokens": req["in_tokens"],
                "output_tokens": req["out_tokens"],
                "parts_tokens_est": {k: est_tokens(v) for k, v in parts.items()},
                "messages": len(req["messages"]),
                "compacted": len(compacted),
            }
        )

    done = next((e["data"] for e in by_step[-1] if e["event"] == "done"), {})
    final = next((e["data"] for evs in by_step for e in evs if e["event"] == "final"), None)
    summary = {
        "prompt": scenario["prompt"],
        "mode": "online" if online else "offline",
        "status": done.get("status"),
        "final": final,
        "steps": len(rows),
        "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "duration_ms": done.get("duration_ms"),
        "note": ("token thật từ nhà cung cấp" if online else f"token ước lượng = ký tự / {CHARS_PER_TOKEN}")
        + f"; parts_tokens_est luôn là ước lượng (ký tự / {CHARS_PER_TOKEN}); chi tiết từng bước ở steps/NN.json",
        "per_step": rows,
        "failed_llm_calls": getattr(llm, "failed", []),
        "spans": [s.model_dump(include={"name", "span_type", "status", "duration_ms"}) for s in (tracer.collector.spans if tracer.collector else [])],
        "events_before_first_step": by_step[0],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    print(f"▶ {scenario['prompt']}")
    print(f"{'bước':>4}  {'công cụ':<34}{'vào':>7}{'ra':>6}  system brief schema tool_kq  msg")
    for r in rows:
        p = r["parts_tokens_est"]
        print(
            f"{r['step']:>4}  {', '.join(r['tools'])[:33]:<34}{r['input_tokens']:>7,}{r['output_tokens']:>6,}"
            f"  {p['system']:>6} {p['brief']:>5} {p['tool_schemas']:>6} {p['tool_results']:>7}  {r['messages']:>3}"
        )
    unit = "token" if online else "token (ước lượng)"
    print(f"■ {summary['status']} · {len(rows)} bước · {summary['total_tokens']:,} {unit} → {out.relative_to(BACKEND)}")
    return out


def main() -> None:
    """Điểm vào dòng lệnh."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", type=Path, help="file kịch bản JSON (mặc định: is_prime có sẵn)")
    parser.add_argument("--name", help="tên thư mục kết quả (mặc định: tên file kịch bản hoặc 'is_prime')")
    parser.add_argument("--docker", action="store_true", help="chạy lệnh thật trong Docker sandbox")
    parser.add_argument("--dump-scenario", type=Path, help="ghi kịch bản mặc định ra file rồi thoát")
    parser.add_argument("--online", action="store_true", help="gọi LLM thật (TỐN TOKEN); mặc định sandbox Docker thật")
    parser.add_argument("--prompt", help="ghi đè prompt của kịch bản")
    parser.add_argument("--provider", help="online: chỉ dùng nhà cung cấp này (tên trong [[llm.providers]])")
    parser.add_argument("--fake-sandbox", action="store_true", help="online: dùng sandbox giả thay vì Docker")
    args = parser.parse_args()
    if args.dump_scenario:
        args.dump_scenario.write_text(json.dumps(DEFAULT_SCENARIO, ensure_ascii=False, indent=1))
        print(f"Đã ghi {args.dump_scenario}")
        return
    scenario = json.loads(args.scenario.read_text()) if args.scenario else dict(DEFAULT_SCENARIO)
    if args.prompt:
        scenario["prompt"] = args.prompt
    name = args.name or ("online_" if args.online else "") + (args.scenario.stem if args.scenario else "is_prime")
    docker = args.docker or (args.online and not args.fake_sandbox)
    asyncio.run(simulate(name, scenario, docker, args.online, args.provider))


if __name__ == "__main__":
    main()
