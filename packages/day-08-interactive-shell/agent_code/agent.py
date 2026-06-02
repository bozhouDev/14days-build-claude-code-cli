from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from rich.console import Console

from .model import ModelProvider, ModelResponse, ToolResult
from .tools import ToolContext, ToolRegistry
from .fs_safety import (
    SkipPolicy,
    apply_single_replace,
    check_mtime_conflict,
    ensure_read_before_edit,
    load_gitignore,
    resolve_in_cwd,
)

from .prompt_ui import confirm_command, confirm_edit, confirm_plan, confirm_tool_use, prompt_single_choice, render_diff
from .permissions import PermissionRequest, decide_permission
from .session import Session
from .project_memory import load_agent_md 
from .compact_basic import compact
from .hooks import run_hooks, run_hooks_raw
from .runtime import RuntimeState

console = Console(no_color=True)


@dataclass
class AgentResult:
    final: str
    trace: list[str]
    messages: list[dict[str, Any]]

_SYSTEM_CORE = (
    "You are an AI coding agent running inside a CLI harness. "
    "You have access to tools for reading/writing files, running shell commands, "
    "searching the web, and asking the user questions. "
    "Use tools when needed; respond directly when you can. "
    "When in plan mode, do not write files. Present a clear plan (you may also call "
    "exit_plan_mode); the harness asks the user to approve before writes unlock."
)

def build_system_prompt(cwd: Path) -> str:
    """组装 system prompt：核心指南 + AGENT.md + MEMORY.md 索引。
    注入顺序：core prompt → 项目规则 → 跨 session 记忆索引。"""
    from .memdir.store import load_index as load_memory_index

    parts: list[str] = [_SYSTEM_CORE]

    agent_md = load_agent_md(cwd)
    if agent_md:
        parts.append(agent_md)

    memory_index = load_memory_index(cwd)
    if memory_index:
        parts.append(f"<project-memory>\n{memory_index}\n</project-memory>")

    return "\n\n".join(parts)


def _assistant_message(response: ModelResponse) -> dict[str, Any]:
    if response.assistant_content:
        return {"role": "assistant", "content": response.assistant_content}

    content: list[dict[str, Any]] = []
    if response.text:
        content.append({"type": "text", "text": response.text})
    for call in response.tool_calls or []:
        content.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
        )
    return {"role": "assistant", "content": content}


def _tool_result_message(tool_call_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": content,
                "is_error": is_error,
            }
        ],
    }


def _result_block(result: ToolResult) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": result.tool_call_id,
        "content": result.content,
        "is_error": result.is_error,
    }


def _format_call_args(args: dict[str, Any]) -> str:
    """trace 里的工具参数可能很大（file_write 的内容、exit_plan_mode 的整段计划）。
    长字符串只在 trace 里截断，完整参数仍照常传给工具/审批 UI。"""
    preview: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 80:
            preview[key] = value[:80] + "…"
        else:
            preview[key] = value
    return str(preview)


def execute_one_tool_call(call, ctx, state, tools, emit) -> dict[str, Any]:
    """跑单个工具，返回一个 tool_result block。"""
    emit(f"tool_call: {call.name} {_format_call_args(call.arguments)}")

    request = PermissionRequest(
        tool_name=call.name,
        args=call.arguments,
        mode=state.permission_mode,
        cwd=ctx.cwd,
    )
    decision = decide_permission(request)

    if decision.behavior != "deny":
        pre_hooks = run_hooks("PreToolUse", call.name, call.arguments, ctx.cwd)
        pre_blocked = [h for h in pre_hooks if not h["success"]]
        if pre_blocked:
            blocked_msgs = "\n".join(
                f"  [hook] {h['command']}: {h['output']}" for h in pre_blocked
            )
            observation = f"tool blocked by PreToolUse hook:\n{blocked_msgs}"
            emit(f"observation: {observation}")
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": observation,
                "is_error": True,
            }

    if call.name == "exit_plan_mode":
        plan_summary = call.arguments.get("plan_summary", "")
        if not confirm_plan(plan_summary):       # 借回终端：渲染计划 + 等批准
            obs = "Plan not approved. Revise the plan and call exit_plan_mode again."
            emit(f"observation: {obs}")
            return {"type": "tool_result", "tool_use_id": call.id, "content": obs, "is_error": True}
        state.permission_mode = "acceptEdits"     # 批准后翻到 acceptEdits，写不再逐个确认

    edit_preview: tuple[str, str, str] | None = None
    if call.name in ("file_write", "file_edit") and decision.behavior != "deny":
        path_str = call.arguments.get("file_path", "")
        if not path_str:
            result = ToolResult(call.id, "error: missing required argument 'file_path'", is_error=True)
            emit(f"observation: {result.content}")
            return _result_block(result)

        try:
            path = resolve_in_cwd(ctx.cwd, path_str)
        except (ValueError, OSError) as exc:
            result = ToolResult(call.id, f"error: {exc}", is_error=True)
            emit(f"observation: {result.content}")
            return _result_block(result)

        old_content = path.read_text(encoding="utf-8") if path.exists() else ""
        validation_error: str | None = None
        if call.name == "file_write":
            if path.exists():
                validation_error = (
                    ensure_read_before_edit(ctx.read_state, path)
                    or check_mtime_conflict(ctx.read_state, path)
                )
            new_content = call.arguments.get("content", "")
        else:
            new_content = ""
            if not path.exists():
                validation_error = f"error: file does not exist: {path_str}"
            else:
                validation_error = (
                    ensure_read_before_edit(ctx.read_state, path)
                    or check_mtime_conflict(ctx.read_state, path)
                )
            if validation_error is None:
                new_content, replace_err = apply_single_replace(
                    old_content,
                    call.arguments.get("old_string", ""),
                    call.arguments.get("new_string", ""),
                    bool(call.arguments.get("replace_all", False)),
                )
                if replace_err is not None:
                    validation_error = replace_err

        if validation_error is not None:
            result = ToolResult(call.id, validation_error, is_error=True)
            emit(f"observation: {result.content}")
            return _result_block(result)
        edit_preview = (path_str, old_content, new_content)

    if decision.behavior == "deny":
        result = ToolResult(call.id, f"error: {decision.message}", is_error=True)
        emit(f"observation: {result.content}")
        return _result_block(result)

    if decision.behavior == "ask":
        if call.name in ("file_write", "file_edit"):
            if edit_preview is not None:
                path_str, old_content, new_content = edit_preview
                diff_text = render_diff(old_content, new_content, path_str)
                console.print(f"\n[bold]Diff for {path_str}:[/bold]")
                console.print(diff_text)
                if not confirm_edit(path_str):
                    result = ToolResult(call.id, "error: edit rejected by user", is_error=True)
                    emit(f"observation: {result.content}")
                    return _result_block(result)

        elif call.name == "bash":
            command = call.arguments.get("command", "")
            timeout = call.arguments.get("timeout", 30)
            console.print(f"\nCommand: {command}", markup=False, highlight=False)
            console.print(f"timeout: {timeout}s  cwd: {ctx.cwd}", markup=False, highlight=False)
            if not confirm_command(command):
                result = ToolResult(call.id, "error: command rejected by user", is_error=True)
                emit(f"observation: {result.content}")
                return _result_block(result)

        elif call.name in ("web_fetch", "web_search"):
            detail = call.arguments.get("url") or call.arguments.get("query") or str(call.arguments)
            if not confirm_tool_use(call.name, detail):
                result = ToolResult(call.id, "error: tool use rejected by user", is_error=True)
                emit(f"observation: {result.content}")
                return _result_block(result)

        elif call.name == "ask_user_question":
            question = call.arguments.get("prompt", "")
            options = call.arguments.get("options", [])
            labels = [str(o) for o in options] if isinstance(options, list) else []
            selected = prompt_single_choice(question, labels)
            if selected is None:
                result = ToolResult(call.id, "User skipped the question.", is_error=False)
            else:
                result = ToolResult(call.id, f'User selected: "{selected}"', is_error=False)
            emit(f"observation: {result.content}")
            return _result_block(result)

    result = tools.run(call, ctx)
    emit(f"observation: {result.content}")
    if not result.is_error:
        post_hooks = run_hooks(
            "PostToolUse", call.name, call.arguments, ctx.cwd,
            tool_result=result.content,
        )
        for h in post_hooks:
            status = "ok" if h["success"] else f"warning: {h['output']}"
            console.print(f"[dim]hook: PostToolUse {call.name} {status}[/dim]")
    return _result_block(result)


def partition_tool_calls(calls, tools) -> list[list]:
    """连续只读工具合成并行组；写工具截断、自成串行组。"""
    batches: list[list] = []
    current: list = []
    for call in calls:
        tool = tools.get(call.name)
        if tool is not None and tool.is_read_only:
            current.append(call)
        else:
            if current:
                batches.append(current)
                current = []
            batches.append([call])
    if current:
        batches.append(current)
    return batches


def execute_plan_boundary_calls(calls, ctx, state, tools, emit) -> list[dict[str, Any]] | None:
    """plan 模式下，exit_plan_mode 是 turn boundary：同轮其它工具不执行。"""
    if state.permission_mode != "plan":
        return None
    exit_call = next((call for call in calls if call.name == "exit_plan_mode"), None)
    if exit_call is None:
        return None

    blocks: list[dict[str, Any]] = []
    for call in calls:
        if call is exit_call:
            blocks.append(execute_one_tool_call(call, ctx, state, tools, emit))
            continue
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": "Skipped because exit_plan_mode is waiting for approval. Re-issue this tool after approval if needed.",
                "is_error": True,
            }
        )
    return blocks


def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    state: RuntimeState | None = None,
    session: Session | None = None,
    system_prompt: str | None = None,
) -> AgentResult:
    resolved_cwd = cwd or Path.cwd()
    state = state or RuntimeState()
    ctx = ToolContext(
        cwd=resolved_cwd,
        skip_policy=SkipPolicy.default(gitignore=load_gitignore(resolved_cwd)),
        runtime_state=state,
    )
    def emit(line: str) -> None:
        # 工具结果可能很长：完整内容只通过 tool_result 回填给模型，终端只看工具调用/最终回答。
        if line.startswith("observation:"):
            return
        # 流式输出 trace：append 给测试用，print 给读者看
        trace.append(line)
        console.print(line, markup=False, highlight=False)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
     # Day 6：如果有 session 且已有历史，从历史恢复；否则从当前 prompt 冷启动
    if session and session.history:
        messages = list(session.history)
        messages.append({"role": "user", "content": prompt})
    else:
        messages = [{"role": "user", "content": prompt}]

    # Day 6：刚加进 messages 的这条 user prompt 也要落盘，
    # 否则 --resume 时 session.history 里只有 assistant 没有起点 user
    if session:
        session.append_messages([messages[-1]])
    trace: list[str] = []
    continuation_count = 0    # Day 8 v3：Stop hook 续跑次数，封顶防死循环
    for step in range(max_steps):
        if len(messages) > 40:
            messages = compact(messages, keep=8)
            console.print(f"[dim]compacted: {len(messages)} messages remaining[/dim]")
        response = provider.complete(messages, tools=tools.list(), system=system_prompt)
        messages.append(_assistant_message(response))

        # Day 8 v3：ESC 半步中断——在执行工具前检查 abort
        if state.abort_event.is_set():
            emit("interrupted by user")
            if response.tool_calls:
                # 配对不变量：模型给了 tool_use，就必须有对应 tool_result，否则下次请求被 API 拒
                blocks = [
                    {"type": "tool_result", "tool_use_id": c.id,
                     "content": "Interrupted by user", "is_error": True}
                    for c in response.tool_calls
                ]
                messages.append({"role": "user", "content": blocks})
                if session:
                    session.append_messages(messages[-2:])
            elif session:
                session.append_messages([messages[-1]])
            return AgentResult(final="interrupted", trace=trace, messages=messages)

        if not response.tool_calls:
            final = response.text or ""
            # Day 8 v6：plan 模式下，模型这一轮没再调工具就说明计划写完了。
            # turn 边界就是审批检查点——不管模型是调 exit_plan_mode 还是直接把计划当 final 交出来，
            # 都走同一个 confirm_plan，批准后才解锁写。
            if state.permission_mode == "plan" and final.strip():
                if confirm_plan(final):
                    state.permission_mode = "acceptEdits"
                    messages.append({"role": "user", "content": "Plan approved. Implement it now."})
                else:
                    messages.append({"role": "user", "content": "Plan not approved. Revise the plan and present it again."})
                if session:
                    session.append_messages(messages[-2:])
                continue
            # Day 8 v3：Stop hook——模型自认答完，给 hook 一次"再推一轮"的机会
            forced: str | None = None
            if continuation_count < 2:        # 最多续跑 2 次
                payload = {"event": "Stop", "final_text": final,
                           "cwd": str(ctx.cwd), "continuation_count": continuation_count}
                for h in run_hooks_raw("Stop", payload, ctx.cwd):
                    if not h["success"] and h["output"].strip():
                        forced = h["output"].strip()
                        break
            if forced is not None:
                continuation_count += 1
                emit(f"continue: {forced}")
                messages.append({"role": "user", "content": f"continue: {forced}"})
                if session:
                    session.append_messages(messages[-2:])
                continue                      # 回到 loop 顶，再跑一轮
            emit(f"final: {final}")
            if session:
                session.append_messages([messages[-1]])
            return AgentResult(final=final, trace=trace, messages=messages)

        tool_result_blocks = execute_plan_boundary_calls(response.tool_calls, ctx, state, tools, emit)
        if tool_result_blocks is None:
            tool_result_blocks = []
            for batch in partition_tool_calls(response.tool_calls, tools):
                if len(batch) == 1:
                    tool_result_blocks.append(execute_one_tool_call(batch[0], ctx, state, tools, emit))
                else:
                    # 只读组并行。ex.map 按输入顺序返回结果，保证 tool_result 顺序对齐 tool_use。
                    with ThreadPoolExecutor(max_workers=4) as ex:
                        results = list(
                            ex.map(
                                lambda c: execute_one_tool_call(c, ctx, state, tools, emit),
                                batch,
                            )
                        )
                    tool_result_blocks.extend(results)

        messages.append({"role": "user", "content": tool_result_blocks})
        if session:
            session.append_messages(messages[-2:])

    final = f"reached max_steps={max_steps}"
    emit(f"final: {final}")
    return AgentResult(final=final, trace=trace, messages=messages)
