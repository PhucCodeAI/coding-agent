"""CRUD trên bảng Postgres qua PostgREST (Supabase). Không biết gì về nghiệp vụ: service chọn bảng, cột, điều kiện."""

from __future__ import annotations

from typing import Any

import httpx

from app.errors import DatabaseError
from app.integrations.supabase import SupabaseClient


def _where(filters: dict[str, Any] | None) -> dict[str, str]:
    """Điều kiện bằng (`cột = giá trị`) theo cú pháp PostgREST.

    Args:
        filters: Cột → giá trị.

    Returns:
        Query string PostgREST.
    """
    return {k: f"eq.{v}" for k, v in (filters or {}).items()}


class Database:
    """Đọc / ghi bảng qua PostgREST."""

    def __init__(self, client: SupabaseClient) -> None:
        """Tạo repository.

        Args:
            client: Supabase client dùng chung.
        """
        self.client = client

    async def _call(self, method: str, table: str, *, params: dict[str, Any], prefer: str | None = None, json: Any = None) -> httpx.Response:
        """Gửi 1 request tới bảng, đổi lỗi HTTP/mạng thành DatabaseError.

        Args:
            method: HTTP method.
            table: Tên bảng.
            params: Query string.
            prefer: Header `Prefer` của PostgREST.
            json: Body.

        Returns:
            Response.

        Raises:
            DatabaseError: Request thất bại.
        """
        try:
            return await self.client.request(method, f"/rest/v1/{table}", params=params, json=json, headers={"Prefer": prefer} if prefer else None)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            body = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else type(e).__name__
            raise DatabaseError(f"{method} {table} thất bại", context={"table": table, "error": body}) from e

    async def select(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        *,
        columns: str = "*",
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """Đọc các dòng thỏa điều kiện.

        Args:
            table: Tên bảng.
            filters: Cột → giá trị (so sánh bằng).
            columns: Cột cần lấy (cú pháp `select` của PostgREST).
            order: Sắp xếp, VD `updated_at.desc`.
            limit: Số dòng tối đa.
            offset: Bỏ qua bao nhiêu dòng đầu.

        Returns:
            Danh sách dòng.
        """
        params: dict[str, Any] = {"select": columns, **_where(filters)}
        params.update({k: v for k, v in (("order", order), ("limit", limit), ("offset", offset)) if v is not None})
        return (await self._call("GET", table, params=params)).json()

    async def get(self, table: str, filters: dict[str, Any], *, columns: str = "*") -> dict[str, Any] | None:
        """Đọc 1 dòng.

        Args:
            table: Tên bảng.
            filters: Cột → giá trị.
            columns: Cột cần lấy.

        Returns:
            Dòng đầu tiên thỏa điều kiện, hoặc None.
        """
        rows = await self.select(table, filters, columns=columns, limit=1)
        return rows[0] if rows else None

    async def insert(self, table: str, row: dict[str, Any], *, ignore_duplicates: bool = False) -> bool:
        """Thêm 1 dòng.

        Args:
            table: Tên bảng.
            row: Dữ liệu.
            ignore_duplicates: Trùng khóa chính thì bỏ qua thay vì báo lỗi.

        Returns:
            True nếu dòng được thêm (False khi trùng và được bỏ qua).
        """
        prefer = "resolution=ignore-duplicates,return=representation" if ignore_duplicates else "return=representation"
        return bool((await self._call("POST", table, params={}, prefer=prefer, json=row)).json())

    async def update(self, table: str, filters: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        """Cập nhật các dòng thỏa điều kiện.

        Args:
            table: Tên bảng.
            filters: Cột → giá trị.
            values: Cột → giá trị mới.

        Returns:
            Các dòng sau cập nhật.
        """
        return (await self._call("PATCH", table, params=_where(filters), prefer="return=representation", json=values)).json()

    async def delete(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Xóa các dòng thỏa điều kiện.

        Args:
            table: Tên bảng.
            filters: Cột → giá trị (bắt buộc, không xóa cả bảng).

        Returns:
            Các dòng đã xóa.
        """
        if not filters:
            raise DatabaseError("Từ chối xóa không điều kiện", context={"table": table})
        return (await self._call("DELETE", table, params=_where(filters), prefer="return=representation")).json()


__all__ = ["Database"]
