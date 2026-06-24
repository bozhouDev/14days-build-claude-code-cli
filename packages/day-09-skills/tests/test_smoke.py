import json
from pathlib import Path

import agent_code.agent as agent_module
import agent_code.prompt_ui as prompt_ui
from agent_code.agent import (
    _format_call_args,
    execute_one_tool_call,
    execute_plan_boundary_calls,
    partition_tool_calls,
    run_agent,
)
from agent_code.interactive import bottom_toolbar
from agent_code.model import MockProvider, ModelResponse, ToolCall
from agent_code.permissions import PermissionRequest, decide_permission
from agent_code.runtime import RuntimeState
from agent_code.slash import SlashContext, dispatch_slash
from agent_code.tools import ToolContext, default_tools


def test_echo_agent_loop() -> None:
    result = run_agent("用 echo 工具说 hi", MockProvider(), default_tools())

    assert "tool_call: echo" in result.trace[0]
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


class AbortProvider:
    def complete(self, messages, tools=None, system=None):
        return ModelResponse(
            tool_calls=[
                ToolCall(id="call_echo_abort", name="echo", arguments={"text": "hi"}),
            ],
            stop_reason="tool_use",
        )


def test_abort_pairs_pending_tool_results() -> None:
    state = RuntimeState()
    state.abort_event.set()

    result = run_agent("will abort", AbortProvider(), default_tools(), state=state)

    assert result.final == "interrupted"
    assert "interrupted by user" in result.trace
    assert result.messages[-1]["content"][0]["tool_use_id"] == "call_echo_abort"
    assert result.messages[-1]["content"][0]["is_error"] is True


class FinalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools=None, system=None):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(text="done")
        return ModelResponse(text="done with test")


def test_stop_hook_can_force_one_more_turn(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks.json"
    command = (
        "python3 -c \"import json,sys; d=json.load(sys.stdin); "
        "ok='test' in d.get('final_text',''); "
        "sys.stderr.write('' if ok else 'add a unit test'); "
        "sys.exit(0 if ok else 1)\""
    )
    hooks.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "*", "run": command}]}}), encoding="utf-8")
    provider = FinalProvider()

    result = run_agent("write code", provider, default_tools(), cwd=tmp_path)

    assert provider.calls == 2
    assert "continue: add a unit test" in result.trace
    assert result.final == "done with test"


def test_partition_tool_calls_groups_contiguous_read_only_tools() -> None:
    registry = default_tools()
    calls = [
        ToolCall(id="read_1", name="read_file", arguments={"path": "pyproject.toml"}),
        ToolCall(id="read_2", name="grep", arguments={"pattern": "agent-code"}),
        ToolCall(id="write_1", name="file_write", arguments={"file_path": "x.txt", "content": "x"}),
        ToolCall(id="read_3", name="git_status", arguments={}),
    ]

    batches = partition_tool_calls(calls, registry)

    assert [[c.id for c in batch] for batch in batches] == [
        ["read_1", "read_2"],
        ["write_1"],
        ["read_3"],
    ]


class MultiEchoProvider:
    def complete(self, messages, tools=None, system=None):
        last = messages[-1]
        if isinstance(last["content"], list):
            content = ",".join(block["content"] for block in last["content"])
            return ModelResponse(text=content)
        return ModelResponse(
            tool_calls=[
                ToolCall(id="call_echo_a", name="echo", arguments={"text": "a"}),
                ToolCall(id="call_echo_b", name="echo", arguments={"text": "b"}),
            ],
            stop_reason="tool_use",
        )


def test_parallel_read_only_results_keep_tool_use_order() -> None:
    result = run_agent("echo twice", MultiEchoProvider(), default_tools())

    tool_results = result.messages[2]["content"]
    assert [block["tool_use_id"] for block in tool_results] == ["call_echo_a", "call_echo_b"]
    assert result.final == "a,b"


def test_todo_write_updates_runtime_state_and_toolbar() -> None:
    registry = default_tools()
    state = RuntimeState()
    ctx = ToolContext(cwd=Path.cwd(), runtime_state=state)

    result = registry.run(
        ToolCall(
            id="todo_1",
            name="todo_write",
            arguments={
                "todos": [
                    {"content": "读 cli.py", "status": "completed", "activeForm": "正在读 cli.py"},
                    {"content": "跑测试", "status": "in_progress", "activeForm": "正在跑测试"},
                ]
            },
        ),
        ctx,
    )

    assert "Todos updated." in result.content
    assert [item.content for item in state.todo_store] == ["读 cli.py", "跑测试"]
    assert "正在跑测试" in bottom_toolbar(state)


def test_todo_tools_are_allowed_without_confirmation() -> None:
    cwd = Path.cwd()

    assert decide_permission(PermissionRequest("todo_read", {}, "default", cwd)).behavior == "allow"
    assert decide_permission(PermissionRequest("todo_write", {"todos": []}, "default", cwd)).behavior == "allow"


def test_plan_mode_allows_plan_tools_and_denies_writes() -> None:
    cwd = Path.cwd()

    assert decide_permission(PermissionRequest("todo_write", {"todos": []}, "plan", cwd)).behavior == "allow"
    assert decide_permission(PermissionRequest("enter_plan_mode", {}, "plan", cwd)).behavior == "allow"
    assert decide_permission(PermissionRequest("exit_plan_mode", {"plan_summary": "ok"}, "plan", cwd)).behavior == "allow"
    denied = decide_permission(PermissionRequest("file_write", {"file_path": "x.py"}, "plan", cwd))
    assert denied.behavior == "deny"
    assert "writes unlock after you approve" in (denied.message or "")


def test_plan_slash_toggles_runtime_state() -> None:
    state = RuntimeState()
    ctx = SlashContext(
        cwd=Path.cwd(),
        permission_mode=state.permission_mode,
        model=state.model,
        provider=state.provider,
        session_id=None,
        state=state,
    )

    result = dispatch_slash("/plan", ctx)
    assert result.message.startswith("entered plan mode")
    assert state.permission_mode == "plan"

    result = dispatch_slash("/plan off", ctx)
    assert result.message == "exited plan mode"
    assert state.permission_mode == "default"


def test_exit_plan_mode_approval_unlocks_accept_edits(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "confirm_plan", lambda _summary: True)
    state = RuntimeState(permission_mode="plan")
    ctx = ToolContext(cwd=Path.cwd(), runtime_state=state)
    result = execute_one_tool_call(
        ToolCall(id="plan_1", name="exit_plan_mode", arguments={"plan_summary": "1. test"}),
        ctx,
        state,
        default_tools(),
        lambda _line: None,
    )

    assert state.permission_mode == "acceptEdits"
    assert result["is_error"] is False
    assert result["content"] == "Plan approved. Write tools are now enabled."


def test_exit_plan_mode_rejection_stays_in_plan(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "confirm_plan", lambda _summary: False)
    state = RuntimeState(permission_mode="plan")
    ctx = ToolContext(cwd=Path.cwd(), runtime_state=state)
    result = execute_one_tool_call(
        ToolCall(id="plan_1", name="exit_plan_mode", arguments={"plan_summary": "1. test"}),
        ctx,
        state,
        default_tools(),
        lambda _line: None,
    )

    assert state.permission_mode == "plan"
    assert result["is_error"] is True
    assert "Plan not approved" in result["content"]


def test_confirm_plan_renders_panel_before_prompt(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(prompt_ui, "_terminal_asker", None)
    monkeypatch.setattr(
        prompt_ui.typer,
        "echo",
        lambda text, nl=True: events.append(("echo", text)),
    )

    def fake_confirm(prompt: str, default: bool = False) -> bool:
        events.append(("confirm", prompt))
        return True

    monkeypatch.setattr(prompt_ui.typer, "confirm", fake_confirm)

    assert prompt_ui.confirm_plan("1. Create file") is True
    assert events[0][0] == "echo"
    assert "Plan" in events[0][1]
    assert "1. Create file" in events[0][1]
    assert events[1] == ("confirm", "Approve this plan and exit plan mode?")


def test_confirm_plan_interactive_writes_panel_to_real_terminal(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(prompt_ui, "_terminal_asker", lambda func: func())
    monkeypatch.setattr(
        prompt_ui,
        "_write_real_terminal",
        lambda text: events.append(("panel", text)),
    )

    def fake_confirm(prompt: str, default: bool = False) -> bool:
        events.append(("confirm", prompt))
        return True

    monkeypatch.setattr(prompt_ui.typer, "confirm", fake_confirm)

    assert prompt_ui.confirm_plan("1. Create file") is True
    assert events[0][0] == "panel"
    assert "Plan" in events[0][1]
    assert "1. Create file" in events[0][1]
    assert events[1] == ("confirm", "Approve this plan and exit plan mode?")


class PlanFinalProvider:
    """模拟 DeepSeek 行为：进 plan 后把计划当 final 文本交出来，不调用 exit_plan_mode。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools=None, system=None):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(text="1. Create file\n2. Run tests")
        return ModelResponse(text="implemented")


def test_plan_mode_turn_end_text_goes_through_approval(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "confirm_plan", lambda summary: "Create file" in summary)
    state = RuntimeState(permission_mode="plan")
    provider = PlanFinalProvider()

    result = run_agent("write code after approval", provider, default_tools(), state=state)

    assert provider.calls == 2
    assert state.permission_mode == "acceptEdits"
    assert result.final == "implemented"


def test_exit_plan_mode_is_turn_boundary_and_skips_same_turn_writes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_module, "confirm_plan", lambda _summary: True)
    state = RuntimeState(permission_mode="plan")
    ctx = ToolContext(cwd=tmp_path, runtime_state=state)
    calls = [
        ToolCall(id="write_1", name="file_write", arguments={"file_path": "poems.txt", "content": "draft"}),
        ToolCall(id="plan_1", name="exit_plan_mode", arguments={"plan_summary": "write poems"}),
    ]

    blocks = execute_plan_boundary_calls(calls, ctx, state, default_tools(), lambda _line: None)

    assert blocks is not None
    assert state.permission_mode == "acceptEdits"
    assert not (tmp_path / "poems.txt").exists()
    assert blocks[0]["tool_use_id"] == "write_1"
    assert blocks[0]["is_error"] is True
    assert "Skipped because exit_plan_mode" in blocks[0]["content"]
    assert blocks[1]["tool_use_id"] == "plan_1"
    assert blocks[1]["is_error"] is False


def test_format_call_args_trims_long_values_for_trace() -> None:
    short = _format_call_args({"path": "agent_code/cli.py"})
    assert short == "{'path': 'agent_code/cli.py'}"

    long_plan = "x" * 200
    trimmed = _format_call_args({"plan_summary": long_plan})
    assert long_plan not in trimmed
    assert "…" in trimmed
    assert len("x" * 80) <= len(trimmed)

