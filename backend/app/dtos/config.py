"""Model cấu hình: hạ tầng (config.toml + .env) và LLM / tool / agent (tool_config.toml). Nạp ở `app/config.py`."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ToolName = Literal["sandbox", "git", "workspace", "formatter"]
TOOL_NAMES: tuple[str, ...] = ("sandbox", "git", "workspace", "formatter")

# Nhóm công cụ agent được cấp (khai báo ở `[agent] tools`) → tool hạ tầng (`[tools.*]`) đứng sau.
# `finish` (kết thúc lượt) luôn có; `ask_user` có khi `[agent] ask_human = true`.
TOOL_GROUPS: dict[str, str | None] = {
    "workspace_read": "workspace",  # list_files, read_file, search_text
    "workspace_write": "workspace",  # write_file, edit_file, delete_file
    "test": "sandbox",  # run_tests
    "shell": "sandbox",  # run_command (python trong sandbox)
    "git_read": "git",  # git_status, git_diff
    "git_write": "git",  # git_commit
    "format": "formatter",  # format_files (ruff)
    "install": "sandbox",  # install_packages (pip, có mạng)
    "inspect": None,  # system_info, find_files, check_syntax, git_log, read_log, pip_list
}


def _check_reasoning(v: str) -> str:
    """Chuẩn hóa reasoning effort.

    Args:
        v: Giá trị thô.

    Returns:
        "" hoặc low/medium/high.

    Raises:
        ValueError: Giá trị khác.
    """
    v = (v or "").strip().lower()
    if v not in ("", "low", "medium", "high"):
        raise ValueError(f"reasoning phải là '', low, medium hoặc high — nhận '{v}'")
    return v


# ============================================================
# Tools
# ============================================================
class _ToolBase(BaseModel):
    """Thông số chung của mọi tool hạ tầng.

    Attributes:
        enabled: Tắt thì nhóm công cụ dựa vào tool này không dùng được.
        description: Mô tả (chỉ để đọc).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    description: str = ""


class LLMProviderConfig(BaseModel):
    """1 nhà cung cấp LLM (API tương thích OpenAI chat/completions) — `[[llm.providers]]`."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=40)
    enabled: bool = True
    url: str
    model: str
    api_key_env: str = Field(..., min_length=1, description="Tên biến môi trường chứa API key (.env)")
    # Cách gửi reasoning effort: openrouter → {"reasoning": {"effort"}}, reasoning_effort → trường
    # `reasoning_effort` (Gemini/OpenAI), none → không gửi.
    reasoning_style: Literal["openrouter", "reasoning_effort", "none"] = "openrouter"


class LLMConfig(BaseModel):
    """Mô hình ngôn ngữ dùng chung cho mọi agent (section `[llm]`).

    Nhiều nhà cung cấp theo thứ tự ưu tiên, gọi theo vòng tròn: nhà nào lỗi thì chuyển ngay sang nhà kế tiếp,
    hết vòng quay lại từ đầu (tối đa `failover_rounds` vòng).
    """

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    providers: list[LLMProviderConfig] = Field(..., min_length=1)
    # Số lần thử khi CHỈ có 1 nhà cung cấp. Nhiều nhà: mỗi nhà 1 lần/vòng, lỗi là chuyển nhà ngay.
    attempts_per_provider: int = Field(default=2, ge=1, le=10)
    # Nhà cung cấp vừa lỗi hạ tầng bị đẩy xuống cuối hàng trong khoảng này (0 = tắt).
    provider_cooldown_sec: int = Field(default=300, ge=0, le=86400)
    # Số vòng gọi lần lượt qua mọi nhà cung cấp trước khi báo lỗi.
    failover_rounds: int = Field(default=2, ge=1, le=10)
    # Vòng sau: nhà vừa bị 429 còn hồi ≤ ngưỡng này thì chờ nốt rồi gọi; lâu hơn thì gọi luôn (có thể lại 429 → chuyển tiếp).
    rate_limit_max_wait_sec: int = Field(default=10, ge=0, le=300)
    # DEV ONLY (đặt "off" khi dùng thật): cache = lưu/phát lại câu trả lời LLM theo nội dung hội thoại (chờ dev_cache_delay_sec
    # rồi mới trả); demo = LLM giả theo kịch bản (0 token). Xem docs/ARCHITECTURE.md.
    dev_mode: Literal["off", "cache", "demo"] = "off"
    dev_cache_delay_sec: float = Field(default=3.0, ge=0, le=60)
    dev_demo_delay_sec: float = Field(default=1.5, ge=0, le=60)
    reasoning_by_model: dict[str, str] = Field(default_factory=dict)

    @field_validator("reasoning_by_model")
    @classmethod
    def _validate_map(cls, v: dict[str, str]) -> dict[str, str]:
        """Chuẩn hóa bảng reasoning theo model."""
        return {k.lower(): _check_reasoning(val) for k, val in v.items()}

    @model_validator(mode="after")
    def _validate_providers(self) -> LLMConfig:
        """Tên nhà cung cấp không trùng và có ít nhất 1 nhà đang bật."""
        names = [p.name for p in self.providers]
        if len(set(names)) != len(names):
            raise ValueError(f"llm.providers có tên trùng: {names}")
        if not any(p.enabled for p in self.providers):
            raise ValueError("llm.providers: cần ít nhất 1 nhà cung cấp enabled = true")
        return self

    @property
    def enabled_providers(self) -> list[LLMProviderConfig]:
        """Nhà cung cấp đang bật, theo thứ tự ưu tiên."""
        return [p for p in self.providers if p.enabled]

    @property
    def model(self) -> str:
        """Model của nhà cung cấp ưu tiên số 1 (dùng cho log)."""
        return self.enabled_providers[0].model


class SandboxToolConfig(_ToolBase):
    """Tool `sandbox` — Docker."""

    image: str = "python:3.12-slim"
    timeout_sec: int = Field(default=30, ge=5, le=600)
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    pids_limit: int = Field(default=128, ge=16, le=4096)
    network: str = "none"
    install_network: str = "bridge"  # mạng riêng cho lệnh cài thư viện (install_packages)
    install_timeout_sec: int = Field(default=180, ge=30, le=1800)
    user: str = "auto"
    max_output_bytes: int = Field(default=256 * 1024, ge=4096)


class GitToolConfig(_ToolBase):
    """Tool `git`."""

    user_name: str = "AI Coding Agent"
    user_email: str = "agent@vibecode.local"
    commit_subject_max_len: int = Field(default=72, ge=20, le=200)
    gitignore: str = ""


class WorkspaceToolConfig(_ToolBase):
    """Tool `workspace`."""

    max_file_view_bytes: int = Field(default=200 * 1024, ge=1024)
    max_diff_chars: int = Field(default=300_000, ge=1000)


class FormatterToolConfig(_ToolBase):
    """Tool `formatter` — ruff auto-format code agent vừa ghi."""

    line_length: int = Field(default=100, ge=40, le=200)
    sort_imports: bool = True
    timeout_sec: int = Field(default=30, ge=5, le=300)
    executable: str = ""


class ToolsConfig(BaseModel):
    """Toàn bộ tool."""

    model_config = ConfigDict(extra="forbid")

    sandbox: SandboxToolConfig = Field(default_factory=SandboxToolConfig)
    git: GitToolConfig = Field(default_factory=GitToolConfig)
    workspace: WorkspaceToolConfig = Field(default_factory=WorkspaceToolConfig)
    formatter: FormatterToolConfig = Field(default_factory=FormatterToolConfig)

    def get(self, name: str) -> _ToolBase:
        """Lấy config tool theo tên.

        Args:
            name: Tên tool.

        Returns:
            Config của tool.
        """
        return getattr(self, name)


# ============================================================
# Agent: [agent]
# ============================================================
class AgentConfig(BaseModel):
    """Thông số vận hành của agent (section `[agent]`). Vòng lặp: app/agents/agent.py."""

    model_config = ConfigDict(extra="forbid")

    tools: list[str] = Field(default_factory=lambda: list(TOOL_GROUPS), description=f"Nhóm công cụ: {sorted(TOOL_GROUPS)}")
    history_turns: int = Field(default=5, ge=0, le=50)  # số lượt chat trước đưa vào brief
    max_steps: int = Field(default=30, ge=2, le=500)  # số bước (lời gọi LLM) tối đa của agent mỗi lượt
    max_wall_sec: int = Field(default=1800, ge=30, le=14400)  # thời gian tối đa cả lượt
    max_fail_streak: int = Field(default=3, ge=1, le=20)  # số lệnh công cụ lỗi liên tiếp → dừng, báo lỗi gốc
    keep_full_results: int = Field(default=2, ge=1, le=50)  # số lượt gần nhất giữ nguyên kết quả công cụ
    request_max_chars: int = Field(default=20000, ge=4000)  # tổng ký tự (tin nhắn + schema công cụ) tối đa mỗi lời gọi LLM; vượt → rút kết quả cũ
    old_result_chars: int = Field(default=400, ge=50, le=10000)  # kết quả công cụ cũ hơn rút còn ngần này ký tự
    tool_result_max_chars: int = Field(default=3500, ge=200)
    reasoning: str = ""
    ask_human: bool = True  # false → không có công cụ ask_user
    human_input_timeout_sec: int = Field(default=600, ge=30, le=3600)
    auto_commit: bool = True
    commit_message_template: str = "feat: vibe solution for '{prompt}'"
    commit_prompt_max_len: int = Field(default=60, ge=10, le=200)

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, v: list[str]) -> list[str]:
        """Mọi nhóm công cụ phải tồn tại; bỏ trùng."""
        unknown = [t for t in v if t not in TOOL_GROUPS]
        if unknown:
            raise ValueError(f"nhóm công cụ không tồn tại: {unknown}. Hợp lệ: {sorted(TOOL_GROUPS)}")
        return list(dict.fromkeys(v))

    @field_validator("reasoning")
    @classmethod
    def _validate_reasoning(cls, v: str) -> str:
        """Chuẩn hóa reasoning effort."""
        return _check_reasoning(v)

    @field_validator("commit_message_template")
    @classmethod
    def _validate_template(cls, v: str) -> str:
        """Mẫu commit message phải chứa `{prompt}`."""
        if "{prompt}" not in v:
            raise ValueError("commit_message_template phải chứa '{prompt}'")
        return v

    @property
    def reasoning_or_none(self) -> str | None:
        """Reasoning dạng truyền cho LLM (None = theo mặc định model)."""
        return self.reasoning or None


class ToolConfig(BaseModel):
    """Root của tool_config.toml."""

    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig
    tools: ToolsConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)

    @model_validator(mode="after")
    def _validate_agent(self) -> ToolConfig:
        """Mọi nhóm công cụ được cấp phải có tool hạ tầng đang bật."""
        for group in self.agent.tools:
            infra = TOOL_GROUPS[group]
            if infra and not self.tools.get(infra).enabled:
                raise ValueError(f"agent dùng nhóm '{group}' cần tool '{infra}' nhưng tool đang tắt (enabled = false)")
        return self


# ============================================================
# Hạ tầng: config.toml + .env
# ============================================================
def _require_env(key: str) -> str:
    """Đọc biến môi trường bắt buộc, fail-fast nếu thiếu.

    Args:
        key: Tên biến môi trường.

    Returns:
        Giá trị của biến môi trường (đã strip).

    Raises:
        RuntimeError: Khi biến môi trường không tồn tại hoặc rỗng.
    """
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(f"Thiếu biến môi trường bắt buộc '{key}'. Kiểm tra file .env hoặc export thủ công.")
    return value


def _validate_url(v: str) -> str:
    """Chuẩn hóa và kiểm tra URL hợp lệ.

    Args:
        v: URL thô.

    Returns:
        URL đã strip trailing slash.

    Raises:
        ValueError: Khi URL không bắt đầu bằng http(s).
    """
    if not v.startswith(("https://", "http://")):
        raise ValueError("URL phải bắt đầu bằng http:// hoặc https://")
    return v.rstrip("/")


# ============================================================
# Schema các section trong config.toml
# ============================================================
class LogConfig(BaseModel):
    """Cấu hình logging."""

    log_path: str = "./logs"
    level: str = "INFO"
    rotation: str = "10 MB"
    retention: str = "7 days"


class ServerConfig(BaseModel):
    """Cấu hình HTTP server."""

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=list)


class DatabaseConfig(BaseModel):
    """Cấu hình Postgres.

    Credentials được nạp động từ biến môi trường, không lưu trong TOML.
    """

    table_name: str = "traces"
    sessions_table: str = "sessions"
    messages_table: str = "session_messages"

    @property
    def supabase_url(self) -> str:
        """URL Supabase (đọc từ env SUPABASE_URL)."""
        return _validate_url(_require_env("SUPABASE_URL"))

    @property
    def supabase_key(self) -> str:
        """API key Supabase (đọc từ env SUPABASE_KEY)."""
        return _require_env("SUPABASE_KEY")


class StorageConfig(BaseModel):
    """Cấu hình Storage.

    Credentials được nạp động từ biến môi trường, không lưu trong TOML.
    """

    bucket: str = "trace-logs"

    @property
    def supabase_url(self) -> str:
        """URL Supabase (đọc từ env SUPABASE_URL)."""
        return _validate_url(_require_env("SUPABASE_URL"))

    @property
    def supabase_key(self) -> str:
        """API key Supabase (đọc từ env SUPABASE_KEY)."""
        return _require_env("SUPABASE_KEY")


class WorkspaceConfig(BaseModel):
    """Nơi chứa workspace của các phiên (mỗi phiên 1 thư mục + 1 git repo)."""

    base: str = "./storage/sandboxes"


class SecretsConfig(BaseModel):
    """Credentials Supabase nạp từ biến môi trường lúc dùng (không có trong TOML).

    Đọc khi backend khởi động (`main.lifespan`) chứ không phải lúc import, để test / script không cần Supabase vẫn chạy được.
    API key LLM do `LLMClient` đọc theo `api_key_env` của từng nhà cung cấp.
    """

    @property
    def supabase_url(self) -> str:
        """URL Supabase (đọc từ env SUPABASE_URL)."""
        return _validate_url(_require_env("SUPABASE_URL"))

    @property
    def supabase_key(self) -> str:
        """API key Supabase (đọc từ env SUPABASE_KEY)."""
        return _require_env("SUPABASE_KEY")


class AppConfig(BaseModel):
    """Root config gộp tất cả section."""

    logs: LogConfig
    server: ServerConfig
    database: DatabaseConfig
    storage: StorageConfig
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)


# ============================================================
# Loader + singleton
# ============================================================


__all__ = ["TOOL_GROUPS", "TOOL_NAMES", "AgentConfig", "AppConfig", "LLMConfig", "ToolConfig", "ToolsConfig"]
