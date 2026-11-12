"""Chuyển nhà cung cấp LLM theo vòng tròn: lỗi là chuyển ngay, không chờ cùng nhà."""

import asyncio
import json
import time

import httpx

from app.integrations.llm import LLMClient

OK = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def client(script: dict[str, list]) -> tuple[LLMClient, list[str]]:
    """LLMClient với 3 nhà cung cấp giả; `script[model]` = phản hồi lần lượt (số = HTTP status, "slow" = 200 sau 0.5s, dict = 200).

    Args:
        script: Kịch bản phản hồi theo model.

    Returns:
        (client, danh sách model theo thứ tự được gọi).
    """
    llm = LLMClient(api_key="k")
    llm.providers = llm.providers[:3]
    llm.cfg = llm.cfg.model_copy(update={"failover_rounds": 2, "provider_cooldown_sec": 300, "rate_limit_max_wait_sec": 10})
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """Trả phản hồi theo kịch bản của model trong body."""
        model = json.loads(request.content)["model"]
        calls.append(model)
        nxt = script[model].pop(0)
        if nxt == "slow":
            await asyncio.sleep(0.5)
        if isinstance(nxt, int):
            return httpx.Response(nxt, text="Rate limit reached. Please try again in 0.2s.")
        return httpx.Response(200, json=OK)

    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm, calls


async def test_rate_limit_switches_immediately_without_waiting():
    """Nhà đầu báo 429 → gọi ngay nhà kế tiếp (không chờ Retry-After cùng nhà)."""
    llm, calls = client({})
    a, b = (p.model for p in llm.providers[:2])
    llm, calls = client({a: [429], b: [OK]})
    start = time.monotonic()
    res = await llm.chat([{"role": "user", "content": "hi"}])
    assert res["message"]["content"] == "ok"
    assert calls == [a, b]
    assert time.monotonic() - start < 0.15


async def test_slow_provider_is_waited_for_not_switched():
    """Nhà trả lời chậm (vẫn 200) → chờ tới khi xong, không chuyển nhà."""
    llm, _ = client({})
    a = llm.providers[0].model
    llm, calls = client({a: ["slow"]})
    res = await llm.chat([{"role": "user", "content": "hi"}])
    assert res["message"]["content"] == "ok"
    assert calls == [a]


async def test_cycles_back_to_first_provider_in_next_round():
    """Cả vòng đều lỗi → quay lại vòng 2; nhà vừa 429 với thời gian hồi ngắn được chờ nốt rồi gọi lại."""
    llm, _ = client({})
    a, b, c = (p.model for p in llm.providers)
    llm, calls = client({a: [429, OK], b: [503], c: [500]})
    res = await llm.chat([{"role": "user", "content": "hi"}])
    assert res["message"]["content"] == "ok"
    assert calls == [a, b, c, a]
