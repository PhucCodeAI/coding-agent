"""Đọc / ghi file JSON trong 1 bucket Supabase Storage."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from app.errors import StorageError
from app.integrations.supabase import SupabaseClient


def _safe_path(path: str) -> str:
    """Kiểm đường dẫn object (không rỗng, không `..`, không bắt đầu bằng `/`).

    Args:
        path: Đường dẫn trong bucket.

    Returns:
        Đường dẫn đã chuẩn hóa.

    Raises:
        StorageError: Đường dẫn không hợp lệ.
    """
    p = path.strip()
    if not p or p.startswith("/") or ".." in p.split("/"):
        raise StorageError(f"Đường dẫn object không hợp lệ: {path!r}")
    return p


class Storage:
    """Object storage: chỉ write / read / delete."""

    def __init__(self, client: SupabaseClient, bucket: str) -> None:
        """Tạo repository.

        Args:
            client: Supabase client dùng chung.
            bucket: Tên bucket.
        """
        self.client = client
        self.bucket = bucket

    async def write_json(self, path: str, data: Any) -> None:
        """Ghi (đè) 1 object JSON.

        Args:
            path: Đường dẫn trong bucket.
            data: Dữ liệu JSON-able.

        Raises:
            StorageError: Ghi thất bại.
        """
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        try:
            await self.client.request(
                "POST",
                f"/storage/v1/object/{self.bucket}/{quote(_safe_path(path), safe='/')}",
                content=body,
                headers={"Content-Type": "application/json", "x-upsert": "true"},
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise StorageError(f"Ghi {path} thất bại", context={"path": path, "bytes": len(body)}) from e

    async def read_json(self, path: str) -> Any | None:
        """Đọc 1 object JSON.

        Args:
            path: Đường dẫn trong bucket.

        Returns:
            Dữ liệu, hoặc None nếu không tồn tại.

        Raises:
            StorageError: Đọc thất bại hoặc không phải JSON.
        """
        try:
            response = await self.client.request("GET", f"/storage/v1/object/authenticated/{self.bucket}/{quote(_safe_path(path), safe='/')}")
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 404):
                return None
            raise StorageError(f"Đọc {path} thất bại", context={"path": path, "status": e.response.status_code}) from e
        except (httpx.RequestError, ValueError) as e:
            raise StorageError(f"Đọc {path} thất bại", context={"path": path, "error": type(e).__name__}) from e

    async def delete(self, paths: list[str]) -> None:
        """Xóa các object.

        Args:
            paths: Đường dẫn trong bucket.

        Raises:
            StorageError: Xóa thất bại.
        """
        if not paths:
            return
        try:
            await self.client.request("DELETE", f"/storage/v1/object/{self.bucket}", json={"prefixes": [_safe_path(p) for p in paths]})
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise StorageError(f"Xóa {len(paths)} object thất bại", context={"paths": paths[:5]}) from e


__all__ = ["Storage"]
