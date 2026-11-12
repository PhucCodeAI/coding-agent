import pytest
from pydantic import ValidationError

from app.config import tool_configs
from app.dtos.config import AgentConfig
from app.workflow.tools import TOOLS


def test_tool_config_loads_with_valid_agent_tools():
    """tool_config.toml nạp được và mọi nhóm công cụ của agent đều có công cụ."""
    groups = {t.group for t in TOOLS.values()}
    assert set(tool_configs.agent.tools) <= groups
    assert {"finish", "ask_user"} <= groups


def test_unknown_tool_group_is_rejected():
    """Nhóm công cụ lạ trong [agent] tools → lỗi ngay khi nạp."""
    with pytest.raises(ValidationError):
        AgentConfig(tools=["teleport"])
