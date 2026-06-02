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

from .prompt_ui import confirm_command, confirm_edit, confirm_tool_use, prompt_single_choice, render_diff, confirm_plan
from .permissions import PermissionRequest, decide_permission
from .session import Session
from .project_memory import load_agent_md 
from .compact_basic import compact
from .hooks import run_hooks
from .runtime import RuntimeState

console = Console()


@dataclass
class AgentResult:
    final: str
    trace: list[str]
    messages: list[dict[str, Any]]

_SYSTEM_CORE = (
    "You are an AI coding agent running inside a CLI harness. "
    "You have access to tools for reading/writing files, running shell commands, "
    "searching the web, and asking the user questions. "
    "Use tools when needed; respond directly when you can."
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


def execute_one_tool_call(call, ctx, state, tools, emit) -> dict[str, Any]:
    """跑单个工具，返回一个 tool_result block。
    Day 7 内层 for call 循环体原样搬出：每处 append(block); continue 换成 return block。"""
    emit(f"tool_call: {call.name} {call.arguments}")

    request = PermissionRequest(tool_name=call.name, args=call.arguments,
                               mode=state.permission_mode, cwd=ctx.cwd)
    decision = decide_permission(request)

    if decision.behavior != "deny":
        pre = run_hooks("PreToolUse", call.name, call.arguments, ctx.cwd)
        blocked = [h for h in pre if not h["success"]]
        if blocked:
            msg = "\n".join(f"  [hook] {h['command']}: {h['output']}" for h in blocked)
            obs = f"tool blocked by PreToolUse hook:\n{msg}"
            emit(f"observation: {obs}")
            return {"type": "tool_result", "tool_use_id": call.id, "content": obs, "is_error": True}

    # ── execute_one_tool_call 里，PreToolUse hook 块之后加 ──
    if call.name == "exit_plan_mode":
        plan_summary = call.arguments.get("plan_summary", "")
        if not confirm_plan(plan_summary):       # 借回终端：渲染计划 + 等批准
            obs = "Plan not approved. Revise the plan and call exit_plan_mode again."
            emit(f"observation: {obs}")
            return {"type": "tool_result", "tool_use_id": call.id, "content": obs, "is_error": True}
        state.permission_mode = "acceptEdits"     # 批准后翻到 acceptEdits，写不再逐个确认——闭环核心不变量

    # 文件写前置校验（file_write/file_edit；acceptEdits 也要过校验，只是后面跳过确认 UI）
    edit_preview: tuple[str, str, str] | None = None
    if call.name in ("file_write", "file_edit") and decision.behavior != "deny":
        path_str = call.arguments.get("file_path", "")
        if not path_str:
            r = ToolResult(call.id, "error: missing required argument 'file_path'", is_error=True)
            emit(f"observation: {r.content}")
            return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}
        try:
            path = resolve_in_cwd(ctx.cwd, path_str)
        except (ValueError, OSError) as exc:
            r = ToolResult(call.id, f"error: {exc}", is_error=True)
            emit(f"observation: {r.content}")
            return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}
        old_content = path.read_text(encoding="utf-8") if path.exists() else ""
        validation_error: str | None = None
        if call.name == "file_write":
            if path.exists():
                validation_error = (ensure_read_before_edit(ctx.read_state, path)
                                    or check_mtime_conflict(ctx.read_state, path))
            new_content = call.arguments.get("content", "")
        else:
            new_content = ""
            if not path.exists():
                validation_error = f"error: file does not exist: {path_str}"
            else:
                validation_error = (ensure_read_before_edit(ctx.read_state, path)
                                    or check_mtime_conflict(ctx.read_state, path))
            if validation_error is None:
                new_content, replace_err = apply_single_replace(
                    old_content, call.arguments.get("old_string", ""),
                    call.arguments.get("new_string", ""), bool(call.arguments.get("replace_all", False)))
                if replace_err is not None:
                    validation_error = replace_err
        if validation_error is not None:
            r = ToolResult(call.id, validation_error, is_error=True)
            emit(f"observation: {r.content}")
            return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}
        edit_preview = (path_str, old_content, new_content)

    # deny：直接返回 error，不弹 UI
    if decision.behavior == "deny":
        r = ToolResult(call.id, f"error: {decision.message}", is_error=True)
        emit(f"observation: {r.content}")
        return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}

    # ask：按工具类型分发确认 UI（confirm_* 已在 1.5 包了 _ask，自动借回终端）
    if decision.behavior == "ask":
        if call.name in ("file_write", "file_edit") and edit_preview is not None:
            path_str, old_content, new_content = edit_preview
            console.print(f"\n[bold]Diff for {path_str}:[/bold]")
            console.print(render_diff(old_content, new_content, path_str))
            if not confirm_edit(path_str):
                r = ToolResult(call.id, "error: edit rejected by user", is_error=True)
                emit(f"observation: {r.content}")
                return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}
        elif call.name == "bash":
            command = call.arguments.get("command", "")
            console.print(f"\n[bold yellow]Command:[/bold yellow] {command}")
            if not confirm_command(command):
                r = ToolResult(call.id, "error: command rejected by user", is_error=True)
                emit(f"observation: {r.content}")
                return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}
        elif call.name in ("web_fetch", "web_search"):
            detail = call.arguments.get("url") or call.arguments.get("query") or str(call.arguments)
            if not confirm_tool_use(call.name, detail):
                r = ToolResult(call.id, "error: tool use rejected by user", is_error=True)
                emit(f"observation: {r.content}")
                return {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": True}
        elif call.name == "ask_user_question":
            options = call.arguments.get("options", [])
            labels = [str(o) for o in options] if isinstance(options, list) else []
            selected = prompt_single_choice(call.arguments.get("prompt", ""), labels)
            content = "User skipped the question." if selected is None else f'User selected: "{selected}"'
            emit(f"observation: {content}")
            return {"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": False}

    # allow / ask 通过：执行
    result = tools.run(call, ctx)
    emit(f"observation: {result.content}")
    if not result.is_error:
        for h in run_hooks("PostToolUse", call.name, call.arguments, ctx.cwd, tool_result=result.content):
            status = "ok" if h["success"] else f"warning: {h['output']}"
            console.print(f"[dim]hook: PostToolUse {call.name} {status}[/dim]")
    return {"type": "tool_result", "tool_use_id": result.tool_call_id,
            "content": result.content, "is_error": result.is_error}


def partition_tool_calls(calls, tools) -> list[list]:
    """连续只读工具合成并行组；写工具截断、自成串行组。
    例：[Read, Read, Write, Read] → [[Read, Read], [Write], [Read]]"""
    batches: list[list] = []
    current: list = []
    for call in calls:
        tool = tools.get(call.name)
        if tool is not None and tool.is_read_only:
            current.append(call)
        else:
            if current:                       # 写工具前先收掉前面攒的只读组
                batches.append(current)
                current = []
            batches.append([call])            # 写/未知工具单独一组（未知 fail-closed 当串行）
    if current:
        batches.append(current)
    return batches


def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    state: RuntimeState | None = None,   # 原来是 permission_mode: str = "default"
    session: Session | None = None,
    system_prompt: str | None = None,
) -> AgentResult:
    resolved_cwd = cwd or Path.cwd()
    state = state or RuntimeState()       # one-shot 没传就用默认
    ctx = ToolContext(
        cwd=resolved_cwd,
        skip_policy=SkipPolicy.default(gitignore=load_gitignore(resolved_cwd)),
        runtime_state=state,             # 新增：todo / plan 工具靠它读写共享态
    )
    def emit(line: str) -> None:
        # 流式输出 trace：append 给测试用，print 给读者看
        trace.append(line)
        console.print(line, markup=False)
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
            # Day 8 v3：Stop hook——模型自认答完，给 hook 一次"再推一轮"的机会
            from .hooks import run_hooks_raw
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

        tool_result_blocks: list[dict[str, Any]] = []
        for batch in partition_tool_calls(response.tool_calls, tools):
            if len(batch) == 1:
                tool_result_blocks.append(execute_one_tool_call(batch[0], ctx, state, tools, emit))
            else:
                # 只读组并行。ex.map 按输入顺序返回结果，
                # 所以 tool_result 顺序天然对齐 tool_use 顺序——这是必须守的协议约束。
                with ThreadPoolExecutor(max_workers=4) as ex:
                    results = list(ex.map(
                        lambda c: execute_one_tool_call(c, ctx, state, tools, emit), batch
                    ))
                tool_result_blocks.extend(results)

        messages.append({"role": "user", "content": tool_result_blocks})
        if session:
            session.append_messages(messages[-2:])

    final = f"reached max_steps={max_steps}"
    emit(f"final: {final}")
    return AgentResult(final=final, trace=trace, messages=messages)
