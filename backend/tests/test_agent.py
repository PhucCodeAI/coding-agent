import pytest

from tests.conftest import FINISH, HAS_DOCKER, ScriptedLLM, call, run_turn


def of(events, name):
    """Lọc dữ liệu các sự kiện cùng tên.

    Args:
        events: Danh sách (tên, dữ liệu).
        name: Tên sự kiện.

    Returns:
        Danh sách dữ liệu.
    """
    return [d for n, d in events if n == name]


async def test_write_then_finish_succeeds_and_commits(base):
    """Ghi file rồi finish: lượt SUCCESS, có step/tool/file/commit/final/done theo thứ tự."""
    llm = ScriptedLLM([call("write_file", path="calc.py", content="def add(a, b):\n    return a + b\n")], FINISH)
    events = await run_turn(base, llm)
    names = [n for n, _ in events]
    assert names[0] == "session" and names[-1] == "done"
    assert of(events, "done")[0]["status"] == "SUCCESS"
    assert of(events, "final")[0] == {"status": "done", "text": "xong", "code": None}
    assert of(events, "file")[0]["path"] == "calc.py"
    assert of(events, "commit")[0]["files"]
    assert of(events, "step")[0]["tool_calls"][0]["name"] == "write_file"
    assert (base / "ses_test" / "calc.py").read_text().startswith("def add")


async def test_overwrite_without_reading_is_rejected(base):
    """Ghi đè file đã có mà chưa read_file → lỗi công cụ, file giữ nguyên."""
    (base / "ses_test").mkdir()
    (base / "ses_test" / "a.py").write_text("x = 1\n")
    events = await run_turn(base, ScriptedLLM([call("write_file", path="a.py", content="x = 2\n")], FINISH))
    tool = of(events, "tool")[0]
    assert not tool["ok"] and "read_file" in tool["output"]
    assert (base / "ses_test" / "a.py").read_text() == "x = 1\n"


async def test_path_escape_is_blocked(base):
    """Không đọc được file ngoài workspace."""
    events = await run_turn(base, ScriptedLLM([call("read_file", path="../../../../etc/passwd")], FINISH))
    tool = of(events, "tool")[0]
    assert not tool["ok"] and "root:" not in tool["output"]


async def test_three_failures_in_a_row_stop_the_run(base):
    """3 lệnh công cụ lỗi liên tiếp → dừng với AGENT_REPEATED_FAILURE."""
    events = await run_turn(base, ScriptedLLM([call("read_file", path="missing.py")]))
    assert of(events, "final")[0]["code"] == "AGENT_REPEATED_FAILURE"
    assert len(of(events, "step")) == 3


async def test_step_limit_stops_the_run(base):
    """Hết max_steps → dừng với AGENT_STEP_LIMIT."""
    events = await run_turn(base, ScriptedLLM([call("list_files")]), max_steps=2)
    assert of(events, "final")[0]["code"] == "AGENT_STEP_LIMIT"
    assert of(events, "done")[0]["status"] == "FAILED"


async def test_unknown_tool_is_reported_to_the_model(base):
    """Model gọi công cụ không có → kết quả lỗi liệt kê công cụ hợp lệ."""
    events = await run_turn(base, ScriptedLLM([call("deploy")], FINISH))
    assert "không có" in of(events, "tool")[0]["output"]


@pytest.mark.skipif(not HAS_DOCKER, reason="cần Docker đang chạy")
async def test_tests_run_inside_docker_sandbox(base):
    """run_tests chạy pytest thật trong container sandbox."""
    llm = ScriptedLLM(
        [call("write_file", path="test_ok.py", content="def test_ok() -> None:\n    assert 1 + 1 == 2\n")],
        [call("run_tests")],
        FINISH,
    )
    events = await run_turn(base, llm)
    run_tests = [t for t in of(events, "tool") if t["name"] == "run_tests"][0]
    assert run_tests["ok"] and "1 passed" in run_tests["output"]


async def test_command_and_mentioned_file_go_into_the_brief(base):
    """`/test` thêm chỉ dẫn của lệnh, `@calc.py` đính kèm nội dung file vào brief; ghi đè file đã đính kèm không cần read_file."""
    (base / "ses_test").mkdir()
    (base / "ses_test" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    llm = ScriptedLLM([call("write_file", path="calc.py", content="def add(a: int, b: int) -> int:\n    return a + b\n")], FINISH)
    events = await run_turn(base, llm, prompt="/test thêm case số âm cho @calc.py")
    brief = llm.seen[0][1]["content"]
    assert "## Lệnh /test" in brief and "def add(a, b)" in brief
    assert of(events, "tool")[0]["ok"]


async def test_truncated_python_write_is_rejected_and_provider_demoted(base):
    """Nội dung .py bị cắt cụt (ngoặc chưa đóng) → không ghi file, báo gửi lại, hạ ưu tiên nhà cung cấp vừa trả lời."""

    class DemotingLLM(ScriptedLLM):
        """ScriptedLLM có `demote` và báo tên nhà cung cấp."""

        demoted: list[str] = []

        async def chat(self, messages, tools=None, reasoning=None):
            """Như ScriptedLLM, kèm tên nhà cung cấp."""
            res = await super().chat(messages, tools, reasoning)
            res["info"]["llm_provider"] = "P1"
            return res

        def demote(self, name, reason):
            """Ghi lại nhà cung cấp bị hạ."""
            self.demoted.append(name)

    llm = DemotingLLM([call("write_file", path="t.py", content="X = [\n    1,\n    2,  # cắt giữa chừng (")], FINISH)
    events = await run_turn(base, llm)
    tool = of(events, "tool")[0]
    assert not tool["ok"] and "CẮT CỤT" in tool["output"]
    assert not (base / "ses_test" / "t.py").exists()
    assert llm.demoted == ["P1"]


async def test_write_ending_with_markdown_fence_is_rejected(base):
    """Model dừng giữa hàm rồi đóng bằng rào ``` (lỗi ở dòng cuối) → coi là cắt cụt, không ghi file."""
    content = '"""t."""\n\n\ndef test_a() -> None:\n    """a."""\n    assert 1\n\n\ndef test_b() -> None:\n```'
    events = await run_turn(base, ScriptedLLM([call("write_file", path="t.py", content=content)], FINISH))
    tool = of(events, "tool")[0]
    assert not tool["ok"] and "CẮT CỤT" in tool["output"]
    assert not (base / "ses_test" / "t.py").exists()


def test_retry_after_read_from_groq_body():
    """429 không có header Retry-After → đọc "try again in ..." trong body (Groq giới hạn token/phút)."""
    import httpx

    from app.integrations.llm import LLMClient

    def wait(body: str, headers: dict[str, str] | None = None) -> float | None:
        """Số giây chờ đọc được từ 1 phản hồi 429."""
        return LLMClient._retry_after(httpx.Response(429, text=body, headers=headers or {}))

    assert wait('{"error":{"message":"Rate limit reached ... Please try again in 6.42s. Need more tokens"}}') == 6.42
    assert wait("try again in 450ms") == 0.45
    assert wait("try again in 2m3s") == 123
    assert wait("x", {"Retry-After": "3"}) == 3
    assert wait("không có gợi ý") is None


@pytest.mark.parametrize(
    ("content", "expect"),
    [
        ("def f(:\n    return 1\n\n\nX = 2\n", "invalid-syntax"),  # lỗi cú pháp giữa file (không phải cắt cụt)
        ("def f() -> int:\n    return missing_name\n", "F821"),  # tên chưa định nghĩa
    ],
)
async def test_write_with_ruff_errors_is_rejected(base, content, expect):
    """Code .py ruff báo lỗi → KHÔNG ghi file, báo lỗi kèm vị trí để model sửa."""
    events = await run_turn(base, ScriptedLLM([call("write_file", path="m.py", content=content)], FINISH))
    tool = of(events, "tool")[0]
    assert not tool["ok"] and "ruff" in tool["output"] and expect in tool["output"]
    assert not (base / "ses_test" / "m.py").exists()


async def test_edit_that_breaks_python_is_rejected(base):
    """edit_file làm file .py lỗi cú pháp → không ghi, file giữ nguyên bản trước."""
    good = "def f() -> int:\n    return 1\n"
    llm = ScriptedLLM(
        [call("write_file", path="m.py", content=good)],
        [call("read_file", path="m.py")],
        [call("edit_file", path="m.py", old_text="    return 1", new_text="    return (1")],
        FINISH,
    )
    events = await run_turn(base, llm)
    edit = [t for t in of(events, "tool") if t["name"] == "edit_file"][0]
    assert not edit["ok"] and "ruff" in edit["output"]
    assert (base / "ses_test" / "m.py").read_text() == good


def test_sandbox_runs_as_backend_user(tmp_path):
    """`user = "auto"` + backend không phải root → sandbox chạy đúng uid:gid của backend (Linux: ghi được vào workspace)."""
    import os

    from app.integrations.sandbox import _sandbox_user

    if not hasattr(os, "getuid") or os.getuid() == 0:
        pytest.skip("chỉ kiểm khi backend chạy bằng user thường")
    assert _sandbox_user(tmp_path) == f"{os.getuid()}:{os.getgid()}"
