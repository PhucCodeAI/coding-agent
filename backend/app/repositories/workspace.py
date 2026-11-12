"""Workspace của 1 phiên: path jail, ghi file nguyên tử, sửa theo đoạn (search/replace) — `Workspace` + schema `FileAction`/`FileEdit`/`ApplyResult`."""

from __future__ import annotations

import asyncio
import difflib
import os
from pathlib import Path

from loguru import logger

from app.dtos.workspace import ApplyResult, FileAction, FileEdit
from app.errors import (
    EditApplyError,
    FileNotFoundInWorkspaceError,
    PathSecurityError,
)


class Workspace:
    """Quản lý đọc/ghi tệp tin cô lập, ngăn chặn tấn công Path Traversal."""

    def __init__(self, root_dir: Path) -> None:
        """Khởi tạo Workspace.

        Args:
            root_dir: Thư mục gốc cô lập của phiên làm việc.
        """
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # Security helpers
    # --------------------------------------------------------
    def _resolve_safe_path(self, relative_path: str) -> Path:
        """Chuẩn hóa và kiểm tra đường dẫn nằm trong root_dir.

        Args:
            relative_path: Đường dẫn tương đối do LLM cung cấp.

        Returns:
            Đường dẫn tuyệt đối an toàn trên máy host.

        Raises:
            PathSecurityError: Khi path traversal, symlink escape, hoặc chạm .git.
        """
        raw_candidate = self.root_dir / relative_path
        candidate = raw_candidate.resolve()

        if not candidate.is_relative_to(self.root_dir):
            raise PathSecurityError(
                f"Đường dẫn '{relative_path}' nằm ngoài sandbox",
                context={"relative_path": relative_path, "root": str(self.root_dir)},
            )

        if ".git" in candidate.parts:
            raise PathSecurityError(
                "Không được phép sửa đổi trực tiếp thư mục .git",
                context={"relative_path": relative_path},
            )
        self._assert_no_symlink(raw_candidate)
        return candidate

    def _assert_no_symlink(self, target: Path) -> None:
        """Kiểm tra không có symlink trên đường dẫn từ root tới target.

        Args:
            target: Đường dẫn tuyệt đối (chưa resolve) nằm dưới root_dir.

        Raises:
            PathSecurityError: Khi phát hiện symlink trong path components.
        """
        try:
            relative_parts = target.relative_to(self.root_dir).parts
        except ValueError as e:
            raise PathSecurityError(
                f"Path không thuộc root: {target}",
                context={"target": str(target)},
            ) from e

        current = self.root_dir
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                raise PathSecurityError(
                    f"Phát hiện symlink tại: {current}",
                    context={"symlink": str(current), "part": part},
                )

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------
    async def apply_file_action(self, action: FileAction) -> ApplyResult:
        """Thực thi một FileAction (create/update/delete) lên workspace.

        Args:
            action: DTO mô tả thao tác cần thực thi.

        Returns:
            ApplyResult chứa thống kê diff.

        Raises:
            PathSecurityError: Khi đường dẫn không an toàn.
            FileNotFoundInWorkspaceError: Khi update/delete file không tồn tại.
            EditApplyError: Khi không match được edits trong file.
        """
        safe_path = self._resolve_safe_path(action.path)

        if action.action == "create":
            return await self._apply_create(safe_path, action.path, action.content or "")
        if action.action == "update":
            return await self._apply_update(safe_path, action.path, action.edits or [])
        if action.action == "delete":
            return await self._apply_delete(safe_path, action.path)

        raise PathSecurityError(
            f"Action không hợp lệ: {action.action}",
            context={"action": action.action},
        )

    async def read_file(self, relative_path: str) -> str:
        """Đọc nội dung một file trong workspace.

        Args:
            relative_path: Đường dẫn tương đối cần đọc.

        Returns:
            Nội dung file (UTF-8).

        Raises:
            PathSecurityError: Khi đường dẫn không an toàn.
            FileNotFoundInWorkspaceError: Khi file không tồn tại.
        """
        safe_path = self._resolve_safe_path(relative_path)
        if not safe_path.exists() or not safe_path.is_file():
            raise FileNotFoundInWorkspaceError(
                f"File không tồn tại: {relative_path}",
                context={"path": relative_path},
            )
        return await asyncio.to_thread(safe_path.read_text, encoding="utf-8")

    async def list_files(self) -> list[str]:
        """Liệt kê toàn bộ file trong workspace (đệ quy, bỏ qua .git).

        Returns:
            Danh sách đường dẫn tương đối (dùng '/' làm separator).
        """
        results: list[str] = []

        def _walk() -> None:
            """Duyệt workspace, bỏ thư mục ẩn/cache."""
            for path in self.root_dir.rglob("*"):
                if not path.is_file():
                    continue
                if ".git" in path.parts or ".deps" in path.parts or ".pytest_cache" in path.parts:
                    continue
                results.append(path.relative_to(self.root_dir).as_posix())

        await asyncio.to_thread(_walk)
        return sorted(results)

    # --------------------------------------------------------
    # Internal apply methods
    # --------------------------------------------------------
    async def _apply_create(
        self,
        safe_path: Path,
        relative_path: str,
        content: str,
    ) -> ApplyResult:
        """Tạo hoặc ghi đè file với atomic write.

        Args:
            safe_path: Đường dẫn tuyệt đối đã validate.
            relative_path: Đường dẫn tương đối để report.
            content: Nội dung file.

        Returns:
            ApplyResult với diff so với file cũ (nếu có).
        """
        old_content = ""
        if safe_path.exists() and safe_path.is_file():
            old_content = await asyncio.to_thread(safe_path.read_text, encoding="utf-8", errors="replace")

        if safe_path.is_dir():
            raise EditApplyError(f"'{relative_path}' là THƯ MỤC, không thể ghi đè thành file. Đổi tên file (VD thêm đuôi .py) hoặc chọn đường dẫn khác.")
        try:
            await asyncio.to_thread(safe_path.parent.mkdir, parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError) as e:
            blocker = next((p for p in [safe_path.parent, *safe_path.parent.parents] if p.is_file()), None)
            name = blocker.relative_to(self.root_dir).as_posix() if blocker else relative_path
            raise EditApplyError(
                f"Không thể tạo '{relative_path}': '{name}' đang là một FILE (không phải thư mục). "
                f'Xóa nó bằng delete_file(path="{name}") (hoặc đổi tên) rồi ghi lại.'
            ) from e
        await self._atomic_write(safe_path, content)

        added, removed = _count_diff_lines(old_content, content)
        bytes_written = len(content.encode("utf-8"))

        logger.debug("create {} (+{} -{} /{}B)", relative_path, added, removed, bytes_written)

        return ApplyResult(
            path=relative_path,
            action="create",
            lines_added=added,
            lines_removed=removed,
            bytes_written=bytes_written,
        )

    async def _apply_update(
        self,
        safe_path: Path,
        relative_path: str,
        edits: list[FileEdit],
    ) -> ApplyResult:
        """Áp dụng danh sách search/replace lên file đã tồn tại.

        Args:
            safe_path: Đường dẫn tuyệt đối đã validate.
            relative_path: Đường dẫn tương đối để report.
            edits: Danh sách cặp search/replace.

        Returns:
            ApplyResult với diff tổng.

        Raises:
            FileNotFoundInWorkspaceError: File không tồn tại.
            EditApplyError: Không match được một edit nào đó.
        """
        if not safe_path.exists() or not safe_path.is_file():
            raise FileNotFoundInWorkspaceError(
                f"File cần update không tồn tại: {relative_path}",
                context={"path": relative_path},
            )

        try:
            old_content = await asyncio.to_thread(safe_path.read_text, encoding="utf-8")
        except UnicodeDecodeError as e:
            raise EditApplyError(
                f"File '{relative_path}' không phải UTF-8 text, không thể update bằng edits",
                context={"path": relative_path},
            ) from e
        new_content = old_content

        for idx, edit in enumerate(edits):
            new_content = _apply_single_edit(new_content, edit, relative_path, idx)

        await self._atomic_write(safe_path, new_content)

        added, removed = _count_diff_lines(old_content, new_content)
        bytes_written = len(new_content.encode("utf-8"))

        logger.debug(
            "update {} ({} edits, +{} -{} /{}B)",
            relative_path,
            len(edits),
            added,
            removed,
            bytes_written,
        )

        return ApplyResult(
            path=relative_path,
            action="update",
            lines_added=added,
            lines_removed=removed,
            bytes_written=bytes_written,
        )

    async def _apply_delete(
        self,
        safe_path: Path,
        relative_path: str,
    ) -> ApplyResult:
        """Xóa file trong workspace.

        Args:
            safe_path: Đường dẫn tuyệt đối đã validate.
            relative_path: Đường dẫn tương đối để report.

        Returns:
            ApplyResult với số dòng bị xóa.

        Raises:
            FileNotFoundInWorkspaceError: File không tồn tại.
        """
        if not safe_path.exists() or not safe_path.is_file():
            raise FileNotFoundInWorkspaceError(
                f"File cần xóa không tồn tại: {relative_path}",
                context={"path": relative_path},
            )

        old_content = await asyncio.to_thread(safe_path.read_text, encoding="utf-8", errors="replace")
        await asyncio.to_thread(safe_path.unlink)

        added, removed = _count_diff_lines(old_content, "")
        logger.debug("delete {} (-{})", relative_path, removed)

        return ApplyResult(
            path=relative_path,
            action="delete",
            lines_added=0,
            lines_removed=removed,
            bytes_written=0,
        )

    async def _atomic_write(self, target: Path, content: str) -> None:
        """Ghi file atomic: ghi tmp → os.replace.

        Args:
            target: Đường dẫn file đích.
            content: Nội dung cần ghi.
        """

        def _write() -> None:
            """Ghi nguyên tử: ghi file tạm rồi đổi tên."""
            tmp_path = target.with_name(f".{target.name}.tmp")
            try:
                tmp_path.write_text(content, encoding="utf-8")
                os.replace(tmp_path, target)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

        await asyncio.to_thread(_write)


# ============================================================
# Module-level helpers
# ============================================================
def preview_edits(content: str, edits: list[FileEdit], relative_path: str) -> str:
    """Nội dung file sau khi áp `edits` (không ghi đĩa) — để kiểm code trước khi ghi.

    Args:
        content: Nội dung hiện tại.
        edits: Các cặp search/replace.
        relative_path: Đường dẫn tương đối (cho thông báo lỗi).

    Returns:
        Nội dung mới.

    Raises:
        EditApplyError: Không match được một edit nào đó.
    """
    for idx, edit in enumerate(edits):
        content = _apply_single_edit(content, edit, relative_path, idx)
    return content


def _apply_single_edit(
    content: str,
    edit: FileEdit,
    relative_path: str,
    edit_index: int,
) -> str:
    """Áp dụng một FileEdit với layered fallback.

    Tầng 1 — exact match.
    Tầng 2 — whitespace-insensitive (rstrip mỗi dòng).
    Fail cả 2 → raise EditApplyError.

    Args:
        content: Nội dung file hiện tại.
        edit: Cặp search/replace.
        relative_path: Đường dẫn để báo lỗi.
        edit_index: Vị trí edit trong danh sách (để debug).

    Returns:
        Nội dung mới sau khi apply.

    Raises:
        EditApplyError: Khi không match được edit.
    """
    if edit.old_text in content:
        return content.replace(edit.old_text, edit.new_text, 1)

    result = _apply_ws_insensitive(content, edit.old_text, edit.new_text)
    if result is not None:
        logger.debug(
            "Edit #{} trên {} match bằng whitespace-insensitive fallback",
            edit_index,
            relative_path,
        )
        return result

    raise EditApplyError(
        f"Không match được edit #{edit_index} trong '{relative_path}': {edit.old_text[:80]!r}",
        context={
            "path": relative_path,
            "edit_index": edit_index,
            "old_text_preview": edit.old_text[:200],
        },
    )


def _apply_ws_insensitive(
    content: str,
    old_text: str,
    new_text: str,
) -> str | None:
    """Thử match old_text trong content bỏ qua trailing whitespace mỗi dòng.

    Args:
        content: Nội dung file.
        old_text: Đoạn text cần tìm.
        new_text: Đoạn text thay thế.

    Returns:
        Nội dung mới nếu match, None nếu không.
    """
    content_lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines()
    if not old_lines:
        return None

    old_stripped = [line.rstrip() for line in old_lines]
    n = len(old_stripped)

    for start in range(len(content_lines) - n + 1):
        window = content_lines[start : start + n]
        if [line.rstrip("\r\n").rstrip() for line in window] == old_stripped:
            new_block = new_text
            if new_text and not new_text.endswith("\n") and window[-1].endswith("\n"):
                new_block = new_text + "\n"

            prefix = "".join(content_lines[:start])
            suffix = "".join(content_lines[start + n :])
            return prefix + new_block + suffix

    return None


def _count_diff_lines(old: str, new: str) -> tuple[int, int]:
    """Đếm số dòng added/removed giữa hai nội dung.

    Args:
        old: Nội dung gốc.
        new: Nội dung mới.

    Returns:
        Tuple (added, removed).
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()

    added = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1

    return added, removed
