# Day 5：Bash 工具 + 权限引擎

Day 4 让 Agent 能安全改文件了。每次写盘前，harness 出 diff、让你按 y/N、备份旧内容——一套完整的读写保护链。

但 Agent 现在还不能跑命令。模型想跑个 `uv run pytest` 看测试结果、跑个 `git status` 看仓库状态——做不到。Day 3 的 `grep` 是在 harness 内部用 Python 实现的，不是让模型调 shell。

今天给 Agent 加上命令执行能力：`bash` 工具。模型可以调 `bash` 跑任意 shell 命令，但运行前要过一道权限门——危险命令拦截、命令预览确认、超时控制、输出截断。这道门就是权限引擎，它把 Day 4 的 y/N 确认从"edit 专用"升级成"所有危险工具通用的决策层"。

同时加两个配套能力：`ask_user_question` 让模型能在需要你拍板时主动停下来问你结构化问题；`bash(background=True)` 让长命令在后台跑，不阻塞 Agent Loop。

跑完之后你会看到：

- `bash` 工具执行 shell 命令，终端先打印命令预览，你确认后才执行
- `git status` 和 `git diff` 作为只读工具默认放行，不弹确认
- 危险命令（`rm -rf`、`sudo`、`git push` 等）被权限引擎直接拒绝
- `--permission-mode acceptEdits` 跳过文件编辑确认，`plan` 模式拒绝所有写操作
- 模型调 `ask_user_question` 弹出一个 numbered menu 让你选
- `bash(background=True)` 启动后台任务，模型拿到 `background_id` 后可以继续干别的

代码约 300 行新增，改动 4 个老文件，新增 4 个文件。

## 起手：今天的起点

从 Day 4 的 `agent-code` 项目继续改。不需要新依赖——`subprocess`、`threading`、`re` 都是标准库。

今天新增 4 个文件、改 3 个老文件、删 1 个老文件。

新增：

```txt
agent_code/bash_runner.py    bash 同步执行：subprocess + timeout + 输出截断
agent_code/permissions.py    权限引擎：PermissionRequest + PermissionDecision + decide_permission()
agent_code/bg_manager.py     后台 bash：Popen + 写 .bg/<id>.out/.err
agent_code/prompt_ui.py      用户交互：从 diff_ui 迁入 confirm_edit/render_diff，加选项菜单
```

改动：

```txt
agent_code/tools.py          新增 bash / git_status / git_diff / ask_user_question 工具
agent_code/agent.py          把拦截块从"edit 专用"升级成统一 permission gate
agent_code/cli.py            加 --permission-mode 选项
```

删除：

```txt
agent_code/diff_ui.py        实现搬到 prompt_ui.py。v1 先改成 re-export 垫片让老 import 还能跑，v2 切完 import 一并删掉
```

今天分四步走。v1 先把 bash 跑通，v2 把确认逻辑抽成权限引擎，v3 加 ask_user_question，v4 接后台执行。

## v1：bash 同步执行 + 命令预览 + y/N 确认

先把最直接的场景跑通：模型想跑一条命令，harness 把命令打印出来让你看过，你按 y 才执行。

和 Day 4 v1 的 `file_write` 思路一样——拦截在 `tools.run` 之前，先预览再确认。但 bash 有个关键区别：命令越界检查靠的不是 `resolve_in_cwd`（命令是 shell 字符串，不是文件路径），而是 `subprocess.run(cwd=ctx.cwd)` 把进程锁在项目目录里。

### 1.1 新建 `agent_code/bash_runner.py`

bash 执行抽象成一个独立模块。工具函数只做薄包装，实际执行逻辑在 runner 里——这样权限引擎和工具函数都能复用它。

```python
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .fs_safety import truncate_output

# bash 执行的环境变量最小集合——不让宿主机的敏感变量漏进子进程
_MINIMAL_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", ""),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", "/bin/bash"),
}


def run_sync(command: str, cwd: Path, timeout: int = 30) -> str:
    """同步执行 shell 命令。cwd 锁定在项目目录，超时后杀进程。"""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            env=_MINIMAL_ENV,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"

    output = proc.stdout.decode("utf-8", errors="replace")
    if proc.stderr:
        stderr_text = proc.stderr.decode("utf-8", errors="replace")
        if stderr_text.strip():
            output += "\n[stderr]\n" + stderr_text

    # 统一截断，防止长输出撑爆上下文
    truncated = truncate_output(output.strip(), max_chars=12000)

    if proc.returncode != 0:
        return f"exit code {proc.returncode}\n{truncated}"
    return truncated if truncated else "(no output)"
```

`shell=True` 是为了让模型写的命令和读者在终端里敲的一致。这个选择有安全代价——任何能绕过权限引擎的 shell 注入都可能直接执行。教学版的安全策略在 v2 的权限引擎里补：危险命令 regex 拦截 + 用户确认。

### 1.2 新建 `agent_code/prompt_ui.py`

Day 4 的 `diff_ui.py` 里只有 `render_diff` 和 `confirm_edit`。今天还要再加 `confirm_command`、`confirm_tool_use`，v3 还会再加一个选项菜单——里面 80% 都不是 diff 了，文件名得跟着改。把它搬成 `prompt_ui.py`。

模块搬家分两步：

1. v1（这一版）：实现挪到 `prompt_ui.py`，`diff_ui.py` 改成 4 行 re-export。这样 v1 的 `agent.py`（还在写 `from .diff_ui import ...`）原封不动也能跑通——re-export 在这里就是个过渡桥。
2. v2：改 `agent.py` 切到 `from .prompt_ui import ...`，切完整个包里没人再 import `diff_ui`，把 `diff_ui.py` 整个删掉。

为什么不一次到位？因为 v1 的目标是 bash 拦截块跑通，再顺手改 `agent.py` 那行 import 会让本版的 diff 多一处和 bash 无关的改动。垫片留一版，下一版再拆。

先创建 `prompt_ui.py`：

```python
from __future__ import annotations

import difflib

import typer


def render_diff(old: str, new: str, path: str) -> str:
    """用 difflib 生成 unified diff，给增删行加 rich markup 着色。"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    colored: list[str] = []
    for line in diff_lines:
        line = line.rstrip()
        if line.startswith("---") or line.startswith("+++"):
            colored.append(f"[bold]{line}[/bold]")
        elif line.startswith("-"):
            colored.append(f"[red]{line}[/red]")
        elif line.startswith("+"):
            colored.append(f"[green]{line}[/green]")
        elif line.startswith("@@"):
            colored.append(f"[cyan]{line}[/cyan]")
        else:
            colored.append(line)
    return "\n".join(colored)


def confirm_edit(path: str) -> bool:
    """让用户确认是否应用这次编辑，默认不应用。"""
    return typer.confirm(f"Apply this edit to {path}?", default=False)


def confirm_command(command: str) -> bool:
    """让用户确认是否执行这条 bash 命令，默认不执行。"""
    return typer.confirm(f"Run this command?", default=False)


def confirm_tool_use(tool_name: str, detail: str) -> bool:
    """让用户确认非 bash 的 ask 类工具，例如访问外部网络。"""
    return typer.confirm(f"Allow {tool_name}: {detail}?", default=False)
```

然后把 `agent_code/diff_ui.py` **整个文件**替换成下面这 4 行——原来的 `import difflib` / `import typer` / `def render_diff` / `def confirm_edit` 全删掉，留一个空壳只做 re-export：

```python
from __future__ import annotations

# Day 5 v1：render_diff 和 confirm_edit 的实现迁到了 prompt_ui.py。
# 这里只是个 re-export 垫片，让 v1 的 agent.py 不用改 import 也能跑。v2 会把它删掉。
from .prompt_ui import confirm_edit, render_diff

__all__ = ["confirm_edit", "render_diff"]
```

可以验证 re-export 真的生效（函数实体住在 `prompt_ui` 里）：

```bash
$ uv run python -c "from agent_code.diff_ui import render_diff; print(render_diff.__module__)"
agent_code.prompt_ui
```

如果输出是 `agent_code.diff_ui`，说明文件没替干净，原来的 `def render_diff` 还残留着——回去重删一遍。

### 1.3 改 `agent_code/tools.py`：新增 bash、git_status、git_diff

三个新工具。`bash` 是核心——调 `bash_runner.run_sync`。`git_status` 和 `git_diff` 是只读便利工具，内部也是 subprocess，但默认放行——降低 ask 弹窗噪音。

**第一处**，顶部 import 区域追加：

```python
from .bash_runner import run_sync as _bash_run_sync
```

**第二处**，在 `file_edit` 函数**之后**、`class ToolRegistry` **之前**插入三个工具函数：

```python
def _git_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """薄包装 git status——只读、默认 allow。"""
    return _bash_run_sync("git status", ctx.cwd, timeout=10)


def _git_diff(args: dict[str, Any], ctx: ToolContext) -> str:
    """薄包装 git diff——只读、默认 allow。"""
    return _bash_run_sync("git diff", ctx.cwd, timeout=10)


def bash(args: dict[str, Any], ctx: ToolContext) -> str:
    """执行 shell 命令。前置校验和用户确认在 agent.py 拦截块完成。"""
    command = args.get("command", "")
    if not command:
        return "error: missing required argument 'command'"
    timeout = int(args.get("timeout", 30))
    background = bool(args.get("background", False))

    # v1 只做同步；v4 接 background=True 分支
    if background:
        return "error: background mode not implemented yet (coming in v4)"

    return _bash_run_sync(command, ctx.cwd, timeout=timeout)
```

工具函数本身很薄：`bash` 只做参数校验，然后调 runner。真正的安全检查（危险命令拦截、用户确认）全在 `agent.py` 的拦截块里——和 Day 4 的 `file_write`/`file_edit` 一样，工具保持纯净。

**第三处**，在 `default_tools()` 的 `file_edit` 注册**之后**、`return registry` **之前**追加三个注册：

```python
    registry.register(
        Tool(
            name="git_status",
            description="Run git status to see the current state of the working directory.",
            run=_git_status,
            parameters={"type": "object", "properties": {}, "required": []},
        )
    )
    registry.register(
        Tool(
            name="git_diff",
            description="Run git diff to see unstaged changes in the working directory.",
            run=_git_diff,
            parameters={"type": "object", "properties": {}, "required": []},
        )
    )
    registry.register(
        Tool(
            name="bash",
            description=(
                "Execute a shell command. Working directory persists but shell state "
                "does not (each call is a fresh shell). timeout in seconds (default 30). "
                "Avoid cd; use the tool's implicit cwd instead."
            ),
            run=bash,
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds, default 30.",
                        "default": 30,
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run in background. Returns immediately with a background_id. Default false.",
                        "default": False,
                    },
                },
                "required": ["command"],
            },
        )
    )
```

### 1.4 改 `agent_code/agent.py`：在拦截块加 bash 分支

Day 4 的拦截块只认 `file_write` 和 `file_edit`。v1 在这个 if 后面加一个 `elif` 分支：识别 `bash` 工具，打印命令预览，让用户确认。

**第一处**，顶部 import 追加：

```python
from .prompt_ui import confirm_command
```

放在 `from .diff_ui import confirm_edit, render_diff` 之后（这行 import 还能用，因为 `diff_ui` 做了 re-export）。

**第二处**，在拦截块的 `if call.name in ("file_write", "file_edit"):` 整块**之后**、`result = tools.run(call, ctx)` **之前**插入 bash 拦截分支。找到这一行：

```python
            result = tools.run(call, ctx)
```

在它**之前**插入：

```python
            # bash 拦截：打印命令预览，让用户确认后再执行
            elif call.name == "bash":
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
```

注意这里只拦截了 `bash`，没有拦截 `git_status` 和 `git_diff`——它们是只读工具，默认放行直接走 `tools.run`。

`result = tools.run(call, ctx)` 这一行保持不变，对所有工具（包括 file_write/file_edit/bash/其他）都是通用执行路径。

其他 CLI 代码（`render_header()`、`handle_slash()`、`main_command()`）这一版都不用动。

### 1.5 跑三个验证

**(a) 只读 git 工具——不弹确认直接出结果：**

```bash
$ uv run agent-code "用 git_status 看一下当前仓库状态，然后用 git_diff 看改了什么"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: git_status {}
observation: On branch main
Changes not staged for commit:
  modified: ...

tool_call: git_diff {}
observation: diff --git ...
final: 当前仓库有一些未提交改动，我已经看到了 git status 和 git diff。
```

`git_status` 和 `git_diff` 直接走 `tools.run`，没有弹确认。这里不要要求你的输出和示例一模一样：如果你正在这个仓库里跟着教程改代码，`git status` 大概率不是 clean。关键看两件事：出现 `tool_call: git_status` / `tool_call: git_diff`，并且中间没有 `Run this command?` 确认提示。

**(b) bash 执行——命令预览 + 确认：**

```bash
$ uv run agent-code "用 bash 跑一下 uv run pytest --version"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: bash {'command': 'uv run pytest --version', 'timeout': 30, 'background': False}

Command: uv run pytest --version
timeout: 30s  cwd: /your/project
Run this command? [y/N]: y
observation: pytest 8.x.x
final: 当前环境中安装了 pytest 8.x.x。
```

看到 `Command:` 黄字预览和 `Run this command?` 提示，按 `y` 才执行。按 `n` 会看到 `observation: error: command rejected by user`。

**(c) bash 执行报错——模型看到 exit code 后自我修正：**

```bash
$ uv run agent-code "用 bash 跑一下 python -c 'print(1/0)'"
...
tool_call: bash {'command': "python -c 'print(1/0)'", 'timeout': 30, 'background': False}

Command: python -c 'print(1/0)'
timeout: 30s  cwd: /your/project
Run this command? [y/N]: y
observation: exit code 1
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ZeroDivisionError: division by zero
final: 命令执行失败了——代码尝试用 0 做除数，Python 抛出了 ZeroDivisionError。
```

模型拿到 `exit code 1` + stderr 内容后能自己分析失败原因。

v1 的 bash 能跑了，但确认逻辑还嵌在 `agent.py` 的 if/elif 链里。每加一种新工具就要加一个分支，而且危险 bash 也只是弹 y/N，没有在确认前被拦住。下一版把确认逻辑抽成独立的权限引擎，用 `PermissionRequest` 描述一次工具调用，用 `PermissionDecision` 统一决策。

## v2：权限引擎——三模式 + 危险命令拦截

v1 有三个问题：

1. **确认逻辑散落**：`file_write`/`file_edit` 的 diff+confirm 在 if 块里，`bash` 的命令预览+confirm 在 elif 块里。加 `ask_user_question` 又要加一个分支。
2. **危险 bash 只靠确认不够**：`echo hello` 和 `rm -rf /` 都走同一个 y/N 流程。教学版可以让所有 bash 都弹确认，但危险命令应该在确认前直接拒绝。
3. **没有模式切换**：有时候你想让 Agent 自己改文件不弹确认（`acceptEdits`），有时候你想让它只看不改（`plan`）。

权限引擎解决这三个问题：所有工具调用在执行前都经过 `decide_permission()`，返回 `allow | ask | deny`。Agent Loop 根据决策分发：allow → 直接执行，ask → 渲染预览 + 用户确认，deny → 返回 error observation。

### 2.1 新建 `agent_code/permissions.py`

权限引擎的核心是一个函数 `decide_permission(request)`。`PermissionRequest` 描述"这次工具调用想做什么"，`PermissionDecision` 描述 harness 要不要放行。

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PermissionRequest:
    """一次工具调用的权限请求。工具只描述意图，是否执行交给 harness 决定。"""
    tool_name: str
    args: dict
    mode: str
    cwd: Path


@dataclass
class PermissionDecision:
    """权限引擎的决策结果。behavior 是 allow / ask / deny 之一。"""
    behavior: str  # "allow" | "ask" | "deny"
    message: str | None = None  # deny 或 ask 时的说明文字


# 教学版危险命令列表。正则只是兜底，不是完整 shell sandbox。
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf", "rm -rf is destructive"),
    (r"rm\s+-fr", "rm -fr is destructive"),
    (r"\bsudo\b", "sudo grants root access"),
    (r"chmod\s+-R", "chmod -R is recursive permission change"),
    (r"(curl|wget).*\|.*(sh|bash|zsh)\b", "downloaded script execution may execute untrusted code"),
    (r"git\s+push\s+.*--force", "git push --force overwrites remote history"),
    (r"git\s+push\s+-f", "git push -f overwrites remote history"),
    (r"\bgit\s+push\b", "git push publishes local changes to a remote"),
    (r"git\s+reset\s+--hard", "git reset --hard discards uncommitted changes"),
]


def _is_dangerous(command: str) -> str | None:
    """检查命令是否匹配危险模式。返回 None 表示安全，返回字符串表示危险原因。"""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


# 只读工具白名单——这些工具默认 allow，不走 ask 弹窗
_READONLY_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep", "project_tree",
    "git_status", "git_diff",
    "system_date", "echo",
})

# 交互和网络都不是写入，但仍需要用户知道 Agent 正在停下来问人或访问外部资源。
_ASK_TOOLS = frozenset({"ask_user_question", "web_fetch", "web_search"})


def decide_permission(request: PermissionRequest) -> PermissionDecision:
    """权限引擎入口：根据工具名、参数和当前模式决定 allow / ask / deny。"""
    tool_name = request.tool_name
    args = request.args
    mode = request.mode

    if tool_name in _ASK_TOOLS:
        return PermissionDecision("ask")

    # plan 模式：只允许只读工具；ask_user_question / web_* 仍会走 ask。
    # 写类工具一律 deny。
    if mode == "plan":
        if tool_name in _READONLY_TOOLS:
            return PermissionDecision("allow")
        return PermissionDecision(
            "deny",
            f"plan mode: {tool_name} is not allowed. Only read-only tools can run in plan mode.",
        )

    # 只读工具在所有模式下默认允许
    if tool_name in _READONLY_TOOLS:
        return PermissionDecision("allow")

    # bash 工具的危险命令检测——不管什么模式，危险命令先拦截
    if tool_name == "bash":
        command = args.get("command", "")
        danger_reason = _is_dangerous(command)
        if danger_reason:
            return PermissionDecision("deny", f"Dangerous command blocked: {danger_reason}")

    # acceptEdits 模式：文件编辑跳过确认 UI，但安全校验仍在 agent.py 做
    if mode == "acceptEdits":
        if tool_name in ("file_write", "file_edit"):
            return PermissionDecision("allow")

    # 默认模式：写文件和 bash 需要确认
    return PermissionDecision("ask")
```

为什么只读工具白名单放在权限引擎而不是工具注册表？因为"是否只读"是权限决策的属性，不是工具自身的属性。同一个 `bash` 工具可能跑只读命令也可能跑写命令，权限引擎看的是"这个工具在当前调用里做什么"，而不是"这个工具是什么类型"。

`web_fetch` 和 `web_search` 没放进只读白名单。它们不会写本地文件，但会访问外部网络，所以默认走 `ask`，让用户知道 Agent 要离开本地项目去取信息。`ask_user_question` 也走 `ask`，包括 `plan` 模式下也可以用来澄清需求。

这组危险命令 regex 是教学版的最小安全网，不是完备安全边界。生产环境还需要容器 sandbox、结构化命令解析、只读文件系统、网络隔离等更严格的隔离。

### 2.2 改 `agent_code/agent.py`：用 decide_permission 统一拦截

把 v1 的 if/elif 拦截链替换成统一的 permission gate。核心变化：所有工具调用先过 `decide_permission`，根据返回的 behavior 分发。

**第一处**，顶部 import。把 `from .diff_ui import confirm_edit, render_diff` 整行替换成从 `prompt_ui` 直接 import：

```python
from .prompt_ui import confirm_command, confirm_edit, confirm_tool_use, render_diff
```

追加 permissions 的 import：

```python
from .permissions import PermissionRequest, decide_permission
```

切完之后，整个 `agent_code/` 里没有任何文件还在 import `diff_ui`——v1 留的那座 re-export 桥可以拆了。**把 `agent_code/diff_ui.py` 删掉**：

```bash
$ rm agent_code/diff_ui.py
```

验证一下没有遗留引用：

```bash
$ rg 'from \.diff_ui|import diff_ui' agent_code/
```

预期：没有任何输出。如果还命中行，说明哪个文件漏切了，把那一行的 import 改成 `from .prompt_ui import ...` 即可。

**第二处**，改 `run_agent` 签名，加 `permission_mode` 参数：

```python
def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    permission_mode: str = "default",  # 新增：default | acceptEdits | plan
) -> AgentResult:
```

**第三处**，把整个工具调用循环（从 `for call in response.tool_calls:` 到循环结束的 `messages.append(...)`）替换成下面这版。核心变化是用 `decide_permission` 统一入口，根据 `behavior` 分发到三条路径：

```python
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

            edit_preview: tuple[str, str, str] | None = None
            if call.name in ("file_write", "file_edit") and decision.behavior != "deny":
                # acceptEdits 只跳过确认 UI，不能跳过 Day 4 的安全校验
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
                    # v3 接上
                    pass

            # allow 路径 + ask 通过：执行工具
            result = tools.run(call, ctx)
            emit(f"observation: {result.content}")
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
            )

        messages.append({"role": "user", "content": tool_result_blocks})
```

三种决策路径：`deny` 直接返回 error observation（不弹 UI、不执行工具）；`ask` 按工具类型分发不同的预览 UI，用户确认后才落到 `tools.run`；`allow` 跳过 UI 直接 `tools.run`。

注意 `acceptEdits` 只是跳过确认，不是跳过安全校验。`file_write` / `file_edit` 在进入 `ask` 或 `allow` 分支前，都会先走 Day 4 的 read-before-edit、mtime 冲突和字符串替换预计算。

### 2.3 改 `agent_code/cli.py`：加 `--permission-mode` 选项

**第一处**，在 `main_command` 函数签名里加一个 Option：

```python
@app.callback(invoke_without_command=True)
def main_command(
    prompt: str = typer.Argument("", help="Prompt to send to the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", "-C"),
    provider: str = typer.Option("anthropic", "--provider"),
    model: str = typer.Option("deepseek-v4-flash", "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    max_steps: int = typer.Option(8, "--max-steps"),
    permission_mode: str = typer.Option("default", "--permission-mode", help="Permission mode: default, acceptEdits, plan"),
) -> None:
```

**第二处**，`run_once` 函数签名加参数，并传给 `run_agent`：

```python
def run_once(
    prompt: str,
    cwd: Path,
    provider_name: str,
    model: str,
    base_url: str | None,
    max_steps: int,
    permission_mode: str,
) -> None:
    render_header(cwd, provider_name, model, base_url)
    provider = create_provider(provider_name, model, base_url)
    run_agent(prompt, provider, default_tools(), max_steps=max_steps, cwd=cwd, permission_mode=permission_mode)
```

**第三处**，两处调用 `run_once` 的地方补上 `permission_mode` 参数。`main_command` 里两处 `run_once(...)` 调用都加上 `permission_mode`：

```python
        run_once(text, resolved_cwd, provider, model, base_url, max_steps, permission_mode)
```

和 REPL 循环里的：

```python
        run_once(line, resolved_cwd, provider, model, base_url, max_steps, permission_mode)
```

### 2.4 跑五个验证

**(a) default 模式——文件编辑仍需确认：**

Day 4 跑完后，`hello.txt` 通常已经被改成了 `hola from agent`。这里我们先在 default 模式下让模型读一遍文件，再把 `hola` 改回 `hello`，看它是否还会弹 diff + confirm。

```bash
$ uv run agent-code "先读 hello.txt，再把里面的 hola 改成 hello"
...
tool_call: read_file {'path': 'hello.txt'}
observation: hola from agent
...
tool_call: file_edit {...}

Diff for hello.txt:
...
Apply this edit to hello.txt? [y/N]:
```

Day 4 的 diff + confirm 行为不变。

**(b) acceptEdits 模式——文件编辑跳过确认：**

接着再用 `acceptEdits` 把 `hello` 改回 `hola`。每次 `uv run` 都是一个新进程，`read_state` 不会跨命令保留，所以这条也要让模型先读文件。

```bash
$ uv run agent-code --permission-mode acceptEdits "先读 hello.txt，再把里面的 hello 改成 hola"
...
tool_call: read_file {'path': 'hello.txt'}
observation: hello from agent
...
tool_call: file_edit {...}
observation: Edited hello.txt: replaced 5 chars with 4 chars
```

没有 `Diff for` 和 `Apply this edit?`——`acceptEdits` 模式下 `decide_permission` 对 `file_write`/`file_edit` 返回 `allow`，但执行前仍会跑 read-before-edit、mtime 冲突和字符串替换校验。

但 bash 仍然需要确认：

```bash
$ uv run agent-code --permission-mode acceptEdits "用 bash 跑 echo hello"
...
Command: echo hello
Run this command? [y/N]:
```

**(c) plan 模式——写工具被拒绝：**

```bash
$ uv run agent-code --permission-mode plan "创建 hello.txt 文件"
...
tool_call: file_write {'file_path': 'hello.txt', 'content': 'hello'}
observation: error: plan mode: file_write is not allowed. Only read-only tools can run in plan mode.
final: 当前处于 plan 模式，不允许执行写入操作。如果你需要修改文件，请先退出 plan 模式。
```

`file_write` 被 deny，只读工具不受影响：

```bash
$ uv run agent-code --permission-mode plan "读一下 hello.txt"
...
tool_call: read_file {'path': 'hello.txt'}
observation: hola from agent
```

如果你的 `hello.txt` 内容和这里不一样，以当前文件内容为准。这个验证只看一件事：plan 模式允许 `read_file`，但拒绝写工具。

**(d) 危险 git 命令被拦截：**

```bash
$ uv run agent-code "用 bash 跑 git push --force origin main"
...
tool_call: bash {'command': 'git push --force origin main', 'timeout': 30, 'background': False}
observation: error: Dangerous command blocked: git push --force overwrites remote history
```

没有弹 Command 预览、没有弹确认——权限引擎在 `decide_permission` 里直接返回 `deny`。模型看到 error 后通常会换个方案。

**(e) `sudo rm -rf /` 可能被模型先拒绝：**

```bash
$ uv run agent-code "用 bash 跑 sudo rm -rf /"
...
final: 这个命令会删除系统文件，我不能运行。
```

真实模型有时会在调用工具前就拒绝这类极端危险命令。看到这种 final 不代表权限引擎没实现，只是模型层先拦了一次。
要稳定验证权限引擎，优先用上面的 `git push --force`；或者在测试里用 mock provider 直接构造 `bash {"command": "sudo rm -rf /"}`，应该得到 `Dangerous command blocked: sudo grants root access` 或 `Dangerous command blocked: rm -rf is destructive` 一类的 deny observation，具体取决于 regex 命中顺序。

到这里，权限引擎的三模式 + 危险命令拦截全部接好。`agent.py` 里的拦截逻辑不再是一堆 if/elif，而是一个统一的 `decision.behavior` 三分支。

## v3：`ask_user_question` —— 让模型主动问你

v2 的权限引擎只处理了两种交互：allow（直接执行）和 ask（预览 + y/N）。但有些场景模型需要的不只是"同不同意执行"，而是让你做选择题——"我应该用方案 A 还是方案 B？"、"先修测试还是先修代码？"

`ask_user_question` 就是干这个的。它是独立的一等工具，不是 slash 命令。模型调它，harness 阻塞 Agent Loop，终端弹一个 numbered menu 让你选，结果作为 tool_result 回灌模型。

### 3.1 改 `agent_code/prompt_ui.py`：加选项菜单

在文件末尾追加：

```python
def prompt_single_choice(question: str, labels: list[str]) -> str | None:
    """展示一个 numbered menu 让用户单选，返回被选中的 label。"""
    from rich.console import Console

    console = Console()
    console.print(f"\n[bold yellow]? {question}[/bold yellow]")
    for i, label in enumerate(labels, 1):
        console.print(f"  {i}. {label}")
    console.print(f"  0. [dim]Skip / Other[/dim]")

    try:
        choice = typer.prompt("Choice", default="0")
        idx = int(choice)
        if 1 <= idx <= len(labels):
            return labels[idx - 1]
        return None
    except (ValueError, TypeError):
        return None
```

### 3.2 改 `agent_code/tools.py`：新增 `ask_user_question`

**第一处**，在 `bash` 函数**之后**、`class ToolRegistry` **之前**插入：

```python
def _ask_user_question(args: dict[str, Any], ctx: ToolContext) -> str:
    """由 agent.py 拦截块处理——工具函数本身不读 stdin。
    拦截块识别 call.name == "ask_user_question"，调 prompt_ui 后把结果作为 observation 返回。"""
    prompt = args.get("prompt", "")
    options = args.get("options", [])
    if not prompt:
        return "error: missing required argument 'prompt'"
    if not options or not isinstance(options, list):
        return "error: options must be a non-empty list"
    # 实际交互在 agent.py 拦截块里完成——这里只返回占位。
    # 正常路径不会走到这里，因为拦截块会先处理。
    return "error: ask_user_question must be handled by the harness, not executed directly"
```

**第二处**，在 `default_tools()` 的 `bash` 注册**之后**追加：

```python
    registry.register(
        Tool(
            name="ask_user_question",
            description=(
                "Ask the user a structured single-choice question. "
                "Use when you need to decide between multiple approaches "
                "or need user preference before proceeding."
            ),
            run=_ask_user_question,
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The question to ask the user. Should end with ?.",
                    },
                    "options": {
                        "type": "array",
                        "description": "List of option labels (2-4 recommended).",
                        "items": {"type": "string"},
                    },
                },
                "required": ["prompt", "options"],
            },
        )
    )
```

### 3.3 改 `agent_code/agent.py`：拦截 `ask_user_question`

在 v2 的拦截块里已经有 `elif call.name == "ask_user_question":` 占位。把里面的 `pass` 替换成实际逻辑。

**第一处**，顶部 import 追加：

```python
from .prompt_ui import confirm_command, confirm_edit, confirm_tool_use, prompt_single_choice, render_diff
```

**第二处**，把拦截块里的：

```python
                elif call.name == "ask_user_question":
                    # v3 接上
                    pass
```

替换成：

```python
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
```

注意 `ask_user_question` 走的是 `continue`——它自己构造 `ToolResult` 并加入 `tool_result_blocks`，不落到后面的 `tools.run`。
这和 `file_write`/`file_edit`/`bash` 不同：那些工具在确认通过后仍然需要 `tools.run` 执行实际动作，`ask_user_question` 的全部作用就是弹菜单拿答案。

另外注意：`ask_user_question` 在 `decide_permission` 里没有被加入只读白名单，所以它走的是默认的 `ask` 决策。但 `ask_user_question` 的交互方式和其他 ask 工具不同——它不是 y/N 确认，而是选项菜单。所以拦截块在 `decision.behavior == "ask"` 分支里给 `ask_user_question` 开了专门的处理路径。

### 3.4 跑验证

```bash
$ uv run agent-code "我应该先修测试还是先修代码？用 ask_user_question 问我，选项三个：先修测试、先修代码、不确定"
...
tool_call: ask_user_question {'prompt': '修复这个 bug 应该从哪个方向入手？', 'options': ['先修测试', '先修代码', '不确定']}

? 修复这个 bug 应该从哪个方向入手？
  1. 先修测试
  2. 先修代码
  3. 不确定
  0. Skip / Other
Choice: 2
observation: User selected: "先修代码"
final: 你选择了"先修代码"。那我们从修改实现代码开始，让代码行为匹配测试期望。
```

模型拿到你的选择后继续推理。选 `0` 则返回 `User skipped the question.`。

v3 的 `ask_user_question` 到这里就收住。现在这个 numbered menu 是教学版的最小实现：能证明"模型发起结构化问题 → harness 暂停 loop 等用户 → 用户选择变成 tool_result → 模型继续推理"这条链路已经接通。

你在 Claude Code 里看到的上下键选择，本质上不是模型能力更强，而是 `prompt_ui` 这一层做得更完整：终端进入 raw input，监听上/下箭头移动当前选项，按 Enter 返回选中的 label，再由 Agent Loop 包成 tool_result。
源码里这块也是一等工具，名字是 `AskUserQuestion`，它的权限检查固定走 ask，工具调用会要求用户交互；UI 侧支持一组问题、选项描述、预览和多选。我们今天不做这些，是为了先把 harness 边界讲清楚。后面真要升级，主要改 `prompt_single_choice()`：把 `typer.prompt("Choice")` 换成上下键菜单，`agent.py` 的拦截流程基本不用变。

## v4：bash(background=True) —— 后台执行

v1 的 bash 是同步的：模型调了 `bash("sleep 30")`，Agent Loop 就得干等 30 秒。对编译、长时间测试这类场景，等不起。

后台执行解决这个问题：模型设置 `background=True`，harness 启动子进程后立即返回结构化 observation（含 `background_id`、输出文件路径、pid），Agent Loop 不阻塞。模型后续可以 `bash("cat .bg/<id>.out")` 查输出、`bash("kill <pid>")` 杀进程。

### 4.1 新建 `agent_code/bg_manager.py`

一个子线程跑 `subprocess.Popen`，stdout 和 stderr 分别写到 `.bg/<id>.out` 和 `.bg/<id>.err`。启动函数立即返回 dict，不等待进程结束。

```python
from __future__ import annotations

import os
import subprocess
import threading
import uuid
from pathlib import Path


_MINIMAL_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", ""),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", "/bin/bash"),
}


def start_background(command: str, cwd: Path) -> dict:
    """在后台启动 shell 命令，stdout/stderr 流式写入 .bg/<id>.out/.err。
    立即返回结构化信息，不等待命令结束。"""
    bg_id = f"bg-{uuid.uuid4().hex[:8]}"
    bg_dir = cwd / ".bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    out_path = bg_dir / f"{bg_id}.out"
    err_path = bg_dir / f"{bg_id}.err"

    out_f = open(str(out_path), "w")
    err_f = open(str(err_path), "w")

    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        env=_MINIMAL_ENV,
        stdout=out_f,
        stderr=err_f,
    )

    def _wait_and_close() -> None:
        """等子进程结束，关闭文件描述符。在 daemon 线程里跑。"""
        proc.wait()
        out_f.close()
        err_f.close()

    t = threading.Thread(target=_wait_and_close, daemon=True)
    t.start()

    return {
        "background_id": bg_id,
        "output_file": str(out_path.relative_to(cwd)),
        "stderr_file": str(err_path.relative_to(cwd)),
        "pid": proc.pid,
        "message": (
            f"Started in background with ID: {bg_id}. "
            f"Output: .bg/{bg_id}.out, Stderr: .bg/{bg_id}.err. "
            f"Use bash(\"cat .bg/{bg_id}.out\") to check output, "
            f"bash(\"kill {proc.pid}\") to stop."
        ),
    }
```

`daemon=True` 的线程不会阻止 CLI 退出。这里先补一个完整心智模型：真正的后台任务生命周期不是"起一个线程"就结束了，而是 **创建任务 → 记录 task id → 持续写输出 → 允许后续读取 → 支持取消 → 结束时通知模型** 这一整条链。

Claude Code 的做法也是这个形状：后台 bash 仍然是同一个 `Bash` 工具，只是参数里带 `run_in_background: true`。harness 启动子进程后，会注册一个本地 shell task，把输出持续写到任务 output 文件，立刻把 `backgroundTaskId` 和输出路径作为 tool_result 还给模型。任务结束时，再通过 notification 把"这个后台任务完成了"这类消息送回模型上下文；用户要停掉任务时，走内部的 task kill 能力，而不是让模型自己猜 pid。

Day 5 不做这套完整管理。我们只保留最小链路：`background=True` 启动子进程，stdout/stderr 落到 `.bg/<id>.out/.err`，tool_result 返回 `background_id`、输出路径和 `pid`。后续模型想看结果，就自己 `bash("cat .bg/<id>.out")`；想停掉，就自己 `bash("kill <pid>")`。
大家如果想加上可以自己挑战把完整实现出来
### 4.2 改 `agent_code/tools.py`：bash 接 background 分支

找到 `bash` 工具函数里的这两行：

```python
    if background:
        return "error: background mode not implemented yet (coming in v4)"
```

替换成：

```python
    if background:
        # 后台执行：启动子进程后立即返回结构化信息，不阻塞 Agent Loop
        from .bg_manager import start_background
        result = start_background(command, ctx.cwd)
        return (
            f"Command running in background with ID: {result['background_id']}.\n"
            f"Output is being written to: {result['output_file']}\n"
            f"Stderr is being written to: {result['stderr_file']}\n"
            f"PID: {result['pid']}\n\n"
            f"{result['message']}"
        )
```

后台模式不做 timeout（由子进程自己控制），也不做输出截断（输出落到文件，不在 tool_result 里）。它和同步 bash 一样只传最小环境变量，避免把宿主机上的敏感环境变量带进子进程。

### 4.3 改 `agent_code/agent.py`：后台 bash 继续走权限引擎

后台 bash 和同步 bash 走同一个 `decide_permission` 流程。但后台命令通常是 `sleep`、`npm run build` 这类长任务——和 `rm -rf` 的危险等级不同。

不过教学版保持决策一致：对权限引擎来说，`bash` 就是 `bash`，不区分同步和后台。危险命令检测对两者都适用。用户确认对后台 bash 同样弹——`background=True` 只是执行方式不同，不是权限豁免。

不需要额外改动。验证一下即可。

### 4.4 跑两个验证

**(a) 后台执行 + 稍后查输出：**

```bash
$ uv run agent-code "用 bash(background=True) 运行 sleep 5 && echo 'done from background'。拿到 background_id 后，用同步 bash 跑 sleep 6 && cat 对应的 .bg 输出文件"
...
tool_call: bash {'command': 'sleep 5 && echo done from background', 'timeout': 30, 'background': True}

Command: sleep 5 && echo done from background
timeout: 30s  cwd: /your/project
Run this command? [y/N]: y
observation: Command running in background with ID: bg-a1b2c3d4.
Output is being written to: .bg/bg-a1b2c3d4.out
...
tool_call: bash {'command': 'sleep 6 && cat .bg/bg-a1b2c3d4.out', 'timeout': 10, 'background': False}

Command: sleep 6 && cat .bg/bg-a1b2c3d4.out
timeout: 10s  cwd: /your/project
Run this command? [y/N]: y
observation: done from background
final: 后台任务执行完成，输出为 "done from background"。
```

模型第一次调 `bash(background=True)` 拿到 `background_id` 后继续跑别的。后面再调 `bash("sleep 6 && cat .bg/<id>.out")` 读结果。这里故意等 6 秒，因为刚启动后台任务就 `cat` 可能读到空文件。如果你用 `printf` 模拟交互，至少准备两次 `y`：一次给后台命令，一次给后面的 `sleep 6 && cat ...`。

**(b) 杀后台进程：**

```bash
$ uv run agent-code "用 bash(background=True) 运行 sleep 300，然后用 kill 杀掉它"
...
tool_call: bash {'command': 'sleep 300', 'timeout': 30, 'background': True}
...
observation: Command running in background with ID: bg-e5f6g7h8.
PID: 12345
...
tool_call: bash {'command': 'kill 12345', 'timeout': 10, 'background': False}
```

模型从第一次 tool_result 拿到 pid，构造成 `kill <pid>` 命令。

## 收尾：今天的终版文件改动清单

| 文件 | 新增 | 改动 |
|---|---|---|
| `agent_code/bash_runner.py` | 新文件：`run_sync` | — |
| `agent_code/permissions.py` | 新文件：`PermissionRequest` + `PermissionDecision` + `decide_permission` + 危险命令 regex | — |
| `agent_code/bg_manager.py` | 新文件：`start_background` | — |
| `agent_code/prompt_ui.py` | 新文件：`render_diff` + `confirm_edit` + `confirm_command` + `confirm_tool_use` + `prompt_single_choice` | — |
| `agent_code/diff_ui.py` | — | 删除（v1 先改成 re-export 垫片让 `agent.py` 老 import 还能跑，v2 切完 import 后删掉） |
| `agent_code/tools.py` | `bash` + `_git_status` + `_git_diff` + `_ask_user_question` + 四个注册 + import `bash_runner` | — |
| `agent_code/agent.py` | import `PermissionRequest`、`decide_permission`、`confirm_command`、`confirm_tool_use`、`prompt_single_choice` | 拦截块从 if/elif 链改为统一 permission gate；`run_agent` 加 `permission_mode` 参数 |
| `agent_code/cli.py` | — | `--permission-mode` Option；`run_once` 传参 |

## 手动 trace 一遍

输入 `"用 bash 跑 uv run pytest，如果失败就分析原因，用 ask_user_question 让我选方向"`，`--permission-mode default`：

```txt
1. CLI 解析 prompt、cwd、permission_mode=default，进入 run_agent。
2. Agent Loop 把 prompt + 工具列表发给模型。
3. 模型返回 tool_use: bash {"command": "uv run pytest", "timeout": 30, "background": false}。
4. Agent Loop 构造 PermissionRequest(tool_name="bash", args={...}, mode="default", cwd=...)。
5. decide_permission(request):
   a. request.mode 不是 plan → 跳过 plan deny
   b. request.tool_name 不在只读白名单 → 继续
   c. _is_dangerous("uv run pytest") → None，不是危险命令
   d. request.mode 不是 acceptEdits → 不走编辑跳过确认
   e. 返回 PermissionDecision("ask")
6. decision.behavior == "ask" + call.name == "bash":
   → 打印 Command: uv run pytest（黄色）+ timeout/cwd
   → confirm_command("uv run pytest") → 终端弹 Run this command? [y/N]
7. 用户按 y。
8. 落到 tools.run(bash) → bash_runner.run_sync("uv run pytest", cwd, 30)
   → subprocess.run("uv run pytest", shell=True, cwd=..., timeout=30)
   → 返回 "FAILED test_foo.py::test_bar - AssertionError: ..."
9. Agent Loop 打包 tool_result 发回模型。
10. 模型看到失败信息，返回 tool_use: read_file {"path": "test_foo.py"}。
11. read_file 在只读白名单 → decide_permission 返回 allow → 直接 tools.run。
12. 模型读到测试代码，返回 tool_use: ask_user_question {...}。
13. ask_user_question 对应的 PermissionRequest 进入 decide_permission:
    → 不在只读白名单 → 返回 ask
14. decision.behavior == "ask" + call.name == "ask_user_question":
    → prompt_single_choice(question, labels) → 终端弹 numbered menu
15. 用户选 "改测试期望值"。
16. 构造 ToolResult("User selected: 改测试期望值") → 加入 tool_result_blocks → continue
    （不执行 tools.run，ask_user_question 的全部工作就是拿答案）
17. 模型拿到选择，调用 file_edit 改测试文件。
18. file_edit 对应的 PermissionRequest 进入 decide_permission → ask
19. diff + confirm → 用户 y → tools.run(file_edit)
20. 模型拿到 edit 成功 observation，可能再调一次 bash("uv run pytest") 验证。
21. 测试通过 → 模型返回 final 总结。
```

## 今天有了什么

- **`bash` 工具**：模型能跑任意 shell 命令。cwd 锁定在项目目录，timeout 默认 30s，输出截断到 12000 字符。`git_status` / `git_diff` 是它的只读薄包装，默认 allow，降低 ask 弹窗噪音。
- **权限引擎 `permissions.py`**：`PermissionRequest` 描述一次工具调用，`PermissionDecision(allow | ask | deny)` 统一决策。三模式：`default`（写类默认 ask）、`acceptEdits`（文件编辑跳过确认但保留安全校验）、`plan`（写类一律 deny；澄清问题和网络访问仍会走 ask）。
- **危险命令拦截**：regex 覆盖 `rm -rf`、`sudo`、`chmod -R`、`curl | sh`、`git push`、`git push --force`、`git reset --hard`。命中直接 deny，不弹 UI。
- **`ask_user_question` 工具**：模型主动停下来问用户结构化单选问题。终端弹 numbered menu，结果作为 tool_result 回灌，驱动下一轮推理。
- **后台 bash**：`bash(background=True)` 启动子进程后立即返回 `background_id` + 输出文件路径。Agent Loop 不阻塞。模型后续用 `bash("cat .bg/<id>.out")` 查输出、`bash("kill <pid>")` 杀进程。

## 常见问题

### `bash` 工具报 `command timed out after 30s`

模型给的命令跑了超过 30 秒。默认 timeout 是 30s，可以让模型在调用时传更大的 `timeout` 值，或者改用 `background=True` 在后台跑。给 timeout 设置上限可以放到课后挑战里做。

### `shell=True` 安全吗

教学版用 `shell=True` 是为了命令写法自然（和终端敲的一致）。安全靠两层：权限引擎的危险命令 regex 拦截（v2）+ 用户确认（v1）。生产环境还需要容器 sandbox、只读文件系统、网络隔离。这不是"shell=True 安全"，而是"shell=True + harness 拦截"。

### plan 模式下模型反复调 `file_edit` 被拒绝

模型可能没注意到自己处于 plan 模式。plan 模式在 system prompt 里应该有明确提示（Day 8 会加上）。目前如果模型陷入"调写工具 → 被 deny → 再调写工具"循环，可以 `/exit` 退出后用 `--permission-mode default` 重跑。

### `ask_user_question` 选了 `0` 之后模型怎么处理

返回 `User skipped the question.`。模型拿到这个结果后一般会自己拍板或者换一种方式问你。这不是 error——`is_error=False`——模型知道"用户看了问题但没有给明确方向"。

### 后台 bash 的输出文件什么时候出现

`start_background` 调用后 `.bg/<id>.out` 文件立刻创建（`Popen` 时 `open("w")`）。但内容要等到子进程真正产生输出时才写入。模型刚拿到 `background_id` 立刻 `cat` 可能看到空文件——让它等几秒再读，教程验证里用的是 `bash("sleep 6 && cat .bg/<id>.out")`。

## 课后挑战

1. **可选 range 的时间限制**：给 `permissions.py` 加一个配置项 `_MAX_TIMEOUT = 120`。在 `decide_permission` 的 bash 分支里检查 `timeout > _MAX_TIMEOUT`，超过则返回 ask 并附加说明。给命令行加 `--max-timeout` 选项。

2. **记住用户选择**：在 `permissions.py` 里加一个 `_remembered: dict[str, str]` 字典。用户对某个工具选了 "always allow" 后，后续同工具调用直接 allow。给 `confirm_command` 和 `confirm_edit` 加 "yes to all" 选项。

3. **`ask_user_question` 支持多选**：给 `prompt_single_choice` 加一个 `multi_select` 参数。多选模式下用户输入 `1,3` 返回两个 label。同步改 `_ask_user_question` 的 schema 加 `multi_select` 字段。

4. **后台任务列表**：给 `bg_manager.py` 加一个 `list_background() -> list[dict]` 函数，扫描 `.bg/` 目录，返回所有后台任务的状态（running/done）。给 tools.py 加一个 `bg_list` 工具让模型能列出自己的后台任务。

5. **`--permission-mode` 在 REPL 里切换**：给 CLI 加一个 `/permissions` slash 命令。在 REPL 里输入 `/permissions acceptEdits` 切换当前会话的权限模式，不需要退出重进。

## 思考题

1. **为什么权限引擎放在 `agent.py`（Agent Loop 层），而不是放在每个工具函数内部？** （提示：和 Day 4 思考题 2 共享同一个设计原则。如果一个工具函数自己决定"要不要弹确认"，换一个 UI（比如 web 端）时会发生什么？）

2. **`git_status` 和 `git_diff` 被做成独立工具，而不是让模型调 `bash("git status")`。这个设计选择的好处和代价分别是什么？** （提示：想一下权限引擎的只读白名单是怎么工作的，以及模型在猜测"什么命令是只读的"时可能犯什么错。）

3. **`plan` 模式只做了"写工具一律 deny"的硬约束，但没做"强制先用 todo_write 起草计划"的软约束。少了软约束，plan 模式还能叫 Plan Mode 吗？** （提示：Day 8 会补上这一半。先想想如果只有硬约束没有软约束，模型在 plan 模式下的典型行为会是什么。）

4. **后台 bash 的输出写到 `.bg/<id>.out` 文件，模型通过 `bash("cat .bg/<id>.out")` 查询。如果不用文件、改用 harness 在进程结束时自动把输出作为一条 notification 注入下一轮 messages，Agent 的行为会有什么不同？** （提示：notification 注入意味着模型不需要主动查询，但什么时候注入、注入给哪个 step 的模型？）

## 下一天

今天 Agent 具备了命令执行能力：能跑命令、有权限控制、能后台执行、能主动问你问题。加上 Day 3 的文件 + Web 工具和 Day 4 的安全编辑，Agent 已经具备了做小型代码任务的基础能力：读代码 → 改代码 → 跑测试 → 看结果 → 再改。

但每次 `agent-code` 退出后，对话历史就丢了。下一天我们要让会话可以保存和恢复——session JSONL 落盘、`--resume` 恢复上次对话、AGENT.md 项目记忆注入 system prompt、以及一个跨 session 的长期记忆系统（memdir）。这样 Agent 就能记住你和你的项目，而不是每次都是"初次见面"。
