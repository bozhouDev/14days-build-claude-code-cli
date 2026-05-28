# Day 7：Slash Commands + Hooks + Cron 定时任务

Day 6 让 Agent 有了三层记忆——会话历史、AGENT.md 项目规则、跨 session 长期记忆。单 Agent CLI 的核心能力到这里基本完整了。

但 `cli.py` 里还有一个"历史遗留问题"：`/help`、`/exit` 这些运行时控制命令全是硬编码 `if/elif`，每次加新命令都要改 CLI 主逻辑。更麻烦的是——如果你想在 Agent 每次 `file_edit` 之后自动跑一个项目脚本，或者在 REPL 里挂一个"每 5 分钟检查 PR 状态"的定时任务，现在完全做不到。

今天给 harness 开三个扩展面：

- **Slash 注册表**：把运行时控制命令从 `cli.py` 拆出来，统一注册、统一 dispatch。
- **生命周期钩子**：工具调用前后可以插你自己的命令——检查、格式化、记日志。
- **Cron 定时任务**：注册一条 slash/prompt，让 REPL 每隔 N 秒自动重放一次。

跑完之后你会看到：

- `/help`、`/context`、`/permissions`、`/plan` 全是注册表里的命令，不再硬编码在 `cli.py`
- Agent 每次 `file_edit` 之后自动跑一条 `hooks.json` 里的命令
- REPL 里 `/loop add /context --every 60s`，到点自动把这条 slash 排进 Agent Loop

注意：今天的 `/permissions` 和 `/plan` 先做成"查看当前状态 + 给出启动方式"的入口，不在 REPL 里热切换权限模式。完整 Plan Mode 审批闭环留到 Day 8。

代码约 520 行新增，新建 4 个文件，主要改 `slash.py`、`hooks.py`、`scheduler.py`、`cron_tools.py`、`agent.py`、`cli.py`、`tools.py`、`permissions.py`，再补一次 `pyproject.toml` 的命令入口检查。


Day 7 是前 7 天的收官日。把 harness 从"固定程序"升级成"可扩展 CLI 工作台"，后 7 天的 Skills、Subagents、Worktree 都会挂在今天建的扩展面上。

动手前先看一下今天的起点问题。从 Day 6 项目根目录跑：

```bash
$ uv run agent-code
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

输入 /help 查看命令，输入 /exit 退出。
> /help
可用命令：/help, /exit
```

现在只有早期硬编码的 `/help`，没有 `/context`、`/permissions`、`/plan`。v1 就是把这个控制面板拆成注册表。

## 起手：今天的起点

从 Day 6 的 `agent-code` 项目继续改。不需要新依赖——`json`、`threading`、`subprocess`、`queue` 都是标准库。

新增：

```txt
agent_code/slash.py           slash 注册表 + dispatch
agent_code/hooks.py           hooks.json 加载 + PreToolUse / PostToolUse 执行
agent_code/scheduler.py       CronJob 管理 + 后台调度线程
agent_code/cron_tools.py      cron_create / cron_list / cron_cancel 工具
```

这里没有建 `agent_code/tools/cron.py`，因为当前项目里 `agent_code/tools.py` 已经是一个单文件模块。Python 不能在同一层同时把 `tools` 当文件和目录用，所以 cron 工具先放进 `cron_tools.py`。

改动：

```txt
agent_code/agent.py           工具执行前后插入 hook dispatch
agent_code/cli.py             删掉 handle_slash，全走 slash.dispatch；REPL 启动 scheduler；drain pending queue
agent_code/slash.py           注册 /loop add/list/cancel
agent_code/tools.py           注册 cron_* 三个工具
agent_code/permissions.py     cron_list 加进只读工具集合
pyproject.toml                确认 [project.scripts] 里有 agent-code 入口
```

`pyproject.toml` 如果 Day 1 已经加过 `[project.scripts]`，v3 只需要检查，不要重复写一段。

今天分四步：v1 把 slash 拆出来，v2 加 hooks 生命周期拦截，v3 全局安装让 `agent-code` 在任意目录可用，v4 加 cron 定时自唤醒。

## v1：Slash 注册表

先看问题。Day 1 到现在，`cli.py` 里有个 `handle_slash` 函数，大概是这样的：

```python
def handle_slash(text: str) -> bool:
    """处理 slash command，返回 True 表示已处理。"""
    if text == "/help":
        console.print("[bold]可用命令：[/bold]")
        console.print("  /help  显示帮助")
        console.print("  /exit  退出")
        return True
    if text == "/exit":
        raise typer.Exit()
    return False
```

这个写法在小项目里没问题，但每加一个命令就要多一个 `if`。更关键的是——slash handler 和 CLI 主逻辑混在一起，读不到当前 session、provider、权限模式这些运行时状态。`/context` 想打印当前 token 用量就得 import agent 模块，耦合越来越深。

v1 做三件事：新建 `slash.py` 定义注册表和 dispatch、往注册表里填 6 个内置命令、把 `cli.py` 的 `handle_slash` 替换成 `dispatch_slash`。

### 1.1 新建 `agent_code/slash.py`

首先定义三个数据结构——slash command 需要知道"自己叫什么、干什么、运行时上下文是什么、执行完返回什么"。

```python
from __future__ import annotations

import shlex

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class SlashContext:
    """slash handler 接收的运行时上下文。不暴露 provider、session 内部状态，
    只给 handler 它需要的只读信息。"""
    cwd: Path
    permission_mode: str         # "default" / "acceptEdits" / "plan"
    model: str                   # 当前模型名，如 "deepseek-v4-flash"
    provider: str                # 当前 provider，如 "anthropic"
    session_id: str | None       # 当前 session id（可能为 None）


class SlashResult:
    """slash command 执行结果。handled=True 表示已处理，CLI 不再把输入当普通 prompt。
    should_query=True 时 CLI 把 prompt 字段作为新的 user 消息喂给模型。"""

    def __init__(
        self,
        handled: bool = True,
        should_query: bool = False,
        prompt: str = "",
        message: str = "",
    ) -> None:
        self.handled = handled          # True = 命令已处理
        self.should_query = should_query  # True = 把 prompt 作为新用户输入再跑一圈
        self.prompt = prompt            # should_query=True 时的模型 prompt
        self.message = message          # 打给用户的终端消息（本地命令用）


# 返回 SlashResult 的 handler 签名
SlashHandler = Callable[[list[str], SlashContext], SlashResult]


@dataclass
class SlashCommand:
    """一条 slash command 的注册信息。name 不加前缀 /。"""
    name: str
    description: str                    # /help 列出时显示
    handler: SlashHandler               # 实际执行函数
```

`SlashContext` 是关键设计选择——它给 handler 的是当前 CLI 状态的**快照**，不是全局变量的引用。这样 handler 不能意外改 session 或 provider，而且测试时可以直接构造 SlashContext 不用模拟整个 CLI。

`SlashResult.should_query` 是区分"本地命令"和"转模型命令"的边界。`/help` 是本地命令（`should_query=False`），只打印终端消息；以后如果加 `/review` 这种会展开成模型 prompt 的命令，就让它返回 `should_query=True`，CLI 再把 `prompt` 字段喂给 Agent Loop。

然后加注册表和 dispatch 函数：

```python
# 全局注册表：模块加载后所有内置命令都注册在这里
_registry: dict[str, SlashCommand] = {}


def register(name: str, description: str, handler: SlashHandler) -> None:
    """注册一条 slash command。name 不要带 / 前缀。"""
    _registry[name] = SlashCommand(name=name, description=description, handler=handler)


def dispatch_slash(line: str, ctx: SlashContext) -> SlashResult:
    """解析 "/name args" 并分派到已注册命令。未匹配时返回 handled=False。"""
    if not line.startswith("/"):
        return SlashResult(handled=False)
    # 去掉首字符 /，用 shlex 拆 command name 和 args，这样 --label "PR 状态轮询" 能保留空格。
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
```

`dispatch_slash` 的解析规则很简单：`/` 开头 → 第一个 token 是命令名 → 后面全是 args。这里用 `shlex.split` 是为了让 `--label "PR 状态轮询"` 这种带空格的参数能正常保留。不做文件路径歧义判断（`/tmp/foo` 看起来像路径但其实是 slash），先保持简单。

### 1.2 注册 6 个内置命令

在 `slash.py` 底部，用 `register()` 注册 `/help`、`/model`、`/context`、`/compact`、`/permissions`、`/plan`。

```python
def _cmd_help(_args: list[str], ctx: SlashContext) -> SlashResult:
    """列出所有已注册 slash command。"""
    lines = ["[bold]可用命令：[/bold]"]
    for name in sorted(_registry.keys()):
        desc = _registry[name].description
        lines.append(f"  [bold]/{name}[/bold]  {desc}")
    # 不调用模型——纯本地控制命令
    return SlashResult(handled=True, message="\n".join(lines))


def _cmd_model(args: list[str], ctx: SlashContext) -> SlashResult:
    """显示或切换当前模型。不传参打印当前值；传参时只打印提示，
    告诉用户当前 CLI 实现不支持运行时热切换 provider。"""
    if not args:
        return SlashResult(
            handled=True,
            message=f"provider: {ctx.provider}  model: {ctx.model}",
        )
    # 切换模型需要重建 provider，牵涉到 Anthropic/OpenAI 客户端实例化，
    # 当前版本不支持 REPL 中热切换。Day 8 随着 Plan Mode 一起做。
    return SlashResult(
        handled=True,
        message=f"Cannot change model at runtime. Current: {ctx.provider}/{ctx.model}",
    )


def _cmd_context(_args: list[str], ctx: SlashContext) -> SlashResult:
    """打印当前 session 和权限模式。"""
    session = ctx.session_id or "(none)"
    return SlashResult(
        handled=True,
        message=f"cwd: {ctx.cwd}\nsession: {session}\npermission: {ctx.permission_mode}\nmodel: {ctx.provider}/{ctx.model}",
    )


def _cmd_compact(_args: list[str], ctx: SlashContext) -> SlashResult:
    """显示 compact 状态。真正的手动 compact 需要能重写 session 历史，先不做。"""
    # Day 6 已经有自动 compact：run_agent 里 messages 超过阈值会触发。
    # 手动 /compact 要重写 Session JSONL 或当前 messages，这会扩大 v1 的状态管理范围。
    return SlashResult(
        handled=True,
        message="compact: 当前版本只支持自动 compact。messages 超过阈值时会在 Agent Loop 内触发。",
    )


def _cmd_permissions(args: list[str], ctx: SlashContext) -> SlashResult:
    """显示权限模式。v1 不在 REPL 内热切换运行态。"""
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
    """显示 plan 模式提示。完整 Plan Mode 闭环等 Day 8。"""
    if args and args[0] == "off":
        return SlashResult(handled=True, message="当前版本不在 REPL 内热切换权限模式。请重新用 --permission-mode default 启动。")
    if ctx.permission_mode == "plan":
        return SlashResult(handled=True, message="当前已经是 plan 模式。完整审批闭环会在 Day 8 实现。")
    return SlashResult(handled=True, message="要进入 plan 模式，请重新用 --permission-mode plan 启动。完整审批闭环会在 Day 8 实现。")


# --- 注册内置命令 ---
register("help", "显示所有可用 slash command", _cmd_help)
register("model", "显示当前模型/provider", _cmd_model)
register("context", "显示当前 session、cwd、权限模式", _cmd_context)
register("compact", "显示 compact 状态", _cmd_compact)
register("permissions", "显示权限模式 (default/acceptEdits/plan)", _cmd_permissions)
register("plan", "显示 plan 模式提示", _cmd_plan)
```

`/permissions` 和 `/plan` 这里先只做信息展示，不在 REPL 里热切换 `permission_mode`。原因是权限模式是 `cli.py` 主循环持有的运行态变量，直接让 `slash.py` 改它会把注册表和 CLI 状态绑死。后面课后挑战会把这个能力补成 `SlashResult.new_permission_mode` 或 callback。

到这里，v1 版本的 `agent_code/slash.py` 完整文件如下。前面分段讲过每一块为什么存在，真正写文件时直接以这份为准：

```python
from __future__ import annotations

import shlex

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class SlashContext:
    """slash handler 接收的运行时上下文。"""
    cwd: Path
    permission_mode: str
    model: str
    provider: str
    session_id: str | None


class SlashResult:
    """slash command 执行结果。should_query=True 时会把 prompt 送回 Agent Loop。"""

    def __init__(
        self,
        handled: bool = True,
        should_query: bool = False,
        prompt: str = "",
        message: str = "",
    ) -> None:
        self.handled = handled
        self.should_query = should_query
        self.prompt = prompt
        self.message = message


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
        return SlashResult(
            handled=True,
            message=f"provider: {ctx.provider}  model: {ctx.model}",
        )
    return SlashResult(
        handled=True,
        message=f"Cannot change model at runtime. Current: {ctx.provider}/{ctx.model}",
    )


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
    if args and args[0] == "off":
        return SlashResult(handled=True, message="当前版本不在 REPL 内热切换权限模式。请重新用 --permission-mode default 启动。")
    if ctx.permission_mode == "plan":
        return SlashResult(handled=True, message="当前已经是 plan 模式。完整审批闭环会在 Day 8 实现。")
    return SlashResult(handled=True, message="要进入 plan 模式，请重新用 --permission-mode plan 启动。完整审批闭环会在 Day 8 实现。")


register("help", "显示所有可用 slash command", _cmd_help)
register("model", "显示当前模型/provider", _cmd_model)
register("context", "显示当前 session、cwd、权限模式", _cmd_context)
register("compact", "显示 compact 状态", _cmd_compact)
register("permissions", "显示权限模式 (default/acceptEdits/plan)", _cmd_permissions)
register("plan", "显示 plan 模式提示", _cmd_plan)
```

### 1.3 改 `agent_code/cli.py`：删掉 handle_slash，全走 dispatch

**第一处**，顶部 import 追加：

```python
from .slash import SlashContext, dispatch_slash  # Day 7：slash 注册表
```

放在 `from .agent import ...` 之后。

**第二处**，删除旧的 `handle_slash` 函数和 `handle_slash` 调用。然后在 `main_command()` 里、`system_prompt = build_system_prompt(...)` 之后，放一个统一入口函数：

```python
    def run_user_input(line: str) -> None:
        """统一处理用户输入：先走 slash dispatch，未命中再进入 Agent Loop。
        REPL 用户输入和 cron pending prompt 都必须走这个入口。"""
        nonlocal session
        slash_result = dispatch_slash(
            line,
            SlashContext(
                cwd=resolved_cwd,
                permission_mode=permission_mode,
                model=model,
                provider=provider,
                session_id=session.session_id if session else None,
            ),
        )
        if slash_result.handled:
            if slash_result.message:
                console.print(slash_result.message)
            if slash_result.should_query:
                # 把 slash 展开的 prompt 作为新一轮用户输入跑 Agent Loop
                if session is None:
                    session = Session.create(resolved_cwd)
                run_once(
                    slash_result.prompt, resolved_cwd, provider, model, base_url, max_steps,
                    permission_mode, session=session, system_prompt=system_prompt,
                )
            return

        if session is None:
            session = Session.create(resolved_cwd)
        run_once(
            line, resolved_cwd, provider, model, base_url, max_steps,
            permission_mode, session=session, system_prompt=system_prompt,
        )
```

这一步把"输入是不是 slash"的判断从 REPL 循环里提出来。后面 cron 到点时也会调用 `run_user_input(pp)`，否则 `/context` 这种 pending slash 会被当成普通 prompt 发给模型。

**第三处**，一次性模式（`if text:` 分支）里调用这个统一入口。把原来的：

```python
    if text:
        if session is None:
            session = Session.create(resolved_cwd)
        run_once(text, resolved_cwd, provider, model, base_url, max_steps,
                 permission_mode, session=session, system_prompt=system_prompt)
        return
```

替换成：

```python
    if text:
        run_user_input(text.strip())
        return
```

**第四处**，REPL 循环里，把原来的 `run_once(...)` 调用替换成：

```python
        run_user_input(line)
```

### 1.4 跑验证

**(a) `/help` 列出所有命令：**

```bash
$ uv run agent-code "/help"
可用命令：
  /compact  显示 compact 状态
  /context  显示当前 session、cwd、权限模式
  /help     显示所有可用 slash command
  /model    显示当前模型/provider
  /permissions  显示权限模式 (default/acceptEdits/plan)
  /plan     显示 plan 模式提示
```

注意输出不调模型——`/help` 是纯本地命令，CLI 自己打印消息就结束。

一次性 slash 是本地命令，不进入 `run_once()`，所以这里不会打印 `Agent Code` 头部，也不会创建新 session。

**(b) `/context` 打印运行时状态：**

```bash
$ uv run agent-code "/context"
cwd: /your/project
session: (none)
permission: default
model: anthropic/deepseek-v4-flash
```

**(c) REPL 里也能走 slash：**

```bash
$ uv run agent-code
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

> /help
可用命令：
  /compact  显示 compact 状态
  ...
```

REPL 分支会在进入循环前创建 session，所以你可能会看到 `.agent/sessions/` 多出一个文件；这是正常的。上面的一次性 slash（例如 `uv run agent-code "/context"`）不进入 `run_once()`，所以不会创建新 session。

**(d) 未知 slash 报错：**

```bash
$ uv run agent-code "/unknown"
Unknown command: /unknown
```

v1 把 6 个命令装进了注册表，`cli.py` 里不再有硬编码 `if/elif`。但 slash 只管控制面板——工具调用前后你想插自己的检查逻辑，slash 管不到。下一版做 hooks。

## v2：Hooks — 工具生命周期钩子

v1 的命令面板很好，但你没法在"每次 Agent 改完文件"之后自动做点什么。比如你想让 `file_edit` 之后写一条日志、跑格式化，或者 `bash` 执行前做一次额外的安全检查。

v2 加一个 hooks 系统：定义一个 `hooks.json` 放在项目根目录，里面写"什么事件 + 匹配什么工具 + 跑什么命令"。harness 在工具调用前后查这份配置，匹配到就跑你的命令。

做三件事：新建 `hooks.py` 读配置 + 执行 hook、改 `agent.py` 在工具执行前后插入 hook dispatch、验证 PostToolUse 自动触发。

### 2.1 新建 `agent_code/hooks.py`

hook 系统由三个部分组成：加载配置、匹配 hook、执行 hook。

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# hooks.json 放在 cwd 下，是项目级的钩子配置
HOOKS_FILE = "hooks.json"


def load_hooks(cwd: Path) -> dict[str, list[dict[str, Any]]]:
    """加载 hooks.json。文件不存在返回空 dict——不是错误，只是没配置。"""
    file_path = cwd / HOOKS_FILE
    if not file_path.exists():
        return {}
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
            # 主线使用 {"hooks": {"PostToolUse": [...]}}。
            # 也兼容直接写 {"PostToolUse": [...]}，方便手工调试。
            return data.get("hooks", data)
    except (json.JSONDecodeError, OSError) as exc:
        # 坏 JSON 不阻塞 Agent 启动，打印 warning 后当无配置
        print(f"[hook warning] failed to load {file_path}: {exc}")
        return {}


def _matches(tool_name: str, matcher: str) -> bool:
    """matcher 和 tool_name 的匹配规则：
    - "*" 匹配所有工具
    - 单值精确匹配
    - "a|b" 多值匹配
    不做正则和支持通配符，保持简单可预期。"""
    if matcher == "*":
        return True
    if "|" in matcher:
        return tool_name in matcher.split("|")
    return matcher == tool_name


def _run_hook_command(command: str, input_data: dict[str, Any], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
    """执行一个 hook command。subprocess 跑，stdin 传 JSON，timeout 默认 30s。
    返回 (success, output) 二元组——success 为 True 表示退出码 0。"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            input=json.dumps(input_data, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "hook timed out"
    except Exception as exc:
        return False, f"hook execution error: {exc}"


def run_hooks(
    event: str,
    tool_name: str,
    tool_input: dict[str, Any],
    cwd: Path,
    tool_result: str = "",
) -> list[dict[str, Any]]:
    """在给定 event 下执行所有匹配 tool_name 的 hooks。
    返回一个 list，每个元素是 {"event": ..., "tool": ..., "success": bool, "output": str}。
    空 list 表示没有匹配到 hook。

    这是 harness 的 hook dispatch 入口——agent.py 在工具前后调用本函数。"""
    config = load_hooks(cwd)
    entries = config.get(event, [])
    results: list[dict[str, Any]] = []
    for entry in entries:
        matcher = entry.get("matcher", "*")
        if not _matches(tool_name, matcher):
            continue
        # 支持两种格式："run" 单命令，或 "hooks"[].command 多命令。
        commands: list[str] = []
        if "run" in entry:
            commands = [entry["run"]]
        elif "hooks" in entry:
            for h in entry["hooks"]:
                if isinstance(h, dict) and h.get("type") == "command":
                    cmd = h.get("command", "")
                    if cmd:
                        commands.append(cmd)
        for cmd in commands:
            input_data = {
                "event": event,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_result": tool_result,
                "cwd": str(cwd),
            }
            success, output = _run_hook_command(cmd, input_data, cwd)
            results.append({
                "event": event,
                "tool": tool_name,
                "command": cmd,
                "success": success,
                "output": output,
            })
    return results
```

`run_hooks` 的返回值只是 hook 执行结果列表——不直接决定 permission。PreToolUse 的 handle 交给 agent.py，由 agent.py 根据 `success` 字段决定是否阻断工具。这个边界很重要：hook 执行和 hook 决策分开，agent.py 不能把"工具能不能跑"的决定权完全放给 hook。

顶层配置使用 `{"hooks": {...}}`，和后面 `.agent/cron.json` 一样都属于项目级 harness 配置。单条 hook 支持两种写法：主线用 `"run"`（一行命令），也兼容 `"hooks": [{"type": "command", "command": "..."}]` 这种多命令写法。

hook command 和 bash 一样有副作用能力，而且这里的 `subprocess.run(..., shell=True)` 会继承当前 CLI 进程的环境变量。`PreToolUse` 最好只做读检查或轻量校验，不要在里面改文件；否则用户还没确认工具调用，hook 自己已经把项目改了。教学版先信任项目里的 `hooks.json`，不再给 hook command 套第二层确认；不要在不可信仓库里启用 hook 配置。生产版应该给 hook 加确认、最小环境变量或 sandbox。

### 2.2 改 `agent_code/agent.py`：在工具执行前后插入 hook

**第一处**，顶部 import 追加：

```python
from .hooks import run_hooks  # Day 7：生命周期钩子
```

**第二处**，先把 `emit()` 里的打印改成 `markup=False`。hook 的 observation 里会出现 `[hook]` 这种字面量，如果不关掉 Rich markup，终端会把它当成样式标签吞掉：

```python
    def emit(line: str) -> None:
        # 流式输出 trace：append 给测试用，print 给读者看
        trace.append(line)
        console.print(line, markup=False)
```

**第三处**，在 Agent Loop 的工具执行路径里加两段 hook dispatch。`PreToolUse` 放在 `decision = decide_permission(request)` 之后、文件预览和确认 UI 之前；`PostToolUse` 替换原来的 allow 路径 `tools.run(call, ctx)` 那一段。

PreToolUse——找到 Day 5 写过的这段：

```python
            request = PermissionRequest(
                tool_name=call.name,
                args=call.arguments,
                mode=permission_mode,
                cwd=ctx.cwd,
            )
            decision = decide_permission(request)
```

在它后面追加：

```python
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
```

`PreToolUse` 这里放在 permission 决策之后，但放在真正执行和确认 UI 之前。这样有两个好处：plan 模式已经 deny 的写工具不会再触发本地 hook 副作用；default/acceptEdits 下，hook deny 仍然能在弹确认 UI 或执行工具之前拦掉这次调用。hook allow 不能绕过 permission，因为 permission 决策已经先算好了。代价是：如果 hook command 自己有副作用，它会发生在用户确认 UI 之前，所以 Pre hook 要按"检查器"来写，不要按"执行器"来写。

PostToolUse——找到原来的 allow 路径：

```python
            result = tools.run(call, ctx)
            emit(f"observation: {result.content}")
            tool_result_blocks.append(...)
```

把它替换成下面这版。注意 `PostToolUse` 用 `result.content` 作为 `tool_result`，所以必须放在 `tools.run` 之后：

```python
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
```

`PostToolUse` 的 stdout/stderr 先不喂给模型——只打印到终端。如果后续要让它模型可见（比如模型应该知道"ruff 格式化之后哪些地方变了"），课后挑战再补。

### 2.3 顺手修一个遗留 bug：空 `file_path` 不能当 cwd 读

在跑 2.4 验证前，先把 Day 4 留下来的一个小坑补掉。真实模型偶尔会发出 `file_write {}` 这种缺参数的 tool call，哪怕 `tools.py` 里的 JSON Schema 已经把 `file_path` 标成 required。Schema 是给模型看的说明书，不是 harness 的安全边界；Agent Loop 还是要把工具参数当不可信输入。

不修的话，`path_str = call.arguments.get("file_path", "")` 会拿到空串，`resolve_in_cwd(ctx.cwd, "")` 把空路径解析成 cwd 本身，下一行 `path.read_text()` 就把当前目录当文件读，直接抛 `IsADirectoryError`，CLI 崩掉。

修法只在 `run_agent()` 的 `file_write` / `file_edit` 预览块里，找到这一段：

```python
                path_str = call.arguments.get("file_path", "")
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
```

替换成下面这版。变化只有两处：解析路径前先拦空 `file_path`，解析路径后先拦目录路径。模型给错参数时，CLI 会把错误作为 observation 回灌给模型，让它自己重试：

```python
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
```

### 2.4 跑验证

下面几条验证依赖模型能按 prompt 发出 `read_file`、`file_edit`、`bash` 这些 tool call。如果你的模型环境变量还没配好，provider 报错不代表 hooks 代码坏了；先用 `uv run pytest` 确认导入和基础测试通过，等模型可用后再回来复跑本节。判断是否通过时看关键行为，不要求模型最后一句话逐字一样。

**(a) hooks.json 不存在——行为不变：**

```bash
$ printf 'print("hello")\n' > hook_demo.py
$ uv run agent-code --permission-mode acceptEdits "先读 hook_demo.py，再把 hello 改成 hello day7"
Agent Code
cwd: /your/project
session: f1e2d3c4b5a6

tool_call: read_file {...}
observation: ...
tool_call: file_edit {...}
observation: Edited hook_demo.py: replaced 5 chars with 10 chars
...
```

没 hooks.json 时，`load_hooks(cwd)` 返回空 dict，`run_hooks` 直接返回空列表——行为完全不变。

**(b) 创建 hooks.json——PostToolUse 自动写 hook.log。先创建配置文件：

```bash
$ cat > hooks.json << 'EOF'
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "file_edit", "run": "python3 -c \"import json,pathlib,sys; d=json.load(sys.stdin); pathlib.Path('hook.log').write_text('post '+d['tool_name'])\""}
    ]
  }
}
EOF
```

然后让 Agent 改一个临时 Python 文件。这里用 `--permission-mode acceptEdits` 跳过 Day 5 的确认 UI，但 prompt 仍然要求"先读再改"，这样 Day 4 的 read-before-edit 校验能通过：

```bash
$ printf 'print("hello")\n' > hook_demo.py
$ uv run agent-code --permission-mode acceptEdits "先读 hook_demo.py，再把 hello 改成 hello hook"
Agent Code
cwd: /your/project
session: a1b2c3d4e5f6

tool_call: read_file {...}
observation: ...
tool_call: file_edit {...}
observation: Edited hook_demo.py: replaced 5 chars with 10 chars
hook: PostToolUse file_edit ok

final: 已更新 hook_demo.py。
```

`hook: PostToolUse file_edit ok` 这一行证明 hooks.json 被 `agent.py` 读到了、`file_edit` 匹配到了、hook command 执行成功了。

再看 hook 写出的文件：

```bash
$ cat hook.log
post file_edit
```

真实项目里想自动格式化，可以把 `run` 换成 `uvx ruff format .` 或项目里已有的 `ruff format .`。主线用标准库命令，是为了让验证不依赖额外安装。

**(c) PreToolUse 阻断特定 bash。创建一个只读检查型 PreToolUse hook——在 `bash` 执行前检查命令是否包含 `BLOCK_ME`：

```bash
$ cat > hooks.json << 'EOF'
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "bash", "run": "python3 -c \"import json,sys; d=json.load(sys.stdin); cmd=d.get('tool_input',{}).get('command',''); sys.exit(1 if 'BLOCK_ME' in cmd else 0)\""}
    ]
  }
}
EOF

$ uv run agent-code --permission-mode acceptEdits "用 bash 跑 echo BLOCK_ME"
...
tool_call: bash {'command': 'echo BLOCK_ME'}
observation: tool blocked by PreToolUse hook:
  [hook] python3 -c ...: ...
```

PreToolUse hook 非 0 退出码转化成了 `tool_blocked`，Agent 收到 error observation，知道"你的操作被拦截了"。

v2 让工具调用前后有了项目的自定义拦截点。现在 `agent-code` 只能在项目目录里 `uv run`，不方便。下一版让它全局可用。

## v3：全局安装

这一版不写代码——只补一段配置和两条命令。

v1-v2 一直在项目目录底下 `uv run agent-code`。你想到别的项目里用 `agent-code`，就要么 cd 到这个项目目录、要么每次都打完整路径。uv 的 `tool install` 可以把它装成全局命令。

### 3.1 检查 `[project.scripts]`

打开 `pyproject.toml`，确认 `[project.scripts]` 还是 Day 1 的入口。如果你的文件里没有这一段，现在补上：

```toml
[project.scripts]
agent-code = "agent_code.cli:main"
```

`agent_code.cli:main` 指向 `cli.py` 里的 `main()` 函数；`main()` 再调用 Typer app。uv 会把它包装成一个 shell 入口，装到 tool 环境的 bin 目录。

### 3.2 安装并验证

```bash
$ uv tool install -e .
Installed 1 executable: agent-code

$ agent-code --help
Usage: agent-code [OPTIONS] [PROMPT] COMMAND [ARGS]...
...
Options:
  --cwd
  --provider
  --model
  --base-url
  --max-steps
  --permission-mode
  --resume
  --continue
  --help
```

不同 Typer 版本的 help 文案、列宽和中英文提示可能略有差异；有的版本会显示 `COMMAND [ARGS]...`，有的不会。这里验证的不是排版，而是全局命令能被 shell 找到，并且 Day 5/Day 6/Day 7 需要的 option 都还在。

### 3.3 在任意目录测试

```bash
$ cd /tmp
$ agent-code --cwd /your/project "/context"
cwd: /your/project
session: (none)
permission: default
model: anthropic/deepseek-v4-flash
```

`--cwd` 参数控制工作目录，所有文件操作、session JSONL、AGENT.md、memdir、hooks.json 的查找都基于 `cwd`，不是你 shell 的当前目录。如果你省略 `--cwd`，默认值是 `.`——即你当前 shell 所在目录。

注意：`hooks.json` 的查找也在 `cwd` 下。如果你在 `/tmp` 跑 `agent-code` 且没传 `--cwd`，它会在 `/tmp/hooks.json` 找 hook 配置。一般用 `--cwd` 指向你的项目根目录就能解决。

全局安装之后，日常使用就是 `agent-code "prompt"`，不用每次都 `cd` 到项目目录。

v3 的全局安装让 `agent-code` 脱离了"项目源码目录"的束缚。但 REPL 还只能响应当前的你——你不能让它"每隔 5 分钟自动检查一下 PR 状态"。v4 做这件事。

## v4：Cron `/loop` 定时自唤醒

v3 之后 `agent-code` 可以在任意目录跑了。但 REPL 还是"你说话它才动"——你不能注册一个定时任务让它自己按间隔去查东西。

v4 加一个调度器：注册一条 slash/prompt + 执行间隔 → 后台线程到点把这次 prompt 排进 Agent Loop。新建两个文件（`scheduler.py` + `cron_tools.py`），改四个老文件：`tools.py` 注册 cron 工具，`permissions.py` 补权限分类，`cli.py` 管理 scheduler 生命周期，`slash.py` 增加 `/loop` 本地命令。

### 4.1 新建 `agent_code/scheduler.py`

先定义 CronJob——一个定时任务包含的字段——然后是调度器类：管理 job 列表、持久化到 `.agent/cron.json`、后台线程定时唤醒。

```python
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any


class CronJob:
    """一个定时任务。id 是 12 位 hex，slash 是到点要重放的命令/prompt。"""

    def __init__(
        self,
        job_id: str,
        slash: str,
        every_seconds: int,
        label: str = "",
        last_run_at: str | None = None,
        created_at: str | None = None,
    ) -> None:
        self.id = job_id
        self.slash = slash                # 到点要重放的 slash 或 prompt
        self.every_seconds = every_seconds  # 执行间隔（秒）
        self.label = label                # 人类可读标签
        self.last_run_at = last_run_at
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()


# cron 持久化文件，放在 .agent/ 下和 sessions/memory 同级
_CRON_FILE = ".agent/cron.json"


def _cron_path(cwd: Path) -> Path:
    """返回 .agent/cron.json 路径，自动创建 .agent/ 目录。"""
    agent_dir = cwd / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    return agent_dir / "cron.json"


def _load_jobs(cwd: Path) -> list[CronJob]:
    """从 .agent/cron.json 加载持久化 job 列表。文件不存在或损坏返回 []。"""
    fpath = _cron_path(cwd)
    if not fpath.exists():
        return []
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    jobs: list[CronJob] = []
    for item in data.get("jobs", []):
        try:
            jobs.append(CronJob(
                job_id=item["id"],
                slash=item["slash"],
                every_seconds=item["every_seconds"],
                label=item.get("label", ""),
                last_run_at=item.get("last_run_at"),
                created_at=item.get("created_at"),
            ))
        except (KeyError, TypeError):
            continue  # 跳过损坏的 job，不让一条坏数据阻塞调度器
    return jobs


def _save_jobs(cwd: Path, jobs: list[CronJob]) -> None:
    """把当前 job 列表序列化到 .agent/cron.json。"""
    fpath = _cron_path(cwd)
    data = {
        "jobs": [
            {
                "id": j.id,
                "slash": j.slash,
                "every_seconds": j.every_seconds,
                "label": j.label,
                "last_run_at": j.last_run_at,
                "created_at": j.created_at,
            }
            for j in jobs
        ]
    }
    fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class CronScheduler:
    """REPL 内的 cron 调度器。维护 job 列表 + 后台 daemon thread + pending queue。

    harness 边界：
    - 后台线程只负责到点把 prompt 放进 pending queue，绝不直接调用 run_agent
    - REPL 主循环在每次 run_once 返回后 drain queue，把待处理 prompt 作为新一轮用户输入
    - 调度器只在 REPL 模式激活；一次性模式不创建后台线程
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._jobs: list[CronJob] = _load_jobs(cwd)
        self._pending: Queue[str] = Queue()     # 到点排队的 prompt
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # --- job 管理 ---
    def add_job(self, slash: str, every_seconds: int, label: str = "") -> CronJob:
        """添加一个 cron job。id 自动生成。"""
        jid = uuid.uuid4().hex[:12]
        job = CronJob(job_id=jid, slash=slash, every_seconds=every_seconds, label=label)
        with self._lock:
            self._jobs.append(job)
            _save_jobs(self.cwd, self._jobs)
        return job

    def list_jobs(self) -> list[CronJob]:
        with self._lock:
            return list(self._jobs)

    def cancel_job(self, jid: str) -> bool:
        """按 id 取消一个 job。返回 True 表示找到并删除。"""
        with self._lock:
            for i, j in enumerate(self._jobs):
                if j.id == jid:
                    self._jobs.pop(i)
                    _save_jobs(self.cwd, self._jobs)
                    return True
        return False

    # --- pending queue ---
    def drain_pending(self) -> list[str]:
        """取出当前 pending queue 里所有等待重放的 prompt。调用一次就排空。"""
        items: list[str] = []
        while not self._pending.empty():
            try:
                items.append(self._pending.get_nowait())
            except Exception:
                break
        return items

    # --- 后台调度循环 ---
    def _loop(self) -> None:
        """后台线程主循环。每 1 秒 tick 一次：
        - 检查哪些 job 的上次执行时间距今超过 every_seconds
        - 到点的 job 把 slash 放进 pending queue
        - 更新 last_run_at，写回持久化文件
        """
        while not self._stop_event.is_set():
            self._stop_event.wait(1.0)  # 每 1 秒醒来一次
            if self._stop_event.is_set():
                break
            now_ts = datetime.now(timezone.utc).timestamp()
            dirty = False
            with self._lock:
                for job in self._jobs:
                    baseline = job.last_run_at or job.created_at
                    last_ts = 0.0
                    if baseline:
                        try:
                            last_dt = datetime.fromisoformat(baseline)
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=timezone.utc)
                            last_ts = last_dt.timestamp()
                        except ValueError:
                            pass
                    if now_ts - last_ts >= job.every_seconds:
                        self._pending.put(job.slash)
                        job.last_run_at = datetime.now(timezone.utc).isoformat()
                        dirty = True
                if dirty:
                    _save_jobs(self.cwd, self._jobs)

    def start(self) -> None:
        """启动后台调度线程（daemon，随主进程退出自动回收）。"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止调度器。不 join——daemon thread 会在主线程退出时回收。"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
```

`CronScheduler` 的四个关键设计选择：
- **后台线程只 enqueue，不调 Agent Loop**。在另一个线程里直接调 `run_agent` 会导致并发问题——工具执行中突然插进另一条 prompt，cwd 下的文件状态不受控。把 pending prompt 放进 `Queue`，由 REPL 主循环在每轮结束后 drain，保证 Agent Loop 是单线程顺序执行的。
- **间隔用 `every_seconds` 而不用 cron 表达式**。教学版避免 5 字段 cron 解析，用一个整数秒数足够建立"定时自唤醒"的心智模型。想用 cron 表达式的话课后挑战补。
- **持久化是写全量 JSON**。每次 add/cancel/到点都重写一次 `.agent/cron.json`。job 数量不会很大（一般 ≤ 10 条），全量写比增量 patch 简单、出错后可恢复。
- **同进程加锁**。后台线程会遍历 `_jobs`，主线程可能同时 add/cancel，所以这里用 `threading.Lock` 保护 job 列表和写文件。

### 4.2 新建 `agent_code/cron_tools.py`

三个 cron 工具——`cron_create`、`cron_list`、`cron_cancel`——是模型可用的一等工具。REPL 模式下，它们复用 `cli.py` 创建并启动的 `CronScheduler`；一次性模式下，它们临时打开同一份 `.agent/cron.json` 做 add/list/cancel，但不启动后台线程。

```python
from __future__ import annotations

from typing import Any

from .tools import ToolContext


# 全局 scheduler 单例——由 cli.py 在启动 REPL 时设置
_scheduler: Any = None


def set_scheduler(scheduler: Any) -> None:
    """cli.py 在创建 CronScheduler 后调用这个函数，让工具函数能访问同一个实例。"""
    global _scheduler
    _scheduler = scheduler


def _get_scheduler(ctx: ToolContext) -> Any:
    """REPL 里复用正在运行的 scheduler；一次性模式临时读写 cron.json。"""
    if _scheduler is not None:
        return _scheduler
    from .scheduler import CronScheduler

    return CronScheduler(ctx.cwd)


def cron_create(args: dict[str, Any], ctx: ToolContext) -> str:
    """创建一条 cron job——工具函数只做薄包装。"""
    scheduler = _get_scheduler(ctx)
    slash = args.get("slash", "")
    every_seconds = int(args.get("every_seconds", 0))
    label = args.get("label", "")
    if not slash:
        return "error: missing required argument 'slash'"
    if every_seconds <= 0:
        return "error: every_seconds must be positive"
    job = scheduler.add_job(slash, every_seconds, label)
    return f"Cron job created: {job.id} — every {every_seconds}s: {slash}"


def cron_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """列出当前所有 cron job。"""
    scheduler = _get_scheduler(ctx)
    jobs = scheduler.list_jobs()
    if not jobs:
        return "(no cron jobs)"
    lines = []
    for j in jobs:
        last = j.last_run_at or "never"
        label = f" — {j.label}" if j.label else ""
        lines.append(f"  [{j.id}] every {j.every_seconds}s: {j.slash}{label}  (last: {last})")
    return "\n".join(lines)


def cron_cancel(args: dict[str, Any], ctx: ToolContext) -> str:
    """取消一条 cron job。"""
    scheduler = _get_scheduler(ctx)
    jid = args.get("id", "")
    if not jid:
        return "error: missing required argument 'id'"
    if scheduler.cancel_job(jid):
        return f"Cron job cancelled: {jid}"
    return f"error: job not found: {jid}"
```

`cron_create` 的参数名是 `slash`——可以是一条 slash command（`/context`），也可以是一段自然语言 prompt（`"检查 git status 并汇报"`）。到点 scheduler 直接把这个字符串放进 pending queue，CLI 主循环再把它当用户输入传给 Agent Loop。

### 4.3 改 `agent_code/tools.py`：注册 cron_* 工具

在 `default_tools()` 函数里，`return registry` 之前追加三个工具注册。权限上分两类：`cron_list` 是纯读，进 `_READONLY_TOOLS`；`cron_create` / `cron_cancel` 会写 `.agent/cron.json`，但写入范围固定，进 `_LOW_RISK_WRITES`。这样 default / acceptEdits 自动放行，plan 模式仍然 deny。

```python
    # --- Day 7：Cron 定时任务工具 ---
    from .cron_tools import cron_create, cron_list, cron_cancel

    registry.register(
        Tool(
            name="cron_create",
            description=(
                "Create a recurring cron job. The job will re-run the given slash/prompt "
                "every N seconds. Use for periodic checks like PR status polling."
            ),
            run=cron_create,
            parameters={
                "type": "object",
                "properties": {
                    "slash": {"type": "string", "description": "Slash command or prompt to run."},
                    "every_seconds": {"type": "integer", "description": "Interval in seconds."},
                    "label": {"type": "string", "description": "Optional human-readable label."},
                },
                "required": ["slash", "every_seconds"],
            },
        )
    )
    registry.register(
        Tool(
            name="cron_list",
            description="List all active cron jobs with their IDs, intervals, and last-run times.",
            run=cron_list,
            parameters={"type": "object", "properties": {}, "required": []},
        )
    )
    registry.register(
        Tool(
            name="cron_cancel",
            description="Cancel a cron job by its ID.",
            run=cron_cancel,
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Cron job ID to cancel."},
                },
                "required": ["id"],
            },
        )
    )
```

同时改 `agent_code/permissions.py`。先把 `cron_list` 加进 `_READONLY_TOOLS`：

```python
_READONLY_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep", "project_tree",
    "git_status", "git_diff",
    "system_date", "echo",
    "memory_recall",
    "cron_list",  # Day 7：列出 cron job 是纯读
})
```

再把 `cron_create` / `cron_cancel` 加进 Day 6 已经建好的 `_LOW_RISK_WRITES`：

```python
_LOW_RISK_WRITES = frozenset({
    "memory_write",
    "cron_create",
    "cron_cancel",
})
```

不要把 `cron_create` / `cron_cancel` 加进 `_READONLY_TOOLS`。它们会修改 `.agent/cron.json`，只是写入范围很小，所以属于低风险写；plan 模式上方的 deny 分支仍然会拦掉它们。

### 4.4 改 `agent_code/cli.py`：REPL 启动 scheduler + drain pending queue

**第一处**，顶部 import 追加：

```python
import threading
from queue import Empty, Queue

from click.exceptions import Abort

from .scheduler import CronScheduler  # Day 7：cron 调度器
from .cron_tools import set_scheduler  # Day 7：让 cron 工具访问同一个 scheduler
```

**第二处**，REPL 分支——在 `session` 创建之后、输入循环之前，创建并启动 scheduler：

```python
    # Day 7：REPL 模式启动 cron scheduler；一次性模式不启动
    scheduler = CronScheduler(resolved_cwd)
    set_scheduler(scheduler)
    scheduler.start()
```

**第三处**，把 REPL 原来的主线程 `typer.prompt(">")` 循环替换成输入线程 + 主线程轮询。原来的循环大概长这样：

```python
    while True:
        line = typer.prompt(">").strip()
        ...
        run_user_input(line)
```

替换成：

```python
    # Day 7：输入线程只负责把用户输入放进队列；主线程负责跑 Agent Loop。
    input_queue: Queue[str | None] = Queue()
    stop_repl = threading.Event()

    def _read_input() -> None:
        while not stop_repl.is_set():
            try:
                line = typer.prompt(">").strip()
            except (KeyboardInterrupt, EOFError, Abort):
                input_queue.put(None)
                return
            input_queue.put(line)

    input_thread = threading.Thread(target=_read_input, daemon=True)
    input_thread.start()

    try:
        while True:
            # 即使用户没有敲下一行，主线程也会定期检查 cron pending queue。
            for pp in scheduler.drain_pending():
                console.print(f"[dim]cron: running scheduled job → {pp}[/dim]")
                run_user_input(pp)

            try:
                line = input_queue.get(timeout=0.5)
            except Empty:
                continue

            if line is None:
                break
            if not line:
                continue
            if line == "/exit":
                console.print("Bye.")
                break
            run_user_input(line)
    finally:
        stop_repl.set()
        scheduler.stop()
```

这里主线程每 0.5 秒醒一次，先 drain cron pending，再处理用户输入。后台 scheduler 仍然只 enqueue，不直接跑 Agent Loop；Agent Loop 仍然只在主线程顺序执行。

### 4.5 注册 `/loop` slash command

在 `slash.py` 里加一个本地 command：`/loop`。它根据第一个参数继续分派到 `add` / `list` / `cancel` 三个子命令。它们和 `cron_*` 工具共享同一个 CronScheduler（通过 `cron_tools.py` 的全局单例）。

```python
def _cmd_loop_add(args: list[str], ctx: SlashContext) -> SlashResult:
    """本地 /loop add：直接调 cron_create 的函数逻辑，不用绕模型。"""
    from .cron_tools import cron_create
    from .tools import ToolContext

    if not args:
        return SlashResult(handled=True, message="用法: /loop add <slash或prompt> --every <60s|5m|2h> --label <标签>")
    # 简单解析：参数以 -- 开头的是选项，其余拼成 slash
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
    """管理 cron 定时任务：/loop add/list/cancel。"""
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


# 在 slash.py 底部追加注册
register("loop", "管理 cron 定时任务: add/list/cancel", _cmd_loop)
```

这段放在 v1 完整文件里 `register("plan", ...)` 那行之后。到 v4 为止，`slash.py` 底部注册块应该长这样：

```python
register("help", "显示所有可用 slash command", _cmd_help)
register("model", "显示当前模型/provider", _cmd_model)
register("context", "显示当前 session、cwd、权限模式", _cmd_context)
register("compact", "显示 compact 状态", _cmd_compact)
register("permissions", "显示权限模式 (default/acceptEdits/plan)", _cmd_permissions)
register("plan", "显示 plan 模式提示", _cmd_plan)
register("loop", "管理 cron 定时任务: add/list/cancel", _cmd_loop)
```

`/loop add` 的设计选择：它是本地 slash——直接调 `cron_create` 函数注册到 scheduler，不走模型。和 `cron_create` 工具的区别是：人类用 `/loop add` 直接操作，模型用 `cron_create` 工具调同一套 scheduler。两条路径共享同一个 `CronScheduler` 实例和同一份 `.agent/cron.json` 持久化。

这里的 cron 是 REPL 内调度器，不是操作系统的 crontab，也不是后台守护进程。`.agent/cron.json` 负责记住 job 配置；真正“到点 enqueue”只发生在 `agent-code` REPL 进程还活着、scheduler 已经启动的时候。退出 REPL 后不会继续执行，到下次进入 REPL 才会重新加载这些 job。

### 4.6 跑验证

**(a) `/loop add` 创建定时任务：**

```bash
$ uv run agent-code
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

> /loop add /context --every 120 --label 上下文检查
Cron job created: f7e8d9c0a1b2 — every 120s: /context

> /loop list
  [f7e8d9c0a1b2] every 120s: /context — 上下文检查  (last: never)
>
```

每 2 分钟后会看到：

```
cron: running scheduled job → /context
```

调度器到点把 `/context` 放进 pending queue，主循环 drain 后调 `run_user_input("/context")`——slash dispatch 再拦截它，执行 `_cmd_context`，打印运行时状态。整个过程不经过模型。

这一步最好在真实交互 REPL 里看。用 `printf` 管道做自动化验证也可以，但 prompt 可能会挤成 `>: >:` 这种样子；只要能看到 `cron: running scheduled job → /context` 和 `/context` 的输出，就说明 pending queue 通了。

**(b) 查看 `.agent/cron.json`。**另开一个终端，或者先在 REPL 输入 `/exit` 退出，再跑：

```bash
$ cat .agent/cron.json
{
  "jobs": [
    {
      "id": "f7e8d9c0a1b2",
      "slash": "/context",
      "every_seconds": 120,
      "label": "上下文检查",
      "last_run_at": "2026-05-27T14:02:00.123456+00:00",
      "created_at": "2026-05-27T14:00:00.000000+00:00"
    }
  ]
}
```

**(c) `/loop cancel` 删除任务：**

```bash
> /loop cancel f7e8d9c0a1b2
Cron job cancelled: f7e8d9c0a1b2

> /loop list
(no cron jobs)
```

**(d) 模型通过工具创建 cron job。**这条验证要在 REPL 里跑，因为一次性模式不会启动 scheduler。让模型自己对调度器操作，演示 `cron_create` 工具真的在工具池里、权限引擎认得它、scheduler 收到后持久化：

```bash
$ uv run agent-code
Agent Code
cwd: /your/project

> 帮我设一个定时任务，每 3 分钟跑一次 /context，标签叫'定期检查'
Agent Code
cwd: /your/project
session: c1d2e3f4a5b6

tool_call: cron_create {'slash': '/context', 'every_seconds': 180, 'label': '定期检查'}
observation: Cron job created: a7b8c9d0e1f2 — every 180s: /context
final: 已创建定时任务 a7b8c9d0e1f2，每 3 分钟自动运行 /context。
```

这条工具调用在 default 模式下不会弹确认窗，因为前面已经把 `cron_create` 放进 `_LOW_RISK_WRITES`。如果用 `--permission-mode plan` 启动，`decide_permission` 会在 plan 分支直接 deny，模型不能偷偷注册定时任务。

v4 的 cron 调度器让 Agent 不只对当前输入响应——还能对时间响应。到此为止，前 7 天的单 Agent CLI 完整了。

## 收尾：今天的终版文件改动清单

| 文件 | 改动 |
|---|---|
| `agent_code/slash.py` | 新文件：`SlashCommand`、`SlashContext`、`SlashResult`、`_registry`、`dispatch_slash`；6 个内置命令 handler；`/loop` 子命令注册 |
| `agent_code/hooks.py` | 新文件：`load_hooks`（读 hooks.json）、`_matches`（matcher 规则）、`_run_hook_command`（subprocess 执行）、`run_hooks`（dispatch 入口） |
| `agent_code/scheduler.py` | 新文件：`CronJob` 数据类、`CronScheduler`（job 管理 + 后台 daemon thread + pending queue + `.agent/cron.json` 持久化） |
| `agent_code/cron_tools.py` | 新文件：`cron_create` / `cron_list` / `cron_cancel` 工具函数 + `set_scheduler` 全局单例注入 |
| `agent_code/agent.py` | 工具执行前后插入 `run_hooks("PreToolUse", ...)` / `run_hooks("PostToolUse", ...)`；PreToolUse 失败阻断工具 |
| `agent_code/cli.py` | 删除 `handle_slash` 硬编码；REPL 和一次性模式全走 `run_user_input`；REPL 创建 `CronScheduler`，输入线程读命令，主线程轮询 pending queue |
| `agent_code/tools.py` | `default_tools()` 注册 `cron_create` / `cron_list` / `cron_cancel` |
| `agent_code/permissions.py` | `cron_list` 加进 `_READONLY_TOOLS`；`cron_create` / `cron_cancel` 加进 `_LOW_RISK_WRITES`，plan 仍 deny |
| `pyproject.toml` | 确认 `[project.scripts]` 仍是 `agent-code = "agent_code.cli:main"` |

## 手动 trace 一遍

### 路径一：slash dispatch

```txt
用户输入："/context"
1. CLI REPL 读到 "/context"，调 dispatch_slash("/context", ctx)。
2. dispatch_slash 去掉 /，取 "context" 作为 command name。
3. 查 _registry["context"] → _cmd_context handler。
4. _cmd_context 从 SlashContext 取 session_id、permission_mode、model、provider。
5. 返回 SlashResult(handled=True, message="cwd: /project\nsession: a1b...")。
6. CLI 看到 handled=True → 打印 message → continue。
全程不调模型、不写文件、不消耗 API token。
```

### 路径二：hook 拦截

```txt
Agent Loop 中工具执行阶段：
1. Agent 返回 tool_use: file_edit {path:"cli.py", old_string:"...", new_string:"..."}。
2. Agent Loop 构造 PermissionRequest 并调用 decide_permission。
3. 如果 decision 是 deny，直接返回 error observation，不执行 PreToolUse hook。
4. 如果 decision 不是 deny，再调 run_hooks("PreToolUse", "file_edit", arguments, cwd)。
5. hooks.py 读 hooks.json，找 PreToolUse 下 matcher 匹配 file_edit 的条目。
6. 匹配到 {"matcher":"file_edit","run":"python3 -c ..."} → subprocess 执行。
7. hook command 退出码 0 → run_hooks 返回 [{"success":True, ...}]。
8. pre_blocked 为空 → 继续走确认 UI 或 allow 路径，最后 tools.run。
9. tools.run 成功后调 run_hooks("PostToolUse", "file_edit", arguments, cwd, tool_result=result.content)。
10. hooks.py 匹配到 PostToolUse 下的 hook command → subprocess 执行。
11. hook command 退出码 0 → 终端打印 "hook: PostToolUse file_edit ok"。
12. tool_result 回灌给模型，Agent 继续推理。
```

### 路径三：cron 定时自唤醒

```txt
1. CLI REPL 启动时：CronScheduler(cwd) → 从 .agent/cron.json 加载已有 jobs。
2. scheduler.start() → 后台 daemon thread 每 1 秒检查一次。
3. 用户输入 "/loop add /context --every 120" → _cmd_loop_add → scheduler.add_job(...)。
4. scheduler.add_job：append jobs 列表 → _save_jobs(cwd, jobs) 写回 .agent/cron.json。
5. 120 秒后：后台线程发现 now - last_run_at >= 120 → self._pending.put("/context")。
6. 更新 job.last_run_at → _save_jobs(cwd, jobs) 写回文件。
7. REPL 主线程每 0.5 秒轮询 scheduler.drain_pending()。
8. drain 拿到 ["/context"] → console.print "cron: running scheduled job" → run_user_input("/context")。
9. "/context" 重新进入程序顶部的 slash dispatch（路径一）。
10. 如果一次性模式跑 agent-code，调度器不启动——用户退出了就没了。
```

## 今天有了什么

- **Slash 注册表**：6 个内置命令（`/help`、`/model`、`/context`、`/compact`、`/permissions`、`/plan`）通过 `slash.py` 统一注册和 dispatch，不再硬编码在 `cli.py`。`/compact` 先显示 Day 6 自动压缩状态，不手动重写 session 历史。`SlashResult.should_query` 区分"本地控制命令"和"转模型 prompt 命令"。
- **Hooks 生命周期拦截**：`hooks.json` 放在 cwd 下，`PreToolUse` 在工具执行前能阻断，`PostToolUse` 在工具成功后运行只告警不阻断。教学版只支持 `command` hook + 精确匹配/multi-value matcher，不做正则和 HTTP/agent hooks。
- **全局安装**：`uv tool install -e .` 让 `agent-code` 在任意目录可用，`--cwd` 控制工作目录。
- **Cron 定时自唤醒**：`CronScheduler` 管理 job 列表 + 后台 daemon thread + pending queue。job 到点只 enqueue，不重入 Agent Loop。`.agent/cron.json` 持久化，REPL 启动恢复，一次性模式不启动调度器。
- **`/loop` 人类控制面板**：`/loop add/list/cancel` 是本地 slash，直接操作 scheduler。`cron_create/list/cancel` 是模型工具，让 Agent 在推理时也能注册定时任务。两条路径共享同一套 scheduler 实例和持久化。

## 常见问题

### `/permissions` 和 `/plan` 切换后没生效

v1 的 `/permissions` 和 `/plan` 只打印信息，不真正修改 `cli.py` 里的 `permission_mode` 变量。要切换权限模式，目前需要退出 REPL 重新用 `--permission-mode plan` 启动。让 slash 改运行态变量需要传入可变对象或 callback，这个留给课后挑战。

### hooks.json 配好了但 hook 没触发

三个常见原因：(1) `hooks.json` 没放在 cwd 下——`agent-code --cwd /path/to/project` 时 hook 文件在 `--cwd` 指的项目根目录找；(2) matcher 写错——`"file_edit"` 不是 `"FileEdit"`，matcher 做精确字符串匹配；(3) hook command 本身有问题——先用命令行单独跑一下 hook 命令确认它能正常退出 0。

### cron job 到点没自动跑

确认四件事：(1) 你在 REPL 模式（`agent-code` 不加 prompt），一次性模式（`agent-code "prompt"`）不启动 scheduler；(2) REPL 进程还活着，退出后 `.agent/cron.json` 只保留配置，不会继续后台执行；(3) `/loop list` 看到 job 存在且 `last_run_at` 在更新——如果不更新说明后台线程可能没启动成功；(4) job 刚创建，`last_run_at` 还是 `never`，需要等到 `every_seconds` 秒过去才会第一次触发。

## 课后挑战

1. **让 `/permissions` 和 `/plan` 真正切换运行态模式**：给 `SlashContext` 加一个 callback 字段或让 handler 返回 `new_permission_mode` 字段，`cli.py` 在收到 slash 结果后更新循环里的 `permission_mode` 变量。

2. **hook 输出注入模型上下文**：修改 `agent.py` 的 PostToolUse 处理——如果 hook 的 stdout 非空，把它作为一条 `additional_context` block 插进 `tool_result` 后面，让模型知道 hook 做了什么。这样模型能在格式化、lint 或日志脚本执行后看到额外上下文。

3. **支持 cron 5 字段表达式**：给 `CronJob` 增加 `cron` 字段，让 `/loop add` 可以接 `--cron "*/5 * * * *"`。主线先用 `every_seconds`，是为了把注意力放在"到点 enqueue，不重入 Agent Loop"这个边界上。

4. **给 cron job 加最大数量限制**：在 `CronScheduler.add_job()` 里限制最多 20 条任务，超过就返回错误。这样可以避免模型误创建大量定时任务，把 `.agent/cron.json` 刷满。

## 思考题

1. **slash command 为什么不等于工具？** （提示：slash 的 handler 能不能调模型？能不能写文件？能不能读 session 状态？`SlashResult.should_query` 这个字段是要解决什么问题？）
2. **`PreToolUse` hook 和 `decide_permission` 的先后关系为什么重要？** （提示：如果 hook deny 之后 permission 层还弹确认窗，用户体验是什么样？反过来，如果 hook allow 绕过了 plan 模式的 deny，安全边界在哪？）
3. **`CronScheduler` 为什么不在后台线程直接调 `run_agent`？** （提示：工具读写文件——如果主线程 Agent Loop 正在 `file_edit`，另一个线程突然 `bash git checkout`，磁盘状态会怎样？）
4. **全局安装后 `--cwd` 影响哪些文件？** （提示：列出 Day 6 的 session/memdir/AGENT.md + Day 7 的 hooks.json/cron.json——它们的查找起点是什么？如果你在 `/tmp` 跑 `agent-code --cwd /project`，`hooks.json` 在 `/tmp` 还是 `/project` 下找？）

## 下一天

今天给 harness 加了三个扩展面：slash 运行时控制、hooks 生命周期拦截、cron 定时自唤醒。单 Agent CLI 的 7 天基础建设到这里完成——一个完整的 CLI 模型 Agent：会读代码、改文件、跑命令、有权限、能记住、能扩展。

下一天开始后 7 天的"升级成完整 harness"段——**Day 8：TodoWrite + Plan Mode 闭环**。Day 5 的 plan 模式给出了"只读硬约束"，Day 8 把 `exit_plan_mode` 拦截 + 计划渲染 + 用户批准接进来，让 Agent 在动手改代码之前先写 todo、交给用户审视、批准后才开始真正执行。
