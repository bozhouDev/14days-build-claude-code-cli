from __future__ import annotations

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
from .permissions import PermissionRequest, decide_permission
from .prompt_ui import confirm_command, confirm_edit, confirm_tool_use, prompt_single_choice, render_diff
from .session import Session  # Day 6：会话持久化
from .project_memory import load_agent_md  # Day 6：AGENT.md 项目记忆
from .compact_basic import compact  # Day 6：确定性压缩
from .hooks import run_hooks  # Day 7：生命周期钩子

console = Console()


@dataclass
class AgentResult:
    final: str
    trace: list[str]
    messages: list[dict[str, Any]]


# Day 6：核心 system prompt——Agent 的行为指南
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


def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    permission_mode: str = "default",  # 新增：default | acceptEdits | plan
    session: Session | None = None,  # Day 6：传 None 退化为 Day 5 行为
    system_prompt: str | None = None,  # Day 6 v2：cli.py 冷启动时拼好传进来
) -> AgentResult:
    resolved_cwd = cwd or Path.cwd()
    ctx = ToolContext(
        cwd=resolved_cwd,
        skip_policy=SkipPolicy.default(gitignore=load_gitignore(resolved_cwd)),
    )
    def emit(line: str) -> None:
        # 流式输出 trace：append 给测试用，print 给读者看
        trace.append(line)
        console.print(line, markup=False)
    messages: list[dict[str, Any]] = []
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
    for step in range(max_steps):
        # Day 6：消息超过 40 条时自动压缩（保持上下文不爆）
        if len(messages) > 40:
            messages = compact(messages, keep=8)
            console.print(f"[dim]compacted: {len(messages)} messages remaining[/dim]")
        response = provider.complete(
            messages, tools=tools.list(), system=system_prompt
        )
        messages.append(_assistant_message(response))

        if not response.tool_calls:
            final = response.text or ""
            emit(f"final: {final}")
            # Day 6：把最终 assistant 消息落盘
            if session:
                session.append_messages([messages[-1]])
            return AgentResult(final=final, trace=trace, messages=messages)

        tool_result_blocks: list[dict[str, Any]] = []
        for call in response.tool_calls:
            emit(f"tool_call: {call.name} {call.arguments}")

            # 权限引擎统一入口：所有工具调用先包装成 PermissionRequest
            request = PermissionRequest(
                tool_name=call.name,
                args=call.arguments,
                mode=permission_mode,
                cwd=ctx.cwd,
            )
            decision = decide_permission(request)

            # Day 7：PreToolUse hooks — 在工具执行前跑，能阻断工具
            # plan 模式等 deny 决策已经在上面算出，deny 不再执行本地 hook，避免 hook 副作用。
            if decision.behavior != "deny":
                pre_hooks = run_hooks(
                    "PreToolUse", call.name, call.arguments, ctx.cwd,
                )
                pre_blocked = [h for h in pre_hooks if not h["success"]]
                if pre_blocked:
                    blocked_msgs = "\n".join(
                        f"  [hook] {h['command']}: {h['output']}" for h in pre_blocked
                    )
                    observation = f"tool blocked by PreToolUse hook:\n{blocked_msgs}"
                    emit(f"observation: {observation}")
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": observation,
                        "is_error": True,
                    })
                    continue

            edit_preview: tuple[str, str, str] | None = None
            if call.name in ("file_write", "file_edit") and decision.behavior != "deny":
                # acceptEdits 只跳过确认 UI，不能跳过 Day 4 的安全校验
                path_str = call.arguments.get("file_path", "")
                if not path_str:
                    result = ToolResult(call.id, "error: missing required argument 'file_path'", is_error=True)
                    emit(f"observation: {result.content}")
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": True,
                        }
                    )
                    continue

                try:
                    path = resolve_in_cwd(ctx.cwd, path_str)
                except (ValueError, OSError) as exc:
                    result = ToolResult(call.id, f"error: {exc}", is_error=True)
                    emit(f"observation: {result.content}")
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": True,
                        }
                    )
                    continue

                if path.is_dir():
                    result = ToolResult(call.id, f"error: path is a directory: {path_str}", is_error=True)
                    emit(f"observation: {result.content}")
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": True,
                        }
                    )
                    continue

                old_content = path.read_text(encoding="utf-8") if path.exists() else ""

                validation_error: str | None = None
                if call.name == "file_write":
                    if path.exists():
                        validation_error = (
                            ensure_read_before_edit(ctx.read_state, path)
                            or check_mtime_conflict(ctx.read_state, path)
                        )
                    new_content = call.arguments.get("content", "")
                else:  # file_edit
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
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": True,
                        }
                    )
                    continue

                edit_preview = (path_str, old_content, new_content)

            if decision.behavior == "deny":
                # deny 路径：直接返回 error observation，不弹 UI
                result = ToolResult(call.id, f"error: {decision.message}", is_error=True)
                emit(f"observation: {result.content}")
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": result.content,
                        "is_error": True,
                    }
                )
                continue

            elif decision.behavior == "ask":
                # ask 路径：按工具类型分发不同的预览和确认 UI
                if call.name in ("file_write", "file_edit"):
                    # --- 文件编辑：安全校验已经做过；ask 模式只负责 diff + confirm ---
                    if edit_preview is not None:
                        path_str, old_content, new_content = edit_preview
                        diff_text = render_diff(old_content, new_content, path_str)
                        console.print(f"\n[bold]Diff for {path_str}:[/bold]")
                        console.print(diff_text)
                        if not confirm_edit(path_str):
                            result = ToolResult(call.id, "error: edit rejected by user", is_error=True)
                            emit(f"observation: {result.content}")
                            tool_result_blocks.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": result.tool_call_id,
                                    "content": result.content,
                                    "is_error": True,
                                }
                            )
                            continue

                elif call.name == "bash":
                    # --- bash：命令预览 + confirm ---
                    command = call.arguments.get("command", "")
                    timeout = call.arguments.get("timeout", 30)
                    console.print(f"\n[bold yellow]Command:[/bold yellow] {command}")
                    console.print(f"[dim]timeout: {timeout}s  cwd: {ctx.cwd}[/dim]")
                    if not confirm_command(command):
                        result = ToolResult(call.id, "error: command rejected by user", is_error=True)
                        emit(f"observation: {result.content}")
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": result.tool_call_id,
                                "content": result.content,
                                "is_error": True,
                            }
                        )
                        continue

                elif call.name in ("web_fetch", "web_search"):
                    # --- 网络工具：不写本地文件，但要让用户确认是否访问外部资源 ---
                    detail = call.arguments.get("url") or call.arguments.get("query") or str(call.arguments)
                    if not confirm_tool_use(call.name, detail):
                        result = ToolResult(call.id, "error: tool use rejected by user", is_error=True)
                        emit(f"observation: {result.content}")
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": result.tool_call_id,
                                "content": result.content,
                                "is_error": True,
                            }
                        )
                        continue

                elif call.name == "ask_user_question":
                    question = call.arguments.get("prompt", "")
                    options = call.arguments.get("options", [])
                    if not isinstance(options, list):
                        options = []
                    labels = [str(o) for o in options]
                    selected = prompt_single_choice(question, labels)
                    if selected is None:
                        result = ToolResult(call.id, "User skipped the question.", is_error=False)
                    else:
                        result = ToolResult(call.id, f'User selected: "{selected}"', is_error=False)
                    emit(f"observation: {result.content}")
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": result.is_error,
                        }
                    )
                    continue

            # allow 路径 + ask 通过：执行工具
            result = tools.run(call, ctx)
            emit(f"observation: {result.content}")

            # Day 7：PostToolUse hooks — 在工具执行成功后跑，失败不阻断
            if not result.is_error:
                post_hooks = run_hooks(
                    "PostToolUse", call.name, call.arguments, ctx.cwd,
                    tool_result=result.content,
                )
                for h in post_hooks:
                    status = "ok" if h["success"] else f"warning: {h['output']}"
                    console.print(f"[dim]hook: PostToolUse {call.name} {status}[/dim]")
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
            )

        messages.append({"role": "user", "content": tool_result_blocks})

        # Day 6：每轮结束后把 assistant + tool_result 两条消息落盘
        if session:
            session.append_messages(messages[-2:])

    final = f"reached max_steps={max_steps}"
    emit(f"final: {final}")
    return AgentResult(final=final, trace=trace, messages=messages)
