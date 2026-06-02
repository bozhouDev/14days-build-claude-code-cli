from pathlib import Path

from agent_code.agent import run_agent
from agent_code.model import MockProvider, ModelResponse, ToolCall
from agent_code.runtime import RuntimeState
from agent_code.tools import ToolContext, default_tools


class MalformedWriteProvider:
    def __init__(self, arguments: dict[str, object]) -> None:
        self.arguments = arguments
        self.calls = 0

    def complete(self, messages, tools=None, system=None) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call_bad_write",
                        name="file_write",
                        arguments=self.arguments,
                    )
                ],
                stop_reason="tool_use",
            )
        return ModelResponse(text="done")


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


def test_file_write_missing_path_returns_tool_error() -> None:
    result = run_agent(
        "写文件",
        MalformedWriteProvider({}),
        default_tools(),
        cwd=Path(__file__).resolve().parents[1],
        state=RuntimeState(permission_mode="acceptEdits"),
    )

    assert "tool_call: file_write {}" in result.trace[0]
    assert "observation: error: missing required argument 'file_path'" in result.trace[1]
    assert result.final == "done"
