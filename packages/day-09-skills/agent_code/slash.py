from __future__ import annotations

import shlex

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .runtime import RuntimeState


@dataclass
class SlashContext:
    """slash handler 接收的运行时上下文。"""

    cwd: Path
    permission_mode: str
    model: str
    provider: str
    session_id: str | None
    state: RuntimeState | None = None   # Day 8 v2：slash 改运行时状态的入口


class SlashResult:
    """slash command 执行结果。should_query=True 时会把 prompt 送回 Agent Loop。"""

    def __init__(
        self,
        handled: bool = True,
        should_query: bool = False,
        prompt: str = "",
        message: str = "",
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.handled = handled
        self.should_query = should_query
        self.prompt = prompt
        self.message = message
        self.allowed_tools = allowed_tools


SlashHandler = Callable[[list[str], SlashContext], SlashResult]


@dataclass
class SlashCommand:
    """一条 slash command 的注册信息。name 不加 /。"""

    name: str
    description: str
    handler: SlashHandler


_registry: dict[str, SlashCommand] = {}


def register(name: str, description: str, handler: SlashHandler) -> None:
    _registry[name] = SlashCommand(name=name, description=description, handler=handler)


def dispatch_slash(line: str, ctx: SlashContext) -> SlashResult:
    if not line.startswith("/"):
        return SlashResult(handled=False)
    try:
        parts = shlex.split(line[1:].strip())
    except ValueError as exc:
        return SlashResult(handled=True, message=f"Invalid command syntax: {exc}")
    if not parts:
        return SlashResult(handled=False)
    name = parts[0]
    args = parts[1:]
    cmd = _registry.get(name)
    if cmd is None:
        return SlashResult(handled=True, message=f"Unknown command: /{name}")
    return cmd.handler(args, ctx)


def _cmd_help(_args: list[str], ctx: SlashContext) -> SlashResult:
    lines = ["[bold]可用命令：[/bold]"]
    for name in sorted(_registry.keys()):
        desc = _registry[name].description
        lines.append(f"  [bold]/{name}[/bold]  {desc}")
    return SlashResult(handled=True, message="\n".join(lines))


def _cmd_model(args: list[str], ctx: SlashContext) -> SlashResult:
    if not args:
        return SlashResult(handled=True, message=f"provider: {ctx.provider}  model: {ctx.model}")
    target = args[0]
    if ctx.state is not None:
        ctx.state.model = target            # 下一轮 run_turn 按 state.model 重建 provider
    return SlashResult(handled=True, message=f"model → {target}（下一轮生效，当前轮不变）")


def _cmd_context(_args: list[str], ctx: SlashContext) -> SlashResult:
    session = ctx.session_id or "(none)"
    return SlashResult(
        handled=True,
        message=f"cwd: {ctx.cwd}\nsession: {session}\npermission: {ctx.permission_mode}\nmodel: {ctx.provider}/{ctx.model}",
    )


def _cmd_compact(_args: list[str], ctx: SlashContext) -> SlashResult:
    return SlashResult(
        handled=True,
        message="compact: 当前版本只支持自动 compact。messages 超过阈值时会在 Agent Loop 内触发。",
    )


def _cmd_permissions(args: list[str], ctx: SlashContext) -> SlashResult:
    modes = ["default", "acceptEdits", "plan"]
    if not args:
        return SlashResult(
            handled=True,
            message=f"permission mode: {ctx.permission_mode}\navailable: {', '.join(modes)}",
        )
    target = args[0]
    if target not in modes:
        return SlashResult(handled=True, message=f"Unknown mode: {target}. Use: {', '.join(modes)}")
    return SlashResult(
        handled=True,
        message=f"当前版本不在 REPL 内热切换权限模式。请用 --permission-mode {target} 重新启动。",
    )


def _cmd_plan(args: list[str], ctx: SlashContext) -> SlashResult:
    if ctx.state is None:
        return SlashResult(handled=True, message="plan 模式需要交互 shell")
    if args and args[0] == "off":
        ctx.state.permission_mode = "default"
        return SlashResult(handled=True, message="exited plan mode")
    ctx.state.permission_mode = "plan"
    return SlashResult(handled=True, message="entered plan mode（写工具被禁，用 exit_plan_mode 提交计划）")


def _cmd_todo(_args: list[str], ctx: SlashContext) -> SlashResult:
    items = ctx.state.todo_store if ctx.state else []
    icon = {"pending": "○", "in_progress": "◉", "completed": "✓"}
    body = "\n".join(f"  {icon.get(t.status, '?')} {t.content}" for t in items) or "(no todos)"
    return SlashResult(handled=True, message=body)


def _cmd_skills(_args: list[str], ctx: SlashContext) -> SlashResult:
    from .skills import SkillLoader

    loader = SkillLoader(ctx.cwd)
    message = loader.render_list()
    if loader.warnings:
        message += "\n\nwarnings:\n" + "\n".join(f"- {w}" for w in loader.warnings)
    return SlashResult(handled=True, message=message)


def _cmd_skill(args: list[str], ctx: SlashContext) -> SlashResult:
    from .skills import SkillLoader

    if not args:
        return SlashResult(handled=True, message="用法: /skill <name> [任务]")

    name = args[0]
    task = " ".join(args[1:]).strip() or "按这个 skill 的流程完成当前任务。"
    skill = SkillLoader(ctx.cwd).load(name)
    if skill is None:
        return SlashResult(handled=True, message=f"skill not found: {name}")

    prompt = (
        f"Use this skill for the next task.\n\n"
        f"<skill name=\"{skill.name}\">\n{skill.body}\n</skill>\n\n"
        f"Task: {task}"
    )
    return SlashResult(
        handled=True,
        should_query=True,
        prompt=prompt,
        allowed_tools=skill.allowed_tools,
    )


def _cmd_output_style(args: list[str], ctx: SlashContext) -> SlashResult:
    from .skills import list_output_styles, load_output_style

    if ctx.state is None:
        return SlashResult(handled=True, message="output-style 需要交互 shell")

    subcommand = args[0] if args else "list"
    if subcommand == "list":
        styles = list_output_styles(ctx.cwd)
        if not styles:
            return SlashResult(handled=True, message="(no output styles found)")
        lines = [
            f"{style.name}  {style.description}" if style.description else style.name
            for style in styles
        ]
        current = ctx.state.output_style or "(default)"
        return SlashResult(handled=True, message=f"current: {current}\n" + "\n".join(lines))

    if subcommand == "use":
        if len(args) < 2:
            return SlashResult(handled=True, message="用法: /output-style use <name>")
        name = args[1]
        if load_output_style(ctx.cwd, name) is None:
            return SlashResult(handled=True, message=f"output style not found: {name}")
        ctx.state.output_style = name
        return SlashResult(handled=True, message=f"output style -> {name}")

    if subcommand == "reset":
        ctx.state.output_style = None
        return SlashResult(handled=True, message="output style reset")

    return SlashResult(
        handled=True,
        message="用法: /output-style list | /output-style use <name> | /output-style reset",
    )


def _cmd_loop_add(args: list[str], ctx: SlashContext) -> SlashResult:
    from .cron_tools import cron_create
    from .tools import ToolContext

    if not args:
        return SlashResult(handled=True, message="用法: /loop add <slash或prompt> --every <60s|5m|2h> --label <标签>")

    slash_parts: list[str] = []
    every_seconds: int | None = None
    label = ""
    i = 0

    def _parse_every(raw: str) -> int:
        units = {"s": 1, "m": 60, "h": 3600}
        if raw[-1:] in units:
            return int(raw[:-1]) * units[raw[-1]]
        return int(raw)

    while i < len(args):
        if args[i] == "--every" and i + 1 < len(args):
            try:
                every_seconds = _parse_every(args[i + 1])
            except (ValueError, IndexError):
                return SlashResult(handled=True, message="--every 需要整数秒，或 60s / 5m / 2h 这种格式")
            i += 2
        elif args[i] == "--label" and i + 1 < len(args):
            label = args[i + 1]
            i += 2
        else:
            slash_parts.append(args[i])
            i += 1

    slash = " ".join(slash_parts)
    if not slash:
        return SlashResult(handled=True, message="用法: /loop add <slash或prompt> --every <60s|5m|2h>")
    if every_seconds is None:
        return SlashResult(handled=True, message="缺少 --every。用法: /loop add <slash或prompt> --every <60s|5m|2h>")

    tool_ctx = ToolContext(cwd=ctx.cwd)
    msg = cron_create({"slash": slash, "every_seconds": every_seconds, "label": label}, tool_ctx)
    return SlashResult(handled=True, message=msg)


def _cmd_loop_list(_args: list[str], ctx: SlashContext) -> SlashResult:
    from .cron_tools import cron_list
    from .tools import ToolContext

    tool_ctx = ToolContext(cwd=ctx.cwd)
    msg = cron_list({}, tool_ctx)
    return SlashResult(handled=True, message=msg)


def _cmd_loop_cancel(args: list[str], ctx: SlashContext) -> SlashResult:
    from .cron_tools import cron_cancel
    from .tools import ToolContext

    if not args:
        return SlashResult(handled=True, message="用法: /loop cancel <id>")
    tool_ctx = ToolContext(cwd=ctx.cwd)
    msg = cron_cancel({"id": args[0]}, tool_ctx)
    return SlashResult(handled=True, message=msg)


def _cmd_loop(args: list[str], ctx: SlashContext) -> SlashResult:
    if not args:
        return SlashResult(
            handled=True,
            message="用法: /loop add <slash或prompt> --every <60s|5m|2h> --label <标签>\n      /loop list\n      /loop cancel <id>",
        )
    subcommand = args[0]
    rest = args[1:]
    if subcommand == "add":
        return _cmd_loop_add(rest, ctx)
    if subcommand == "list":
        return _cmd_loop_list(rest, ctx)
    if subcommand == "cancel":
        return _cmd_loop_cancel(rest, ctx)
    return SlashResult(handled=True, message=f"Unknown /loop subcommand: {subcommand}")


register("help", "显示所有可用 slash command", _cmd_help)
register("model", "显示当前模型/provider", _cmd_model)
register("context", "显示当前 session、cwd、权限模式", _cmd_context)
register("compact", "显示 compact 状态", _cmd_compact)
register("permissions", "显示权限模式 (default/acceptEdits/plan)", _cmd_permissions)
register("plan", "进入/退出 plan 模式", _cmd_plan)
register("todo", "显示当前 todo 列表", _cmd_todo)
register("skills", "列出本地 .agent/skills 里的 skill", _cmd_skills)
register("skill", "用指定 skill 执行本轮任务", _cmd_skill)
register("output-style", "列出/切换/重置当前回答风格", _cmd_output_style)
register("loop", "管理 cron 定时任务: add/list/cancel", _cmd_loop)
