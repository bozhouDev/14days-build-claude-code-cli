from pathlib import Path

from agent_code.agent import run_agent
from agent_code.model import MockProvider, ToolCall
from agent_code.tools import ToolContext, default_tools


def test_echo_agent_loop() -> None:
    result = run_agent("用 echo 工具说 hi", MockProvider(), default_tools())

    assert "tool_call: echo" in result.trace[0]
    assert "observation: hi" in result.trace[1]
    assert result.final == "echo 工具返回：hi"


def test_agent_records_anthropic_tool_messages() -> None:
    result = run_agent("用 echo 工具说 hi", MockProvider(), default_tools())

    assert result.messages[1]["role"] == "assistant"
    assert result.messages[1]["content"][0]["type"] == "tool_use"
    assert result.messages[1]["content"][0]["name"] == "echo"

    assert result.messages[2]["role"] == "user"
    assert result.messages[2]["content"][0]["type"] == "tool_result"
    assert result.messages[2]["content"][0]["tool_use_id"] == "call_echo_1"


def test_read_file_tool() -> None:
    registry = default_tools()
    ctx = ToolContext(cwd=Path(__file__).resolve().parents[1])
    result = registry.run(
        ToolCall(id="call_1", name="read_file", arguments={"path": "pyproject.toml"}),
        ctx,
    )

    assert 'name = "agent-code"' in result.content
