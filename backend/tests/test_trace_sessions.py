import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.errors import AppError, SessionNotFoundError, TraceNotFoundError
from app.integrations import git
from app.routers.error_handlers import register_error_handlers
from app.routers.sessions import session_router
from app.routers.traces import trace_router
from app.services.session_service import SessionService
from app.services.trace_service import TraceService, aggregate_sessions, extract_error_code, filter_sessions, to_turn


def row(trace_id: str, session_id: str, created_at: str, status: str = "SUCCESS", **extra: Any) -> dict[str, Any]:
    """Tạo 1 dòng bảng traces giả.

    Args:
        trace_id: Mã trace.
        session_id: Mã phiên.
        created_at: Thời điểm tạo (ISO).
        status: Trạng thái lượt.
        **extra: Cột khác ghi đè.

    Returns:
        Dòng trace.
    """
    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "prompt": f"prompt {trace_id}",
        "status": status,
        "duration_ms": 1000,
        "total_tokens": 110,
        "input_tokens": 100,
        "output_tokens": 10,
        "reasoning_tokens": 0,
        "cache_tokens": 5,
        "cost": 0.5,
        "error_message": None,
        "created_at": created_at,
        "finished_at": None,
        **extra,
    }


ROWS = [
    row("tr_000000000003", "ses_a", "2026-01-01T00:03:00", "FAILED", error_message="[LLM_TIMEOUT] quá thời gian"),
    row("tr_000000000001", "ses_a", "2026-01-01T00:01:00"),
    row("tr_000000000002", "ses_b", "2026-01-01T00:02:00", finished_at="2026-01-01T00:09:00"),
]


class FakeDB:
    """Database giả trong bộ nhớ: lọc bằng nhau, sắp xếp theo 1 cột, ghi nhận update."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """Tạo database giả.

        Args:
            tables: Tên bảng → danh sách dòng.
        """
        self.tables = tables

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
            filters: Cột → giá trị.
            columns: Bỏ qua.
            order: `cột.asc|desc`.
            limit: Số dòng tối đa.
            offset: Bỏ qua.

        Returns:
            Danh sách dòng.
        """
        rows = [r for r in self.tables.get(table, []) if all(r.get(k) == v for k, v in (filters or {}).items())]
        if order:
            col, direction = order.split(".")
            rows.sort(key=lambda r: str(r.get(col) or ""), reverse=direction == "desc")
        return rows[:limit] if limit else rows

    async def update(self, table: str, filters: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        """Cập nhật các dòng thỏa điều kiện.

        Args:
            table: Tên bảng.
            filters: Cột → giá trị.
            values: Giá trị mới.

        Returns:
            Các dòng sau cập nhật.
        """
        rows = await self.select(table, filters)
        for r in rows:
            r.update(values)
        return rows

    async def delete(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Xóa các dòng thỏa điều kiện.

        Args:
            table: Tên bảng.
            filters: Cột → giá trị.

        Returns:
            Các dòng đã xóa.
        """
        gone = await self.select(table, filters)
        self.tables[table] = [r for r in self.tables.get(table, []) if r not in gone]
        return gone


class FakeStorage:
    """Storage giả: ghi nhận đường dẫn bị xóa."""

    def __init__(self) -> None:
        """Tạo storage giả."""
        self.deleted: list[str] = []

    async def delete(self, paths: list[str]) -> None:
        """Ghi nhận xóa blob.

        Args:
            paths: Đường dẫn blob.
        """
        self.deleted.extend(paths)


def fake_db() -> FakeDB:
    """Database giả có bảng traces và sessions.

    Returns:
        Database giả.
    """
    sessions = [{"session_id": "ses_a", "title": "Máy tính", "message_count": 4, "created_at": "x", "updated_at": "x"}]
    return FakeDB({"traces": [dict(r, storage_path=f"{r['session_id']}/{r['trace_id']}.json") for r in ROWS], "sessions": sessions})


def test_extract_error_code():
    """Tách mã lỗi từ tiền tố "[CODE]"; không có tiền tố → None."""
    assert extract_error_code("[LLM_TIMEOUT] quá thời gian") == "LLM_TIMEOUT"
    assert extract_error_code("lỗi thường [X]") is None
    assert extract_error_code(None) is None


def test_aggregate_sessions_sums_and_orders_by_last_activity():
    """Gom theo phiên: đếm trạng thái, cộng token/chi phí, lượt cuối theo thời gian, phiên mới hoạt động trước."""
    out = aggregate_sessions(list(ROWS), {"ses_a": "Máy tính"})
    assert [s.session_id for s in out] == ["ses_b", "ses_a"]
    a = out[1]
    assert a.title == "Máy tính"
    assert (a.turn_count, a.success_count, a.failed_count) == (2, 1, 1)
    assert (a.total_tokens, a.input_tokens, a.output_tokens, a.cache_tokens, a.duration_ms) == (220, 200, 20, 10, 2000)
    assert a.cost == pytest.approx(1.0)
    assert (a.last_status, a.last_trace_id) == ("FAILED", "tr_000000000003")
    assert (a.started_at, a.last_activity_at) == ("2026-01-01T00:01:00", "2026-01-01T00:03:00")
    assert out[0].title == "prompt tr_000000000002"
    assert out[0].last_activity_at == "2026-01-01T00:09:00"


def test_filter_sessions_by_query_and_status():
    """Lọc theo từ khóa (tiêu đề / session_id, không phân biệt hoa thường) và trạng thái lượt cuối."""
    out = aggregate_sessions(list(ROWS), {"ses_a": "Máy tính"})
    assert [s.session_id for s in filter_sessions(out, "MÁY")] == ["ses_a"]
    assert [s.session_id for s in filter_sessions(out, "SES_B")] == ["ses_b"]
    assert [s.session_id for s in filter_sessions(out, status="FAILED")] == ["ses_a"]
    assert filter_sessions(out, "máy", "SUCCESS") == []


def test_to_turn_maps_fields():
    """Dòng trace → lượt, kèm turn_index và error_code."""
    turn = to_turn(ROWS[0], 2)
    assert (turn.turn_index, turn.status, turn.error_code, turn.input_tokens) == (2, "FAILED", "LLM_TIMEOUT", 100)


async def test_trace_service_list_detail_delete():
    """TraceService: trang phiên, chi tiết kèm lượt cũ → mới, 404 khi không có trace, xóa trace của phiên."""
    storage = FakeStorage()
    svc = TraceService(fake_db(), storage)
    page = await svc.list_sessions(limit=1)
    assert page.total == 2 and [s.session_id for s in page.items] == ["ses_b"]
    detail = await svc.session_detail("ses_a")
    assert detail.title == "Máy tính"
    assert [(t.turn_index, t.trace_id) for t in detail.turns] == [(1, "tr_000000000001"), (2, "tr_000000000003")]
    with pytest.raises(TraceNotFoundError):
        await svc.session_detail("ses_none")
    await svc.delete_session("ses_a")
    assert sorted(storage.deleted) == ["ses_a/tr_000000000001.json", "ses_a/tr_000000000003.json"]
    assert (await svc.list_sessions()).total == 1


async def test_rename_session(tmp_path: Path):
    """Đổi tên phiên (gộp khoảng trắng); phiên không có → SessionNotFoundError."""
    db = fake_db()
    svc = SessionService(db, TraceService(db, FakeStorage()), str(tmp_path))
    renamed = await svc.rename("ses_a", "  Tên   mới ")
    assert renamed.title == "Tên mới"
    with pytest.raises(SessionNotFoundError):
        await svc.rename("ses_none", "x")


async def test_commits_changes_and_diff(tmp_path: Path):
    """Workspace chưa có → rỗng; có commit → log + diff; hash lạ → NOT_FOUND; file mới chưa commit hiện ở changes."""
    db = fake_db()
    svc = SessionService(db, TraceService(db, FakeStorage()), str(tmp_path))
    assert (await svc.list_commits("ses_a")).commits == []
    assert (await svc.working_changes("ses_a")).files == []
    with pytest.raises(AppError):
        await svc.commit_diff("ses_a", "abcd")

    root = tmp_path / "ses_a"
    root.mkdir()
    await git.init_repo_if_needed(root)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "đầu tiên"], cwd=root, check=True)
    (root / "b.py").write_text("y = 2\n")

    commits = (await svc.list_commits("ses_a")).commits
    assert len(commits) >= 1 and "a.py" in commits[0].files
    diff = await svc.commit_diff("ses_a", commits[0].hash)
    assert "+x = 1" in diff.diff and not diff.truncated
    changes = await svc.working_changes("ses_a")
    assert {"path": "b.py", "status": "added"} in [f.model_dump() for f in changes.files]
    assert "+y = 2" in changes.diff
    with pytest.raises(AppError) as exc:
        await svc.commit_diff("ses_a", "deadbeef")
    assert exc.value.code == "NOT_FOUND"


async def test_routes_order_and_validation(tmp_path: Path):
    """`/traces/sessions...` không bị `/{trace_id}` bắt nhầm; hash commit sai định dạng → 422; PATCH đổi tên trả SessionDTO."""
    db = fake_db()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(trace_router)
    app.include_router(session_router)
    app.state.traces = TraceService(db, FakeStorage())
    app.state.sessions = SessionService(db, app.state.traces, str(tmp_path))
    app.state.chat = type("Chat", (), {"live_trace": lambda self, sid: None})()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        page = (await client.get("/traces/sessions", params={"q": "máy"})).json()
        assert page["total"] == 1 and page["items"][0]["session_id"] == "ses_a"
        assert (await client.get("/traces/sessions/ses_a")).json()["turn_count"] == 2
        assert (await client.get("/traces/sessions/ses_none")).status_code == 404
        live = await client.get("/traces/sessions/ses_a/live")
        assert live.status_code == 200 and live.json() is None
        assert (await client.get("/sessions/ses_a/commits/XYZ/diff")).status_code == 422
        assert (await client.get("/sessions/ses_a/commits/abcd/diff")).status_code == 404
        renamed = await client.patch("/sessions/ses_a", json={"title": "Mới"})
        assert renamed.status_code == 200 and renamed.json()["title"] == "Mới"
        assert (await client.patch("/sessions/ses_none", json={"title": "x"})).status_code == 404
        assert (await client.delete("/traces/sessions/ses_a")).status_code == 204


async def test_changes_without_repo_skip_installed_deps(tmp_path: Path):
    """Workspace chưa có repo (trước commit đầu): thư viện trong .deps và cache không bị tính là thay đổi."""
    db = fake_db()
    svc = SessionService(db, TraceService(db, FakeStorage()), str(tmp_path))
    root = tmp_path / "ses_a"
    (root / ".deps" / "pytest").mkdir(parents=True)
    (root / ".deps" / "pytest" / "__init__.py").write_text("")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
    (root / "a.py").write_text("x = 1\n")
    changes = await svc.working_changes("ses_a")
    assert [f.path for f in changes.files] == ["a.py"]


async def test_write_file_and_exec_routes(tmp_path: Path):
    """PUT file ghi vào workspace; đường dẫn thoát workspace bị chặn; phiên đang chạy → 409; chưa có workspace → 404."""
    db = fake_db()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(session_router)
    app.state.traces = TraceService(db, FakeStorage())
    app.state.sessions = SessionService(db, app.state.traces, str(tmp_path))
    busy: set[str] = set()
    app.state.chat = type("Chat", (), {"is_busy": lambda self, sid: sid in busy})()
    (tmp_path / "ses_a").mkdir()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        res = await client.put("/sessions/ses_a/files/content", json={"path": "pkg/a.py", "content": "x = 1\n"})
        assert res.status_code == 200 and res.json() == {"path": "pkg/a.py", "size": 6}
        assert (tmp_path / "ses_a" / "pkg" / "a.py").read_text() == "x = 1\n"
        assert (await client.put("/sessions/ses_a/files/content", json={"path": "../x.py", "content": ""})).status_code >= 400
        assert (await client.put("/sessions/ses_b/files/content", json={"path": "a.py", "content": ""})).status_code == 404
        busy.add("ses_a")
        assert (await client.put("/sessions/ses_a/files/content", json={"path": "a.py", "content": ""})).status_code == 409
        assert (await client.post("/sessions/ses_a/exec", json={"command": "ls"})).status_code == 409
