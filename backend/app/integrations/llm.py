"""Module gateway giao tiếp với API LLM (Data Access Layer).

    - chat: 1 lượt hội thoại có tool-calling chuẩn (dùng bởi nhóm agent).
    - generate: sinh văn bản thường.
Nhiều nhà cung cấp OpenAI-compatible theo thứ tự ưu tiên, gọi theo vòng tròn: nhà nào lỗi thì chuyển ngay sang nhà kế tiếp,
hết vòng quay lại từ đầu (`failover_rounds` vòng); nhà hết hạn mức ngày bị bỏ qua tới lúc reset.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from loguru import logger

from app.config import tool_configs
from app.dtos.config import LLMProviderConfig
from app.errors import LLMError, LLMResponseError

T = TypeVar("T")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_QUOTA_COOLDOWN_SEC = 3600.0

_OUTPUT_ERRORS = frozenset(
    {
        "LLM_RESPONSE_INVALID",
        "LLM_OUTPUT_TRUNCATED",
        "LLM_BAD_REQUEST",
        "LLM_HTTP_ERROR",
    }
)


@dataclass(frozen=True)
class _Provider:
    """Nhà cung cấp đã có API key (cấu hình + secret)."""

    cfg: LLMProviderConfig
    api_key: str

    @property
    def name(self) -> str:
        """Tên nhà cung cấp."""
        return self.cfg.name

    @property
    def url(self) -> str:
        """Endpoint chat completions."""
        return self.cfg.url

    @property
    def model(self) -> str:
        """Tên model."""
        return self.cfg.model


class _UpstreamBodyError(Exception):
    """HTTP 200 nhưng body là lỗi của upstream (OpenRouter: provider quá tải, ...).

    `_retry` coi như lỗi HTTP: retry nếu status tạm thời / không rõ, hết lượt thì
    `_extract_content` dựng LLMError chuẩn từ body.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        """Tạo lỗi từ body có `error` hoặc thiếu `choices`.

        Args:
            data: Body JSON của response.
        """
        super().__init__(str(data.get("error") or "missing choices")[:200])
        self.data = data
        err = data.get("error")
        code = str(err.get("code", "")) if isinstance(err, dict) else ""
        self.status = int(code) if code.isdigit() else None

    @property
    def retryable(self) -> bool:
        """Lỗi tạm thời (thử lại có thể được)."""
        return self.status is None or self.status in _RETRYABLE_STATUS


def _json_body(resp: httpx.Response) -> dict[str, Any]:
    """Parse body JSON.

    OpenRouter đôi khi gửi keep-alive (khoảng trắng) rồi đóng kết nối mà không có JSON → coi là lỗi tạm thời của provider
    (retry được).

    Args:
        resp: Response HTTP.

    Returns:
        Body JSON.
    """
    try:
        data = resp.json()
    except ValueError:
        raw = resp.text
        raise _UpstreamBodyError(
            {"error": {"message": f"Provider đóng kết nối mà không trả kết quả (body {len(raw)} ký tự, không phải JSON: {raw.strip()[:120]!r})"}}
        ) from None
    if not isinstance(data, dict):
        raise _UpstreamBodyError({"error": {"message": f"Body không phải object JSON: {str(data)[:120]}"}})
    return data


def _check_body(data: dict[str, Any]) -> dict[str, Any]:
    """Raise _UpstreamBodyError nếu body không có kết quả hợp lệ."""
    choices = data.get("choices")
    if data.get("error") or not isinstance(choices, list) or not choices:
        raise _UpstreamBodyError(data)
    return data


class LLMClient:
    """Gateway giao tiếp với API LLM (OpenRouter-compatible)."""

    def __init__(self, api_key: str | None = None) -> None:
        """Khởi tạo LLMClient.

        API key của từng nhà cung cấp lấy từ biến môi trường `api_key_env` của nó
        (tool_config.toml → [[llm.providers]]). `api_key` chỉ là key dự phòng cho
        nhà cung cấp nào thiếu biến môi trường (test / chạy tay).

        Raises:
            ValueError: Không nhà cung cấp nào có API key.
        """
        self.cfg = tool_configs.llm
        self.providers: list[_Provider] = []
        for pc in self.cfg.enabled_providers:
            key = (os.getenv(pc.api_key_env) or api_key or "").strip()
            if key:
                self.providers.append(_Provider(pc, key))
            else:
                logger.warning("LLM provider '{}' bị bỏ qua: thiếu biến môi trường {} trong .env", pc.name, pc.api_key_env)
        if not self.providers:
            raise ValueError("Không có nhà cung cấp LLM nào dùng được — thiếu API key: " + ", ".join(p.api_key_env for p in self.cfg.enabled_providers))
        self._down_until: dict[str, float] = {}
        self._quota_out: set[str] = set()
        self._rate_wait: dict[str, float] = {}
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
        )

    async def aclose(self) -> None:
        """Giải phóng tài nguyên HTTP client."""
        await self.client.aclose()

    def _order(self) -> list[_Provider]:
        """Thứ tự thử: nhà cung cấp khỏe theo thứ tự ưu tiên, nhà đang cooldown xuống cuối."""
        now = time.monotonic()
        healthy = [p for p in self.providers if self._down_until.get(p.name, 0.0) <= now]
        cooling = [p for p in self.providers if p not in healthy and p.name not in self._quota_out]
        out = healthy + cooling
        return out or [p for p in self.providers if p not in out]

    def _mark_failed(self, p: _Provider, err: LLMError) -> None:
        """Xếp nhà cung cấp vừa lỗi hạ tầng xuống cuối hàng trong thời gian nghỉ.

        Args:
            p: Nhà cung cấp.
            err: Lỗi vừa gặp (hết quota ngày → nghỉ tới lúc nhà cung cấp báo).
        """
        if err.code == "LLM_QUOTA_EXCEEDED":
            ctx = err.context if isinstance(err.context, dict) else {}
            wait = ctx.get("retry_after_sec")
            m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", str(ctx.get("body", "")))
            if not isinstance(wait, (int, float)) and m:
                wait = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))
            self._down_until[p.name] = time.monotonic() + (float(wait) + 5 if isinstance(wait, (int, float)) else _QUOTA_COOLDOWN_SEC)
            self._quota_out.add(p.name)
            return
        self._quota_out.discard(p.name)
        body = str(err.context.get("body", "")) if isinstance(err.context, dict) else ""
        if err.code == "LLM_BAD_REQUEST" and "tool_use_failed" in body:
            self.demote(p.name, "tool_use_failed")
            return
        if not self.cfg.provider_cooldown_sec or err.code in _OUTPUT_ERRORS:
            return
        cooldown = float(self.cfg.provider_cooldown_sec)
        retry_after = err.context.get("retry_after_sec") if isinstance(err.context, dict) else None
        if err.code == "LLM_RATE_LIMITED" and isinstance(retry_after, (int, float)):
            cooldown = min(cooldown, float(retry_after) + 1.0)
            self._rate_wait[p.name] = time.monotonic() + float(retry_after)
        self._down_until[p.name] = time.monotonic() + cooldown

    def demote(self, provider_name: str, reason: str) -> None:
        """Xếp 1 nhà cung cấp xuống cuối hàng trong thời gian nghỉ vì output của nó hỏng.

        Args:
            provider_name: Tên nhà cung cấp (như trong cấu hình).
            reason: Lý do (ghi log).
        """
        if not self.cfg.provider_cooldown_sec or all(p.name != provider_name for p in self.providers):
            return
        self._down_until[provider_name] = time.monotonic() + float(self.cfg.provider_cooldown_sec)
        logger.warning("Hạ ưu tiên LLM provider {} trong {}s: {}", provider_name, self.cfg.provider_cooldown_sec, reason)

    async def _failover(self, op: Callable[[_Provider], Awaitable[T]]) -> T:
        """Chạy `op` theo vòng tròn qua các nhà cung cấp tới khi 1 nhà thành công.

        Nhà nào lỗi (429, 5xx, timeout, output hỏng...) thì chuyển NGAY sang nhà kế tiếp, không chờ.
        Hết 1 vòng thì quay lại từ đầu, tối đa `failover_rounds` vòng. Vòng sau chỉ chờ khi nhà sắp gọi
        vừa báo 429 kèm thời gian hồi ngắn (≤ `rate_limit_max_wait_sec`), và chỉ chờ phần còn lại.
        Hết vòng → raise lỗi của lần thử cuối (kèm danh sách đã thử trong context).
        """
        tried: list[str] = []
        last: LLMError | None = None
        for rnd in range(self.cfg.failover_rounds):
            used: set[str] = set()
            while cands := [p for p in self._order() if p.name not in used]:
                p = cands[0]
                used.add(p.name)
                if rnd and p.name in self._quota_out and len(cands) > 1:
                    continue
                wait = self._rate_wait.get(p.name, 0.0) - time.monotonic()
                if rnd and 0 < wait <= self.cfg.rate_limit_max_wait_sec:
                    logger.info("Chờ {:.1f}s cho {} hồi giới hạn tốc độ (vòng {})", wait, p.name, rnd + 1)
                    await asyncio.sleep(wait)
                try:
                    result = await op(p)
                except LLMError as e:
                    last = e
                    tried.append(f"{p.name}:{p.model} → {e.code}")
                    self._mark_failed(p, e)
                    nxt = next((x.name for x in self._order() if x.name not in used), "vòng kế tiếp")
                    logger.warning("LLM provider {} ({}) lỗi [{}] → chuyển sang {} | {}", p.name, p.model, e.code, nxt, e.message[:300])
                    continue
                self._down_until.pop(p.name, None)
                self._rate_wait.pop(p.name, None)
                return result
        assert last is not None
        if isinstance(last.context, dict):
            last.context["providers_tried"] = tried
        raise last

    def _headers(self, p: _Provider) -> dict[str, str]:
        """Tạo HTTP headers (Bearer) cho nhà cung cấp.

        Returns:
            Dictionary chứa Authorization và Content-Type.
        """
        return {
            "Authorization": f"Bearer {p.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        p: _Provider,
        messages: list[dict[str, str]],
        reasoning: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Đóng gói payload gửi lên model.

        Args:
            p: Nhà cung cấp.
            messages: Lịch sử hội thoại.
            reasoning: Mức suy luận.
            **kwargs: Tham số tinh chỉnh bổ sung.

        Returns:
            Payload JSON đã chuẩn hóa.
        """
        reserved = {
            "model",
            "messages",
            "stream",
            "stream_options",
            "response_format",
            "temperature",
            "top_p",
            "max_tokens",
            "reasoning",
        }
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in reserved}

        payload: dict[str, Any] = {
            "model": p.model,
            "messages": _for_provider(p, messages),
        }

        effort = reasoning or self._default_reasoning(p)
        if effort and p.cfg.reasoning_style == "openrouter":
            payload["reasoning"] = {"effort": effort}
        elif effort and p.cfg.reasoning_style == "reasoning_effort":
            payload["reasoning_effort"] = effort

        payload.update(safe_kwargs)
        return payload

    def _default_reasoning(self, p: _Provider) -> str | None:
        """Reasoning mặc định theo tên model ([llm.reasoning_by_model] trong tool_config.toml).

        Returns:
            Effort hoặc None.
        """
        model = p.model.lower()
        for marker, effort in self.cfg.reasoning_by_model.items():
            if marker in model and effort:
                return effort
        return None

    # --------------------------------------------------------
    # Retry
    # --------------------------------------------------------
    async def _retry(
        self,
        p: _Provider,
        func: Callable[[], Awaitable[T]],
        max_attempts: int | None = None,
    ) -> T:
        """Thực thi hàm async với retry lũy thừa.

        Args:
            p: Nhà cung cấp (để log / chọn chiến lược chờ).
            func: Closure async không tham số.
            max_attempts: Số lần thử tối đa. Mặc định lấy từ config.

        Returns:
            Kết quả của func khi thành công.

        Raises:
            LLMError: Lỗi HTTP không thể phục hồi, hoặc lỗi network/HTTP
                sau khi hết lượt retry (đã bọc kèm status + body).
        """
        attempts = max_attempts if max_attempts is not None else (1 if len(self.providers) > 1 else self.cfg.attempts_per_provider)
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await func()
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in _RETRYABLE_STATUS:
                    raise _wrap_http_error(e) from e
                quota = _daily_quota_error(e.response, p.model)
                if quota is not None:
                    raise quota from e
                if e.response.status_code == 429 and len(self.providers) > 1:
                    raise _wrap_http_error(e) from e
                last_exc = e
                wait = min(self._retry_after(e.response) or float(2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "LLM[{}] HTTP {} (attempt {}/{}), retry sau {:.1f}s",
                    p.name,
                    e.response.status_code,
                    attempt,
                    attempts,
                    wait,
                )
            except _UpstreamBodyError as e:
                if not e.retryable:
                    _extract_content(e.data)
                last_exc = e
                wait = float(2 ** (attempt - 1))
                logger.warning(
                    "LLM[{}] upstream lỗi trong body (attempt {}/{}): {} — retry sau {:.1f}s",
                    p.name,
                    attempt,
                    attempts,
                    e,
                    wait,
                )
            except httpx.RequestError as e:
                last_exc = e
                wait = float(2 ** (attempt - 1))
                logger.warning(
                    "LLM[{}] network error (attempt {}/{}): {} — retry sau {:.1f}s",
                    p.name,
                    attempt,
                    attempts,
                    type(e).__name__,
                    wait,
                )

            if attempt < attempts:
                await asyncio.sleep(wait)

        assert last_exc is not None
        if isinstance(last_exc, _UpstreamBodyError):
            _extract_content(last_exc.data)
        if isinstance(last_exc, httpx.HTTPStatusError):
            raise _wrap_http_error(last_exc) from last_exc
        raise LLMError(
            f"Không kết nối được LLM sau {attempts} lần thử: {type(last_exc).__name__}",
            code=("LLM_TIMEOUT" if isinstance(last_exc, httpx.TimeoutException) else "LLM_NETWORK_ERROR"),
            context={"model": p.model, "provider": p.name, "error": str(last_exc)[:300]},
        ) from last_exc

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        """Số giây cần chờ: header Retry-After, không có thì đọc "try again in 1m2.5s" trong body (Groq).

        Args:
            response: Phản hồi HTTP.

        Returns:
            Số giây cần chờ, hoặc None nếu không xác định được.
        """
        value = response.headers.get("Retry-After")
        if value is not None:
            try:
                return float(value)
            except ValueError:
                pass
        try:
            body = response.text
        except Exception:
            return None
        if ms := re.search(r"try again in ([\d.]+)ms", body):
            return float(ms.group(1)) / 1000
        m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", body)
        if not m or not any(m.groups()):
            return None
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------
    def _parse_info(self, p: _Provider, data: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
        """Chuẩn hóa metadata từ response OpenRouter.

        Args:
            p: Nhà cung cấp đã trả lời.
            data: JSON gốc từ API.
            elapsed_ms: Latency đo được (ms).

        Returns:
            Dict metadata đã normalize.
        """
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None

        return {
            "request_id": data.get("id"),
            "model": data.get("model", p.model),
            "provider": data.get("provider", "unknown"),
            "llm_provider": p.name,
            "input_token": int(usage.get("prompt_tokens") or 0),
            "output_token": int(usage.get("completion_tokens") or 0),
            "total_token": int(usage.get("total_tokens") or 0),
            "reasoning_token": int(completion_details.get("reasoning_tokens") or 0),
            "cached_token": int(prompt_details.get("cached_tokens") or 0),
            "cost": float(usage.get("cost") or 0.0),
            "time": elapsed_ms,
            "finish_reason": finish_reason,
        }

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------
    async def generate(
        self,
        messages: list[dict[str, str]],
        reasoning: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Gọi LLM ở chế độ non-stream (tự chuyển nhà cung cấp khi lỗi).

        Args:
            messages: Lịch sử hội thoại.
            reasoning: Mức suy luận (None = theo model).
            **kwargs: Cấu hình bổ sung cho model.

        Returns:
            Dict gồm 'response' (str) và 'info' (dict).
        """
        return await self._failover(lambda p: self._generate_one(p, messages, reasoning, **kwargs))

    async def _generate_one(
        self,
        p: _Provider,
        messages: list[dict[str, str]],
        reasoning: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """1 lời gọi sinh văn bản tới 1 nhà cung cấp (có retry).

        Args:
            p: Nhà cung cấp.
            messages: Hội thoại.
            reasoning: Mức suy luận.
            **kwargs: Tham số thêm cho payload.

        Returns:
            `response` + `info`.
        """
        payload = self._payload(p, messages, reasoning, **kwargs)
        start = time.perf_counter()

        async def _post() -> dict[str, Any]:
            """Gửi request và trả body JSON đã kiểm."""
            resp = await self.client.post(
                url=p.url,
                headers=self._headers(p),
                json=payload,
            )
            resp.raise_for_status()
            return _check_body(_json_body(resp))

        data = await self._retry(p, _post)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        content = _extract_content(data)

        return {
            "response": content,
            "info": self._parse_info(p, data, elapsed_ms),
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
    ) -> dict[str, Any]:
        """1 lượt hội thoại có tool-calling chuẩn (OpenAI `tools` / `tool_calls`), tự chuyển nhà cung cấp khi lỗi.

        Args:
            messages: Hội thoại (có thể gồm message `assistant` kèm `tool_calls` và message `tool`).
            tools: Danh sách tool schema kiểu OpenAI. None = không cho gọi công cụ.
            reasoning: Mức reasoning (None = theo `reasoning_by_model`).

        Returns:
            {"message": {"content": str, "tool_calls": [{id, name, arguments(dict)}]}, "info": dict}.

        Raises:
            LLMError: Mọi nhà cung cấp đều lỗi; `LLM_OUTPUT_TRUNCATED` khi output vượt max_tokens.
        """
        return await self._failover(lambda p: self._chat_one(p, messages, tools, reasoning))

    async def _chat_one(
        self,
        p: _Provider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        reasoning: str | None,
    ) -> dict[str, Any]:
        """1 lời gọi chat (tool-calling) tới 1 nhà cung cấp (có retry).

        Args:
            p: Nhà cung cấp.
            messages: Hội thoại.
            tools: Schema công cụ.
            reasoning: Mức suy luận.

        Returns:
            `message` (content + tool_calls đã parse) + `info`.
        """
        extra: dict[str, Any] = {"tools": tools, "tool_choice": "auto"} if tools else {}
        payload = self._payload(p, messages, reasoning, **extra)
        start = time.perf_counter()

        async def _post() -> dict[str, Any]:
            """Gửi request và trả body JSON đã kiểm."""
            resp = await self.client.post(url=p.url, headers=self._headers(p), json=payload)
            resp.raise_for_status()
            return _check_body(_json_body(resp))

        data = await self._retry(p, _post)
        _extract_content(data)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMResponseError(
                "Response bị cắt do vượt max_tokens (finish_reason=length).",
                code="LLM_OUTPUT_TRUNCATED",
                context={"model": p.model, "provider": p.name},
            )
        msg = choice.get("message") or {}
        calls: list[dict[str, Any]] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {"__invalid_json__": raw[:2000]}
            call = {"id": tc.get("id") or f"call_{i}", "name": fn.get("name") or "", "arguments": args}
            if tc.get("extra_content"):
                call["extra_content"] = tc["extra_content"]
            calls.append(call)
        info = self._parse_info(p, data, int((time.perf_counter() - start) * 1000))
        return {"message": {"content": msg.get("content") or "", "tool_calls": calls}, "info": info}

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------


# ============================================================
# Module-level helpers
# ============================================================
_SKIP_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def _for_provider(p: _Provider, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gemini bắt buộc `extra_content.google.thought_signature` trên mọi tool_call đã có (thiếu → 400); nhà cung cấp khác thì bỏ trường này.

    Tool_call do nhà cung cấp khác sinh ra (khi failover) không có chữ ký thật → dùng giá trị bỏ qua kiểm tra mà Google cho phép.
    """
    google = "googleapis" in p.url
    out: list[dict[str, Any]] = []
    for m in messages:
        calls = m.get("tool_calls")
        if not calls:
            out.append(m)
            continue
        fixed = []
        for c in calls:
            c = {k: v for k, v in c.items() if k != "extra_content"} | ({"extra_content": c.get("extra_content") or _SKIP_SIGNATURE} if google else {})
            fixed.append(c)
        out.append({**m, "tool_calls": fixed})
    return out


def _extract_content(data: dict[str, Any]) -> str:
    """Trích xuất content từ response OpenRouter, defensive.

    OpenRouter có thể trả HTTP 200 với body error (không có 'choices')
    khi upstream provider fail. Hàm này raise LLMResponseError rõ ràng
    thay vì để KeyError lan ra.

    Args:
        data: JSON response từ API.

    Returns:
        Content string (có thể rỗng).

    Raises:
        LLMResponseError: Khi response thiếu 'choices' hoặc structure sai.
    """
    if data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        err_status = str(err.get("code", "")) if isinstance(err, dict) else ""
        raise LLMResponseError(
            f"OpenRouter trả lỗi: {msg}",
            code=_code_for_status(int(err_status)) if err_status.isdigit() else "LLM_PROVIDER_ERROR",
            context={"error": err, "raw_keys": list(data.keys())},
        )

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMResponseError(
            "Response thiếu 'choices' — có thể upstream provider fail",
            context={"raw_keys": list(data.keys()), "preview": str(data)[:300]},
        )

    message = choices[0].get("message") or {}
    content = message.get("content")
    return content or ""


_STATUS_TO_CODE: dict[int, str] = {
    400: "LLM_BAD_REQUEST",
    413: "LLM_BAD_REQUEST",
    401: "LLM_AUTH_FAILED",
    402: "LLM_QUOTA_EXCEEDED",
    403: "LLM_AUTH_FAILED",
    404: "LLM_MODEL_NOT_FOUND",
    422: "LLM_BAD_REQUEST",
    429: "LLM_RATE_LIMITED",
}


def _code_for_status(status_code: int) -> str:
    """Map HTTP status của LLM provider sang mã lỗi catalog.

    Args:
        status_code: HTTP status.

    Returns:
        Mã lỗi (LLM_*).
    """
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "LLM_PROVIDER_ERROR"
    return "LLM_HTTP_ERROR"


def _daily_quota_error(response: httpx.Response, model: str) -> LLMError | None:
    """429 do hết hạn mức NGÀY (OpenRouter `free-models-per-day`, Gemini `...PerDay...`) → LLM_QUOTA_EXCEEDED, không retry."""
    try:
        body = response.text
    except Exception:  # noqa: BLE001
        return None
    lowered = body.lower()
    if "per-day" not in lowered and "per day" not in lowered and "perday" not in lowered:
        return None
    reset = ""
    import re as _re

    m = _re.search(r'"X-RateLimit-Reset"\s*:\s*"?(\d{10,13})', body)
    if m:
        ts = int(m.group(1))
        ts = ts / 1000 if ts > 10**12 else ts
        reset = time.strftime(" Reset lúc %H:%M (giờ máy chủ).", time.localtime(ts))
    return LLMError(
        f"Đã dùng hết hạn mức request trong ngày cho model {model}.{reset} "
        "Nạp credit (OpenRouter: ≥ 10 credits mở 1000 request model free/ngày) hoặc đổi model / nhà cung cấp.",
        code="LLM_QUOTA_EXCEEDED",
        context={"status": 429, "body": body[:300]},
    )


def _wrap_http_error(e: httpx.HTTPStatusError) -> LLMError:
    """Bọc HTTPStatusError thành LLMError kèm status + body để debug.

    Args:
        e: Lỗi HTTP gốc từ httpx.

    Returns:
        LLMError với code theo status (LLM_AUTH_FAILED, LLM_RATE_LIMITED, ...).
    """
    status_code = e.response.status_code
    try:
        body = e.response.text[:500]
    except Exception:
        body = ""
    context: dict[str, object] = {"status": status_code, "body": body}
    retry_after = LLMClient._retry_after(e.response)
    if retry_after is not None:
        context["retry_after_sec"] = retry_after
    return LLMError(
        f"LLM API trả HTTP {status_code}: {body[:200]}",
        code=_code_for_status(status_code),
        context=context,
    )


__all__ = ["LLMClient"]
