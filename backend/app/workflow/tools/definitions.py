"""Định nghĩa mọi công cụ của agent: `@tool(group)` + docstring đầy đủ; thân hàm CHỈ gọi sang `handlers.py`."""

from __future__ import annotations

from app.dtos.tool import ToolContext, ToolOutput
from app.workflow.tools import handlers as h
from app.workflow.tools.registry import tool


@tool(group="workspace_read")
async def list_files(ctx: ToolContext, path_prefix: str = "") -> ToolOutput:
    """Liệt kê file trong workspace.

    Dùng khi: muốn biết dự án đang có file gì.

    Args:
        path_prefix: lọc theo tiền tố đường dẫn
    """
    return await h.list_files(ctx, path_prefix)


@tool(group="workspace_read")
async def read_file(ctx: ToolContext, path: str, start_line: int = 1, max_lines: int = 200) -> ToolOutput:
    """Đọc file (kèm số dòng, không thuộc nội dung).

    Dùng khi: trước khi sửa/ghi đè 1 file đã có, hoặc cần hiểu code.

    Args:
        path: đường dẫn file
        start_line: dòng bắt đầu
        max_lines: số dòng (≤400)
    """
    return await h.read_file(ctx, path, start_line, max_lines)


@tool(group="workspace_read")
async def read_outline(ctx: ToolContext, path: str) -> ToolOutput:
    """Xem cấu trúc rút gọn của 1 file Python: import, class (init + method), hàm kèm tham số/kiểu, số dòng.

    Dùng khi: muốn nắm file nhanh hoặc chọn đoạn cần đọc, thay vì đọc cả file.

    Args:
        path: đường dẫn file .py
    """
    return await h.read_outline(ctx, path)


@tool(group="workspace_read")
async def search_text(ctx: ToolContext, query: str, path_prefix: str = "") -> ToolOutput:
    """Tìm chuỗi trong mọi file.

    Dùng khi: tìm nơi định nghĩa/sử dụng 1 tên hàm, class, chuỗi.

    Args:
        query: chuỗi cần tìm
        path_prefix: lọc theo tiền tố đường dẫn
    """
    return await h.search_text(ctx, query, path_prefix)


@tool(group="workspace_write")
async def write_file(ctx: ToolContext, path: str, content: str) -> ToolOutput:
    """Tạo/ghi đè cả file (file đã có: read_file trước). .py tự kiểm cú pháp + format.

    Dùng khi: tạo file mới, hoặc viết lại hầu hết file.

    Args:
        path: đường dẫn file
        content: TOÀN BỘ nội dung file (được rỗng)
    """
    return await h.write_file(ctx, path, content)


@tool(group="workspace_write")
async def edit_file(ctx: ToolContext, path: str, old_text: str, new_text: str) -> ToolOutput:
    """Thay 1 đoạn trong file đã có (read_file trước).

    Dùng khi: sửa nhỏ trong file đã có.

    Args:
        path: file đã có
        old_text: đoạn gốc, chép CHÍNH XÁC, duy nhất trong file
        new_text: đoạn thay thế (rỗng = xóa)
    """
    return await h.edit_file(ctx, path, old_text, new_text)


@tool(group="workspace_write")
async def delete_file(ctx: ToolContext, path: str) -> ToolOutput:
    """Xóa file.

    Dùng khi: file thừa hoặc tạo nhầm.

    Args:
        path: file cần xóa
    """
    return await h.delete_file(ctx, path)


@tool(group="format")
async def format_files(ctx: ToolContext, paths: list[str]) -> ToolOutput:
    """Format file .py bằng ruff.

    Dùng khi: hiếm khi (ghi .py đã tự format).

    Args:
        paths: danh sách file .py
    """
    return await h.format_files(ctx, paths)


@tool(group="test")
async def run_tests(ctx: ToolContext, pattern: str = "test_*.py") -> ToolOutput:
    """Chạy test (pytest) trong sandbox; lỗi trả bản tóm tắt: nơi sai + lỗi gốc.

    Dùng khi: sau khi viết/sửa code hoặc test.

    Args:
        pattern: mẫu tên file test (VD test_unit_*.py)
    """
    return await h.run_tests(ctx, pattern)


@tool(group="shell")
async def run_command(ctx: ToolContext, argv: list[str]) -> ToolOutput:
    """Chạy 1 lệnh python trong sandbox (không docker/pip/mạng).

    Dùng khi: chạy thử script, in giá trị để gỡ lỗi.

    Args:
        argv: danh sách, bắt đầu bằng python, VD ["python","-c","print(1)"]
    """
    return await h.run_command(ctx, argv)


@tool(group="git_read")
async def git_status(ctx: ToolContext) -> ToolOutput:
    """Danh sách file thay đổi chưa commit.

    Dùng khi: cần biết đã đổi những gì.
    """
    return await h.git_status(ctx)


@tool(group="git_read")
async def git_diff(ctx: ToolContext, path: str = "") -> ToolOutput:
    """Diff các thay đổi chưa commit.

    Dùng khi: review code.

    Args:
        path: chỉ file này
    """
    return await h.git_diff(ctx, path)


@tool(group="git_write")
async def git_commit(ctx: ToolContext, message: str) -> ToolOutput:
    """Commit mọi thay đổi trong workspace.

    Dùng khi: việc đã xong và test pass.

    Args:
        message: commit message
    """
    return await h.git_commit(ctx, message)


@tool(group="finish")
async def finish(ctx: ToolContext, status: str, answer: str, commit_message: str = "") -> ToolOutput:
    """Kết thúc lượt và gửi câu trả lời cuối cho người dùng.

    Dùng khi: việc đã xong (test pass) hoặc không thể làm tiếp.

    Args:
        status: done | failed | blocked.
        answer: câu trả lời cho người dùng (Markdown ngắn: đã làm gì, file chính, cách chạy test, lưu ý).
        commit_message: commit message (tuỳ chọn; bỏ trống = tự sinh).
    """
    return await h.finish(ctx, status, answer, commit_message)


@tool(group="ask_user")
async def ask_user(ctx: ToolContext, question: str, options: list[str], recommended: str = "") -> ToolOutput:
    """Hỏi người dùng 1 câu trắc nghiệm và chờ trả lời (người dùng luôn có thể tự nhập câu khác).

    Dùng khi: quyết định cốt lõi không tự suy ra được. Chi tiết nhỏ thì tự quyết.

    Args:
        question: câu hỏi ngắn.
        options: 2–4 lựa chọn.
        recommended: lựa chọn khuyến nghị (phải nằm trong options; bỏ trống = lựa chọn đầu).
    """
    return await h.ask_user(ctx, question, options, recommended)


@tool(group="install")
async def install_packages(ctx: ToolContext, packages: list[str]) -> ToolOutput:
    """Cài thư viện Python (pip, có mạng) vào workspace và ghi requirements.txt.

    Dùng khi: việc cần thư viện ngoài (ngoài thư viện chuẩn) — gọi TRƯỚC khi viết code/test dùng thư viện đó.

    Args:
        packages: 1–10 tên thư viện PyPI, có thể kèm phiên bản (VD "requests==2.32.3").
    """
    return await h.install_packages(ctx, packages)


@tool(group="inspect")
async def read_log(ctx: ToolContext, grep: str = "", tail_lines: int = 40) -> ToolOutput:
    """Đọc log đầy đủ của lệnh sandbox gần nhất (test, chạy lệnh).

    Dùng khi: bản tóm tắt lỗi chưa đủ, cần xem thêm dòng quanh chỗ sai.

    Args:
        grep: chỉ lấy các dòng chứa chuỗi này
        tail_lines: số dòng cuối (≤120) khi không có grep
    """
    return await h.read_log(ctx, grep, tail_lines)


@tool(group="inspect")
async def find_files(ctx: ToolContext, pattern: str) -> ToolOutput:
    """Tìm file theo mẫu tên (glob).

    Dùng khi: biết tên/đuôi file nhưng chưa biết nó nằm ở thư mục nào.

    Args:
        pattern: VD "*.py", "test_*.py", "services/*"
    """
    return await h.find_files(ctx, pattern)


@tool(group="inspect")
async def check_syntax(ctx: ToolContext, paths: list[str] = None) -> ToolOutput:  # type: ignore[assignment]
    """Kiểm tra cú pháp Python nhanh (không chạy code).

    Dùng khi: vừa sửa nhiều file và muốn chắc không có lỗi cú pháp trước khi chạy test.

    Args:
        paths: danh sách file .py; bỏ trống = mọi file .py
    """
    return await h.check_syntax(ctx, paths or [])


@tool(group="inspect")
async def pip_list(ctx: ToolContext) -> ToolOutput:
    """Liệt kê thư viện đã cài thêm trong workspace.

    Dùng khi: trước khi import thư viện ngoài, hoặc khi gặp ModuleNotFoundError.
    """
    return await h.pip_list(ctx)


@tool(group="inspect")
async def git_log(ctx: ToolContext, limit: int = 10) -> ToolOutput:
    """Lịch sử commit gần đây của workspace.

    Dùng khi: cần biết thay đổi trước đó đã làm gì.

    Args:
        limit: số commit (≤30)
    """
    return await h.git_log(ctx, limit)


@tool(group="inspect")
async def system_info(ctx: ToolContext) -> ToolOutput:
    """Xem tài nguyên hệ thống và giới hạn của sandbox: CPU, RAM, tải, ổ đĩa, Python, mạng.

    Dùng khi: nghi ngờ lỗi do thiếu tài nguyên/timeout, hoặc cần chọn cách chạy phù hợp.
    """
    return await h.system_info(ctx)
