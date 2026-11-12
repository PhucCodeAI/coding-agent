"""HTTP client Supabase (PostgREST + Storage): xác thực bằng service key, thử lại khi 429/5xx/lỗi mạng."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class SupabaseClient:
    """Gửi request tới Supabase; repository dựng đường dẫn và tham số."""

    def __init__(self, url: str, key: str, http: httpx.AsyncClient | None = None) -> None:
        """Tạo client.

        Args:
            url: SUPABASE_URL (https://<project>.supabase.co).
            key: SUPABASE_KEY.
            http: HTTP client dùng chung (None = tự tạo).
        """
        self.base_url = url.rstrip("/")
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        attempts: int = 3,
    ) -> httpx.Response:
        """Gửi request, thử lại lỗi tạm thời (theo `Retry-After` hoặc lũy thừa 2).

        Args:
            method: HTTP method.
            path: Đường dẫn sau base URL, VD `/rest/v1/sessions`.
            params: Query string.
            json: Body JSON.
            content: Body thô.
            headers: Header thêm.
            attempts: Số lần thử tối đa.

        Returns:
            Response thành công.

        Raises:
            httpx.HTTPStatusError: Lỗi HTTP không phục hồi được (hoặc hết lượt thử).
            httpx.RequestError: Lỗi mạng sau khi hết lượt thử.
        """
        url = f"{self.base_url}{path}"
        for attempt in range(1, attempts + 1):
            try:
                response = await self.http.request(method, url, params=params, json=json, content=content, headers={**self._headers, **(headers or {})})
            except httpx.RequestError as e:
                if attempt == attempts:
                    raise
                logger.warning("Supabase lỗi mạng (lần {}/{}): {}", attempt, attempts, type(e).__name__)
                await asyncio.sleep(2 ** (attempt - 1))
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                wait = float(response.headers.get("Retry-After") or 2 ** (attempt - 1))
                logger.warning("Supabase HTTP {} (lần {}/{}), thử lại sau {:.1f}s", response.status_code, attempt, attempts, wait)
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return response
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        """Đóng HTTP client."""
        await self.http.aclose()


__all__ = ["SupabaseClient"]
