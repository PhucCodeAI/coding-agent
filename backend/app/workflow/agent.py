"""Agent — vòng lặp tool-calling của 1 lượt chat.

    hội thoại = [system, brief]
    lặp: gọi LLM (kèm schema công cụ) → chạy các tool_call → thêm kết quả (message `tool`) → lặp
    dừng khi: gọi `finish` · hết `max_steps` · `max_fail_streak` lệnh công cụ lỗi liên tiếp · LLM không dùng được · quá `max_wall_sec`.

Mọi sự kiện (step, tool, file, sandbox, question...) đẩy vào 1 hàng đợi; `run()` yield chúng ra SSE theo thứ tự phát sinh.
Ngữ cảnh: kết quả công cụ cũ được rút gọn (`prompt.compact`); lời gọi công cụ của chính agent không bị sửa.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import datetime
from time import perf_counter
from typing import Any

from loguru import logger

from app.config import tool_configs
from app.dtos.agent import AgentContext, AgentState, Outcome
from app.dtos.config import AgentConfig
from app.dtos.events import (
    AnswerEventPayload,
    AnswerItemPayload,
    CommitEventPayload,
    FinalEventPayload,
    QuestionEventPayload,
    QuestionItemPayload,
    QuestionOptionPayload,
    StepEventPayload,
    ToolCallPayload,
    ToolEventPayload,
)
from app.dtos.question import Question, QuestionOption
from app.dtos.tool import ToolContext, ToolOutput
from app.errors import AppError, LLMError, ToolError
from app.integrations import git
from app.utils.sse import format_sse
from app.workflow.commands import MAX_FILE_CHARS, command_of, mentions
from app.workflow.prompt import compact, system_prompt
from app.workflow.questions import QuestionBroker
from app.workflow.tools import ToolSpec, tools_for_groups

_TRANSIENT = frozenset({"LLM_RATE_LIMITED", "LLM_PROVIDER_ERROR", "LLM_TIMEOUT", "LLM_NETWORK_ERROR"})
_PATIENCE = (15, 30)
_OUTPUT_ERRORS = frozenset({"LLM_OUTPUT_TRUNCATED", "LLM_RESPONSE_INVALID", "LLM_BAD_REQUEST"})
_NUDGE = "Bạn chưa gọi công cụ nào. Hãy làm việc bằng cách gọi công cụ; xong (hoặc không làm tiếp được) thì gọi `finish`."
_SHRINK = (
    "Lượt vừa rồi lỗi (output quá dài hoặc lệnh gọi công cụ không hợp lệ) nên CHƯA có gì được thực hiện. "
    "Làm nhỏ lại: mỗi lượt tối đa 1–2 file (~150 dòng); file lớn thì ghi khung trước rồi `edit_file`."
)


class Agent:
    """1 lượt làm việc của agent (xem docstring module)."""

    def __init__(self, context: AgentContext, llm: Any, questions: QuestionBroker | None = None, cfg: AgentConfig | None = None) -> None:
        """Tạo agent cho 1 lượt.

        Args:
            context: Dữ liệu của lượt (workspace, trace collector, lịch sử, metrics).
            llm: LLMClient (hoặc bản giả có cùng `chat()`).
            questions: Nơi hỏi người dùng; None = không hỏi được.
            cfg: Cấu hình; None = `[agent]` trong tool_config.toml.
        """
        self.context = context
        self.llm = llm
        self.questions = questions
        self.cfg = cfg or tool_configs.agent
        self.state = AgentState()
        self.outcome: Outcome | None = None
        self.commit_message = ""
        self.committed = False
        self.file_versions: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._events: asyncio.Queue[str | None] = asyncio.Queue()
        self._finished = asyncio.Event()

    @property
    def finished(self) -> bool:
        """Lượt đã có kết quả cuối chưa."""
        return self._finished.is_set()

    def emit(self, sse: str) -> None:
        """Đẩy 1 sự kiện SSE ra client.

        Args:
            sse: Chuỗi SSE đã format.
        """
        self._events.put_nowait(sse)

    def file_lock(self, path: str) -> asyncio.Lock:
        """Khóa theo file (2 lệnh ghi cùng file không chạy chồng nhau).

        Args:
            path: Đường dẫn file trong workspace.

        Returns:
            Lock của file.
        """
        return self._locks[path]

    def tools(self) -> list[ToolSpec]:
        """Công cụ được cấp: nhóm trong `[agent] tools` + `finish` (+ `ask_user` khi được hỏi người dùng).

        Returns:
            Danh sách công cụ.
        """
        return tools_for_groups(set(self.cfg.tools) | {"finish"} | ({"ask_user"} if self.cfg.ask_human else set()))

    def finish(self, status: str, answer: str, commit_message: str = "", code: str | None = None) -> str:
        """Đặt kết quả cuối (chỉ lần đầu có tác dụng).

        Args:
            status: done | failed | blocked.
            answer: Câu trả lời cho người dùng.
            commit_message: Commit message (rỗng = tự sinh).
            code: Mã lỗi khi không thành công.

        Returns:
            Thông báo cho model.
        """
        if self.outcome is None:
            ok = status == "done"
            self.outcome = Outcome(
                status="SUCCESS" if ok else "FAILED",
                text=answer,
                error_code=None if ok else (code or "AGENT_INCOMPLETE"),
                output={"text": answer, "status": status},
            )
            self.commit_message = commit_message
            self._finished.set()
        return "Đã kết thúc lượt."

    async def ask_user(self, question: str, options: list[str], recommended: str) -> str:
        """Hỏi người dùng 1 câu trắc nghiệm và chờ trả lời (hết giờ → lựa chọn khuyến nghị).

        Args:
            question: Câu hỏi.
            options: 2–4 lựa chọn.
            recommended: Lựa chọn khuyến nghị.

        Returns:
            Câu trả lời dạng chữ cho model.
        """
        rec = recommended if recommended in options else options[0]
        q = Question(id="q1", question=question, options=[QuestionOption(label=o, recommended=o == rec) for o in options])
        if not self.cfg.ask_human or self.questions is None:
            return f"(Không hỏi được người dùng — dùng lựa chọn khuyến nghị) {rec}"
        req = self.questions.open(self.context.session_id, [q])
        self.emit(
            format_sse(
                "question",
                QuestionEventPayload(
                    request_id=req.request_id,
                    timeout_sec=self.cfg.human_input_timeout_sec,
                    questions=[
                        QuestionItemPayload(
                            id=q.id,
                            question=q.question,
                            other_placeholder=q.other_placeholder,
                            options=[QuestionOptionPayload(label=o.label, description=o.description, recommended=o.recommended) for o in q.options],
                        )
                    ],
                ),
            )
        )
        answers, timed_out = await self.questions.wait(req, self.cfg.human_input_timeout_sec)
        self.emit(
            format_sse(
                "answer",
                AnswerEventPayload(
                    request_id=req.request_id,
                    timed_out=timed_out,
                    answers=[AnswerItemPayload(question_id=a.question_id, choice=a.choice, text=a.text) for a in answers],
                ),
            )
        )
        text = answers[0].as_text() if answers else rec
        return ("(Hết giờ — dùng lựa chọn khuyến nghị) " if timed_out else "Người dùng trả lời: ") + text

    async def brief(self, files: list[str]) -> str:
        """Tin nhắn đầu tiên gửi model: yêu cầu, chỉ dẫn của `/lệnh`, nội dung file `@` đính kèm, lịch sử, file đang có.

        Args:
            files: File trong workspace.

        Returns:
            Brief dạng Markdown.
        """
        c = self.context
        parts = [f"## Yêu cầu của người dùng [{datetime.now().astimezone():%Y-%m-%d %H:%M}]: {c.prompt}"]
        if command := command_of(c.prompt):
            parts.append(f"## Lệnh /{command.name} — {command.title}\n{command.instruction}")
        for path in mentions(c.prompt, files):
            content = await c.workspace.read_file(path)
            more = f"\n…[đã cắt, còn {len(content) - MAX_FILE_CHARS} ký tự — đọc tiếp bằng read_file]" if len(content) > MAX_FILE_CHARS else ""
            parts.append(f"## File đính kèm: {path}\n```\n{content[:MAX_FILE_CHARS]}\n```{more}")
            self.state.read_versions[path] = 0
        if c.history:
            lines = [f"- {h['role']}: {str(h['content'])[:400]}" for h in c.history[-(self.cfg.history_turns * 2) :]]
            parts.append("## Các lượt trước trong phiên\n" + "\n".join(lines))
        shown = "\n".join(files[:60]) + (f"\n… +{len(files) - 60} file" if len(files) > 60 else "")
        parts.append(f"## Workspace hiện có ({len(files)} file)\n{shown or '(trống)'}")
        return "\n\n".join(parts)

    async def run(self) -> AsyncGenerator[str, None]:
        """Chạy cả lượt; yield SSE theo thứ tự phát sinh.

        Yields:
            Chuỗi SSE.
        """
        files = [f for f in await self.context.workspace.list_files() if f != ".gitignore"]
        worker = asyncio.create_task(self._loop(await self.brief(files)))
        closer = asyncio.create_task(self._close(worker))
        try:
            while (item := await self._events.get()) is not None:
                yield item
        finally:
            closer.cancel()
            await _cancel(worker)
        while not self._events.empty():
            if item := self._events.get_nowait():
                yield item

    async def _loop(self, brief: str) -> None:
        """Vòng lặp gọi LLM → chạy công cụ tới khi `finish` hoặc chạm giới hạn.

        Args:
            brief: Tin nhắn đầu tiên.
        """
        cfg, st = self.cfg, self.state
        specs = {s.name: s for s in self.tools()}
        schemas = [s.schema() for s in specs.values()]
        budget = cfg.request_max_chars - len(json.dumps(schemas, ensure_ascii=False))
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(cfg)}, {"role": "user", "content": brief}]
        sent_upto = nudges = 0
        while not self.finished:
            if st.steps >= cfg.max_steps:
                self.finish("failed", f"Hết {cfg.max_steps} bước mà chưa xong.", code="AGENT_STEP_LIMIT")
                return
            try:
                msg = await self._chat_patiently(compact(messages, cfg.keep_full_results, cfg.old_result_chars, budget), schemas, messages[sent_upto:])
            except LLMError as e:
                if e.code in _OUTPUT_ERRORS and st.output_errors < 2:
                    st.output_errors += 1
                    messages.append({"role": "user", "content": _SHRINK})
                    continue
                self.finish("failed", f"Dừng: LLM không dùng được — [{e.code}] {e.message}", code=e.code)
                return
            st.steps += 1
            st.output_errors = 0
            calls = msg["tool_calls"]
            for c in calls:
                while c["id"] in st.call_ids:
                    c["id"] += "_"
                st.call_ids.add(c["id"])
            messages.append(_assistant_message(msg))
            sent_upto = len(messages)
            if not calls:
                nudges += 1
                if nudges > 2:
                    self.finish("failed", f"Không gọi công cụ / không kết thúc. Nội dung cuối: {msg['content'][:500]}")
                    return
                messages.append({"role": "user", "content": _NUDGE})
                continue
            for call in calls:
                out = await self._call_tool(specs, call)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": _cap(out.text, cfg.tool_result_max_chars)})
                if out.stop:
                    return
                st.fail_streak = 0 if out.ok else st.fail_streak + 1
                st.last_errors = (st.last_errors + [f"{call['name']}: {out.text[:400]}"])[-3:] if not out.ok else []
            if st.fail_streak >= cfg.max_fail_streak:
                errors = "\n---\n".join(st.last_errors)
                self.finish(
                    "failed",
                    f"Dừng tự động: {st.fail_streak} lệnh công cụ lỗi liên tiếp — cần xem nguyên nhân gốc "
                    f"(môi trường/công cụ, không chỉ do code). Lỗi gần nhất:\n{errors}",
                    code="AGENT_REPEATED_FAILURE",
                )
                return

    async def _chat_patiently(self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]], sent: list[dict[str, Any]]) -> dict[str, Any]:
        """Gọi LLM; mọi nhà cung cấp đều lỗi tạm thời qua đủ các vòng thì chờ 15/30s rồi thử lại, lỗi khác raise.

        Args:
            messages: Hội thoại đã rút gọn.
            schemas: Schema công cụ.
            sent: Phần hội thoại mới từ lượt trước (cho trace).

        Returns:
            Tin nhắn của model.
        """
        for wait in (*_PATIENCE, None):
            try:
                return await self._chat(messages, schemas, sent)
            except LLMError as e:
                if wait is None or e.code not in _TRANSIENT:
                    raise
                logger.warning("LLM bận tạm thời [{}] → chờ {}s", e.code, wait)
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    async def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], sent: list[dict[str, Any]]) -> dict[str, Any]:
        """1 lời gọi LLM: span trace, cộng metrics, phát sự kiện `step`.

        Args:
            messages: Hội thoại gửi LLM.
            tools: Schema công cụ.
            sent: Phần hội thoại mới từ lượt trước.

        Returns:
            Tin nhắn của model: `content`, `tool_calls`.
        """
        step = self.state.steps + 1
        name = f"Bước {step}"
        span_in = {"new_messages": _trunc(sent), "total_messages": len(messages)}
        t0 = perf_counter()
        collector = self.context.collector
        try:
            with collector.track(name):
                res = await self.llm.chat(messages, tools, reasoning=self.cfg.reasoning_or_none)
        except Exception as e:
            collector.add_span(
                name=name,
                span_type="llm",
                duration_ms=int((perf_counter() - t0) * 1000),
                input_data=span_in,
                status="ERROR",
                output_data={"error": getattr(e, "message", str(e)), "code": getattr(e, "code", None)},
            )
            raise
        duration_ms = int((perf_counter() - t0) * 1000)
        info, msg = res.get("info") or {}, res["message"]
        collector.add_span(
            name=name,
            span_type="llm",
            duration_ms=duration_ms,
            input_data=span_in,
            output_data=msg,
            metadata={
                k: info.get(v, 0)
                for k, v in (
                    ("input_tokens", "input_token"),
                    ("output_tokens", "output_token"),
                    ("reasoning_tokens", "reasoning_token"),
                    ("cached_tokens", "cached_token"),
                    ("cost", "cost"),
                    ("model", "model"),
                    ("llm_provider", "llm_provider"),
                    ("finish_reason", "finish_reason"),
                )
            },
        )
        self.context.add_usage(info)
        self.state.last_provider = str(info.get("llm_provider") or "")
        self.emit(
            format_sse(
                "step",
                StepEventPayload(
                    step=step,
                    model=str(info.get("model") or ""),
                    provider=str(info.get("llm_provider") or ""),
                    input_tokens=int(info.get("input_token") or 0),
                    output_tokens=int(info.get("output_token") or 0),
                    reasoning_tokens=int(info.get("reasoning_token") or 0),
                    duration_ms=duration_ms,
                    content=msg.get("content") or "",
                    tool_calls=[
                        ToolCallPayload(id=c["id"], name=c["name"], args={k: v for k, v in c["arguments"].items() if k != "__invalid_json__"})
                        for c in msg.get("tool_calls") or []
                    ],
                ),
            )
        )
        return msg

    def demote_provider(self, reason: str) -> None:
        """Hạ ưu tiên nhà cung cấp vừa trả output hỏng (VD tham số công cụ bị cắt cụt) để lời gọi sau dùng nhà khác.

        Args:
            reason: Lý do (ghi log).
        """
        demote = getattr(self.llm, "demote", None)
        if self.state.last_provider and demote:
            demote(self.state.last_provider, reason)

    async def _call_tool(self, specs: dict[str, ToolSpec], call: dict[str, Any]) -> ToolOutput:
        """Chạy 1 lời gọi công cụ: kiểm tên/JSON, gọi handler, ghi span `tool`, phát sự kiện `tool`.

        Args:
            specs: Công cụ theo tên.
            call: Lời gọi của model (`id`, `name`, `arguments`).

        Returns:
            Kết quả (lỗi cũng trả về dạng kết quả để model tự sửa).
        """
        name, args = call["name"], call["arguments"]
        if name not in specs and "." in name and name.rsplit(".", 1)[-1] in specs:
            name = call["name"] = name.rsplit(".", 1)[-1]
        spec = specs.get(name)
        t0 = perf_counter()
        if spec is None:
            out = ToolOutput(f"Công cụ `{name}` không có. Công cụ của bạn: {', '.join(specs)}", ok=False)
        elif "__invalid_json__" in args:
            out = ToolOutput("arguments không phải JSON hợp lệ — gọi lại với JSON đúng (escape chuỗi cẩn thận).", ok=False)
        else:
            try:
                out = await spec.call(ToolContext(self), args)
            except ToolError as e:
                out = ToolOutput(f"LỖI: {e}\nCách dùng: {spec.usage}", ok=False)
            except AppError as e:
                out = ToolOutput(f"LỖI: {e.message}", ok=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception("Công cụ {} lỗi", name)
                out = ToolOutput(f"LỖI hệ thống khi chạy công cụ: {type(e).__name__}: {e}", ok=False)
            if out.ok and out.text.startswith("LỖI:"):
                out.ok = False
        duration_ms = int((perf_counter() - t0) * 1000)
        self.context.collector.add_span(
            name=name,
            span_type="tool",
            duration_ms=duration_ms,
            status="OK" if out.ok else "ERROR",
            input_data={k: v for k, v in args.items() if k != "__invalid_json__"},
            output_data={"text": out.text[:4000]},
        )
        self.emit(
            format_sse(
                "tool", ToolEventPayload(id=call["id"], name=name, ok=out.ok, output=_cap(out.text, self.cfg.tool_result_max_chars), duration_ms=duration_ms)
            )
        )
        return out

    async def _close(self, worker: asyncio.Task[None]) -> None:
        """Chờ vòng lặp xong (hoặc hết giờ), đặt kết quả nếu thiếu, commit, phát `final` rồi đóng hàng đợi.

        Args:
            worker: Task chạy vòng lặp.
        """
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=self.cfg.max_wall_sec)
        except TimeoutError:
            self.finish("failed", f"Quá thời gian tối đa {self.cfg.max_wall_sec}s.", code="AGENT_TIMEOUT")
        except Exception as e:  # noqa: BLE001
            logger.exception("Vòng lặp agent lỗi")
            self.finish("failed", f"Lỗi hệ thống: {type(e).__name__}: {e}", code="SYSTEM_ERROR")
        await _cancel(worker)
        if self.outcome is None:
            self.finish("failed", "Agent dừng mà không gọi `finish`.")
        await self._commit()
        assert self.outcome is not None
        self.emit(
            format_sse(
                "final",
                FinalEventPayload(
                    status="done" if self.outcome.status == "SUCCESS" else "failed",
                    text=self.outcome.text,
                    code=self.outcome.error_code,
                ),
            )
        )
        self._events.put_nowait(None)

    async def _commit(self) -> None:
        """Commit workspace khi lượt thành công, có file được ghi và model chưa tự commit."""
        if not (self.outcome and self.outcome.status == "SUCCESS" and self.cfg.auto_commit and not self.committed and self.file_versions):
            return
        prompt = " ".join(self.context.prompt.split())[: self.cfg.commit_prompt_max_len]
        msg = self.commit_message or self.cfg.commit_message_template.format(prompt=prompt)
        try:
            h, files = await git.commit_changes(workspace_path=self.context.workspace_dir, message=msg)
        except AppError as e:
            logger.error("Commit cuối lượt thất bại: {}", e.message)
            return
        if h:
            self.committed = True
            self.outcome.output["commit_hash"] = h
            self.emit(format_sse("commit", CommitEventPayload(hash=h, message=msg, files=files)))


async def _cancel(task: asyncio.Task[None]) -> None:
    """Hủy task (nếu còn chạy) và chờ nó dừng hẳn."""
    if not task.done() and task is not asyncio.current_task():
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


def _trunc(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bản rút gọn hội thoại để lưu trace (không gửi LLM)."""
    out = []
    for m in messages:
        c = str(m.get("content") or "")
        out.append({**m, "content": c if len(c) <= 4000 else c[:4000] + f"…[+{len(c) - 4000} ký tự]"})
    return out


def _assistant_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Đổi tin nhắn model về dạng OpenAI để thêm vào hội thoại.

    Args:
        msg: Tin nhắn đã parse (`content`, `tool_calls`).

    Returns:
        Message `assistant` theo chuẩn OpenAI.
    """
    out: dict[str, Any] = {"role": "assistant", "content": msg["content"] or ""}
    if msg["tool_calls"]:
        out["tool_calls"] = [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                **({"extra_content": c["extra_content"]} if c.get("extra_content") else {}),
            }
            for c in msg["tool_calls"]
        ]
    return out


def _cap(text: str, limit: int) -> str:
    """Cắt `text` còn tối đa `limit` ký tự, ghi chú số ký tự đã cắt."""
    return text if len(text) <= limit else text[: limit - 40] + f"\n…[đã cắt {len(text) - limit + 40} ký tự]"


__all__ = ["Agent"]
