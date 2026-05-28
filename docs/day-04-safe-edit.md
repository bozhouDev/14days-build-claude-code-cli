# Day 4：Safe Edit

Day 3 让 Agent 能读文件了。模型读到代码之后，很自然地会想改——"把这段重构一下"、"README 里加一行说明"。

问题来了：如果模型说改就改，我们的 CLI 不加任何检查，一句话就能覆盖你的 `app.py`——没有 diff、没有确认、没有后悔药。

所以今天我们在"模型输出意图"和"真正落盘"之间加一层执行控制。在 AI Agent 语境里，这层控制叫 harness——包在模型外面的执行控制层。模型负责说"我想调 file_write"，harness 负责决定这个调用能不能安全落地、落之前要不要出 diff、落了之后旧内容要不要备份。对我们的 CLI 来说，harness 就是 Agent Loop + 工具拦截逻辑 + 安全检查的总和。每次写盘前：

- `file_write` 整文件覆盖：agent 先出 diff，你按 y 再落盘。
- `file_edit` 字符串替换：必须先读过目标文件、读完后没被外部改过、`old_string` 只能匹配一处（或开 `replace_all` 全替换）。
- 每次成功写盘前，旧内容自动备份到 `.agent/history/`，不用模型操心。

跑完之后，Agent 第一次能安全地改你的项目文件——不是偷偷写，而是让你看见、让你决定。

代码约 300 行，新增 `diff_ui.py`、`file_history.py`，改动 `tools.py`、`agent.py`、`fs_safety.py`、`cli.py`。

## 起手：今天的起点

从 Day 3 的 `agent-code` 项目继续改。不需要新依赖——`difflib` 是标准库，rich 和 typer 早就装过。

Day 3 埋了一个钩子：`read_file` 每次读完文件都会调 `ctx.read_state.record(path, content)`，把 `(mtime_ns, char_count)` 写进 `ReadFileState.entries`。今天这个钩子终于要登场了——它就是"模型读过文件之后，文件有没有被外部改过"的判断依据。

今天我们加两个新文件（`diff_ui.py`、`file_history.py`），改四个老文件（`tools.py`、`agent.py`、`fs_safety.py`、`cli.py`），分三步走。

## v1：`file_write` 整文件覆盖 + diff 预览

先做最直接的需求：模型想写一个新文件或者覆盖一个已有文件，harness 在写盘前把 diff 渲染出来，让你看过再决定。

这一版实现三件事：新建 `diff_ui.py`、在 `tools.py` 加 `file_write`、在 `agent.py` 的工具循环里拦截 `file_write`。

### 1.1 新建 `agent_code/diff_ui.py`

这是 diff 渲染 + 用户确认的小模块。`render_diff` 用 `difflib.unified_diff` 生成差异文本，给增删行加上 rich 颜色标记。`confirm_edit` 用 `typer.confirm` 弹一个 y/N 提示。

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
```

注意这里的边界：diff 是 agent 展示给**人**看的，不是返回给模型的 observation。模型只知道自己请求了 `file_write`；真正写盘前，CLI 要把差异摆出来，让用户决定要不要继续。

### 1.2 改 `agent_code/tools.py`：新增 `file_write`

在 `web_search` 函数**之后**、`class ToolRegistry` **之前**插入 `file_write`。这个工具函数本身只做"写盘 + 更新 read_state"——所有安全校验（read-before-edit、mtime 冲突、用户确认）都在 `agent.py` 的拦截块里、`tools.run` 之前完成。这样工具函数就保持纯净：工具负责执行，harness 负责决定能不能执行。

```python
def file_write(args: dict[str, Any], ctx: ToolContext) -> str:
    """整文件覆盖写入。前置校验由 agent.py 拦截块完成。"""
    path_str = args.get("file_path", "")
    content = args.get("content", "")
    if not path_str:
        return "error: missing required argument 'file_path'"
    try:
        path = resolve_in_cwd(ctx.cwd, path_str)
    except ValueError as exc:
        return f"error: {exc}"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    # 写盘后刷新 read_state，让下一次编辑基于最新内容
    ctx.read_state.record(path, content)
    return f"Wrote {len(content)} chars to {path_str}"
```

然后在 `default_tools()` 的末尾、`return registry` **之前**注册：

```python
    registry.register(
        Tool(
            name="file_write",
            description="Write or overwrite a file. Path is relative to cwd.",
            run=file_write,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Relative path inside cwd."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["file_path", "content"],
            },
        )
    )
```

为什么 `file_write` 只要 `file_path` 和 `content`？因为它描述的是模型想做的事，不描述安全策略。文件不存在时可以直接新建；文件已经存在时必须先读过，再让 `agent.py` 的拦截块出 diff、问确认。

### 1.3 改 `agent_code/agent.py`：流式 trace + 拦截 `file_write`

核心改动落在两处：

- **流式 trace**：把每条 trace 立刻打印到终端，让 diff 和 confirm 提示能和 `tool_call` / `observation` 按真实顺序交错出现。Day 3 是 `run_agent` 跑完才一次性 dump trace，看不出"diff 是哪一步出现的"。
- **`file_write` 拦截**：在 `tools.run(call, ctx)` **之前**，先做前置校验（文件存在则要求先读过），通过了再渲染 diff 并 `typer.confirm`。用户回 n 或前置校验失败，直接构造 `ToolResult(is_error=True)` 当 observation 交回给模型，不进入 `tools.run`。

**第一步**，扩展顶部 import。`from .model import ModelProvider, ModelResponse` 改成多导一个 `ToolResult`（Day 1 就定义在 `model.py`，拦截块要手动构造它）：

```python
from .model import ModelProvider, ModelResponse, ToolResult
```

`from .fs_safety import SkipPolicy, load_gitignore` 改成多导一个 `resolve_in_cwd`：

```python
from .fs_safety import SkipPolicy, load_gitignore, resolve_in_cwd
```

再追加两行新 import：

```python
from rich.console import Console

from .diff_ui import confirm_edit, render_diff
```

**第二步**，在 import 段之后、`@dataclass class AgentResult` 之前加一个模块级 console：

```python
console = Console()
```

**第三步**，把 `run_agent` 里所有 `trace.append(line)` 调用改成同时打印。先在 `run_agent` 函数体顶部，紧贴 `messages = [...]` 之后加一个内部函数：

```python
    def emit(line: str) -> None:
        # 流式输出 trace：append 给测试用，print 给读者看
        trace.append(line)
        console.print(line)
```

然后把函数体里所有 4 处 `trace.append(...)` **替换**成 `emit(...)`：
改完之后，循环体现在是这个样子（注意两处 `trace.append` 已经变成了 `emit`）：

```python
        for call in response.tool_calls:
            emit(f"tool_call: {call.name} {call.arguments}")
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
```

**第四步**，在上面这个循环的基础上插入 `file_write` 拦截块。在 `emit(f"tool_call: ...")` **之后**、`result = tools.run(call, ctx)` **之前**插入。最终循环体长这样：

```python
        for call in response.tool_calls:
            emit(f"tool_call: {call.name} {call.arguments}")

            # file_write / file_edit 的 harness 拦截：先做前置校验，再渲染 diff，最后让用户确认
            if call.name in ("file_write", "file_edit"):
                path_str = call.arguments.get("file_path", "")

                # 1) 路径解析：越界 cwd 直接当 error
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

                # 2) 前置校验（v1 只对 file_write 做"文件存在则要求读过"）
                validation_error: str | None = None
                if call.name == "file_write" and path.exists():
                    if path not in ctx.read_state.entries:
                        validation_error = (
                            f"error: file has not been read yet. "
                            f"Read {path_str} first before editing."
                        )
                # file_edit 的校验在 v2 接上

                # 3) 算 new_content（v1 file_edit 还没接，先跳过 diff）
                new_content: str | None = None
                if call.name == "file_write":
                    new_content = call.arguments.get("content", "")

                # 4) 校验失败：不渲染 diff、不问用户，直接 error observation
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

                # 5) 校验通过：渲染 diff + 用户确认
                if new_content is not None:
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
```

拦截分四种结局：路径越界 / 前置校验失败 / 用户拒绝都构造 `is_error=True` 的 observation 后 `continue`，跳过 `tools.run`；只有最后一种"通过"才落到循环底部的 `tools.run`。每一种 error 都被流式打印出来了，模型也能在下一轮请求里把它当 feedback 吃下去。

**第五步**，更新 `agent_code/cli.py`：trace 现在已经在 `run_agent` 内部流式打印了，`run_once` 不能再 print 一遍。把 Day 3 `run_once` 里这两行：

```python
    result = run_agent(prompt, provider, default_tools(), max_steps=max_steps, cwd=cwd)
    for line in result.trace:
        console.print(line)
```

简化成：

```python
    run_agent(prompt, provider, default_tools(), max_steps=max_steps, cwd=cwd)
```

不需要 `result` 变量了——`AgentResult` 还是返回的，只是现在没人消费它（Day 6 加 session 持久化时会用上）。

### 1.4 跑两个验收

先新建一个文件——agent 对这场景不要求 read-before-edit：

```bash
$ uv run agent-code "用 file_write 创建 hello.txt，内容一行 'hello from agent'"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: file_write {'file_path': 'hello.txt', 'content': 'hello from agent'}

Diff for hello.txt:
--- a/hello.txt
+++ b/hello.txt
@@ -0,0 +1 @@
+hello from agent
Apply this edit to hello.txt? [y/N]: y
observation: Wrote 16 chars to hello.txt
final: 已成功创建 hello.txt，内容为 hello from agent。
```

再覆盖已有文件——`ReadFileState` 是每次 `agent-code` 调用独立的，所以第二次调用里模型必须先 `read_file` 一次再 `file_write`。这里把 `file_write` 写进 prompt，是为了让 v1 的验证更稳定：

```bash
$ uv run agent-code "用 read_file 读 hello.txt，然后用 file_write 把内容改成 'goodbye from agent'"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: read_file {'path': 'hello.txt'}
observation: hello from agent

tool_call: file_write {'file_path': 'hello.txt', 'content': 'goodbye from agent'}

Diff for hello.txt:
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello from agent
+goodbye from agent
Apply this edit to hello.txt? [y/N]: y
observation: Wrote 18 chars to hello.txt
final: 已完成！hello.txt 的内容已成功从 "hello from agent" 改为 "goodbye from agent"。
```

上面是一段典型输出。`final` 的措辞可能不一样，不用逐字对；只要看到 `tool_call: file_write`、`Diff for hello.txt`、`Apply this edit`、`observation: Wrote ...` 这几段，就说明 v1 跑通了。两次都要你按 `y` 才写盘，按 `n` 就原样不动。把上面命令的 `y` 换成 `n` 再跑一次，看 observation 是不是 `error: edit rejected by user`。

v1 的 file_write 能覆盖整文件了，但它有个致命缺陷：模型想把"README 第三段第五句话"改一个字，它得把整个 README 重写一遍塞进 `content`，消耗几千 token。下一版我们给模型一个"只改要改的那行"的工具。

## v2：`file_edit` 字符串替换 + 三项安全检查

v1 让模型能写了，但 `file_write` 是整文件覆盖——改一个字就要重写整个文件。Agent 真正需要的是一把手术刀，不是一把大锤。

`file_edit` 就是这把手术刀。模型给它三个东西：

- `file_path`：改哪个文件
- `old_string`：把文件里哪一段替换掉（必须原样匹配，包括缩进和空白）
- `new_string`：换成什么

然后 harness 在落盘前做三件事：文件读过没？读完之后被外部改过没？`old_string` 在文件里唯一吗？这三件事都是纯函数，集中在 `fs_safety.py`。

### 2.1 改 `agent_code/fs_safety.py`：加三个纯函数

在 `load_gitignore` 函数**之后**、文件末尾追加：

```python
def ensure_read_before_edit(state: ReadFileState, path: Path) -> str | None:
    """检查文件是否在本次会话中被读过。没读过返回 error 字符串。"""
    if path not in state.entries:
        return (
            f"error: file has not been read yet. "
            f"Read {path.name} first before editing."
        )
    return None


def check_mtime_conflict(state: ReadFileState, path: Path) -> str | None:
    """检查文件在 read 之后是否被外部修改过。mtime 变了返回 error。
    教学版不做 content-equals 兜底——mtime 变就判冲突，再读一次即可。"""
    entry = state.entries.get(path)
    if entry is None:
        return None  # ensure_read_before_edit 会先拦住没读的情况
    read_mtime_ns, _ = entry
    try:
        current_mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    if current_mtime_ns > read_mtime_ns:
        return (
            f"error: file was modified after read. "
            f"Read {path.name} again before editing."
        )
    return None


def apply_single_replace(
    content: str, old: str, new: str, replace_all: bool
) -> tuple[str | None, str | None]:
    """在 content 中查找 old 并替换为 new。
    返回 (new_content, error)：成功时 error 为 None，失败时 new_content 为 None。"""
    if old == "":
        # str.count("") 会返回 len+1，str.replace("", x) 会在每个字符之间插入 x。
        # 这两个行为对模型完全没用，直接拒绝。
        return None, "error: old_string must not be empty."
    if old == new:
        return None, "error: old_string and new_string are exactly the same."

    count = content.count(old)
    if count == 0:
        return None, "error: string to replace not found in file."
    if count > 1 and not replace_all:
        return None, (
            f"error: found {count} matches for old_string. "
            f"Use replace_all=True to replace all, or make old_string more specific."
        )

    if replace_all:
        return content.replace(old, new), None
    else:
        return content.replace(old, new, 1), None
```

三个函数都不碰全局状态。`apply_single_replace` 返回 tuple：`(结果, None)` 表示成功，`(None, 错误消息)` 表示失败。

这三个检查拆成纯函数，是为了让安全规则能单独测试，也让 `agent.py` 的拦截块读起来像一条清楚的门禁链：先确认读过，再确认没被外部改过，最后确认替换目标唯一。这里先返回字符串 error，Agent Loop 再把它包成 `is_error=True` 的 observation 交回给模型。

### 2.2 改 `agent_code/tools.py`：新增 `file_edit`

`file_edit` 工具函数本身保持纯净：解析路径 → 读盘 → 替换 → 写盘 → 刷新 read_state。read-before-edit、mtime 冲突、多匹配这三项校验都在 `agent.py` 的拦截块里完成，工具函数只在 `apply_single_replace` 里做最后一道兜底（防止 diff 和写盘之间出现 race，老内容已经变了导致 `old_string` 不再匹配）。

**第一处**，顶部 import 区域。`from .fs_safety import (...)` 的括号里追加 `apply_single_replace`：

```python
from .fs_safety import (
    ReadFileState,
    SkipPolicy,
    apply_single_replace,
    ensure_text_file,
    ensure_within_size,
    resolve_in_cwd,
    should_skip,
    truncate_output,
)
```

`ensure_read_before_edit` 和 `check_mtime_conflict` 不在 `tools.py` 用——它们在 `agent.py` 用，下一节再 import。

**第二处**，在 `file_write` 函数**之后**、`class ToolRegistry` **之前**插入 `file_edit`：

```python
def file_edit(args: dict[str, Any], ctx: ToolContext) -> str:
    """字符串替换编辑。前置校验在 agent.py 拦截块里完成。"""
    path_str = args.get("file_path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if not path_str:
        return "error: missing required argument 'file_path'"
    try:
        path = resolve_in_cwd(ctx.cwd, path_str)
    except ValueError as exc:
        return f"error: {exc}"

    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        return f"error: {exc}"

    # 防 race：agent.py 已经做过一次 apply_single_replace 算 diff，
    # 如果 confirm 那一刻到现在 old_content 又被外部改过，这里会再兜一次。
    new_content, err = apply_single_replace(content, old_string, new_string, replace_all)
    if err:
        return err

    path.write_text(new_content, encoding="utf-8")
    ctx.read_state.record(path, new_content)
    return f"Edited {path_str}: replaced {len(old_string)} chars with {len(new_string)} chars"
```

**第三处**，在 `default_tools()` 的 `file_write` 注册**之后**追加 `file_edit` 注册：

```python
    registry.register(
        Tool(
            name="file_edit",
            description=(
                "Edit a file by replacing old_string with new_string. "
                "old_string must be unique in the file (or use replace_all=True). "
                "You must read the file before editing."
            ),
            run=file_edit,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Relative path inside cwd."},
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to replace. Must match including whitespace and indentation.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "String to replace it with.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences. Default false.",
                        "default": False,
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        )
    )
```

### 2.3 改 `agent_code/agent.py`：把 file_edit 接进拦截，并把 v1 的内联校验换成 fs_safety 函数

v2 的拦截块要做两件事：把校验从"v1 内联的几行 if"换成调 `ensure_read_before_edit` / `check_mtime_conflict` 这两个新函数；把 `file_edit` 也接进拦截，在内存里试跑 `apply_single_replace` 算出 `new_content` 给 diff 预览。

**第一处**，扩展 `from .fs_safety import` 里加三个新函数：

```python
from .fs_safety import (
    SkipPolicy,
    apply_single_replace,
    check_mtime_conflict,
    ensure_read_before_edit,
    load_gitignore,
    resolve_in_cwd,
)
```

**第二处**，把 v1 拦截块里 "2) 前置校验" 这一段（约 8 行）：

```python
                # 2) 前置校验（v1 只对 file_write 做"文件存在则要求读过"）
                validation_error: str | None = None
                if call.name == "file_write" and path.exists():
                    if path not in ctx.read_state.entries:
                        validation_error = (
                            f"error: file has not been read yet. "
                            f"Read {path_str} first before editing."
                        )
                # file_edit 的校验在 v2 接上
```

替换成下面这版——`file_write` 用 `ensure_read_before_edit + check_mtime_conflict`；`file_edit` 总是要求文件存在 + read-before-edit + mtime 一致：

```python
                # 2) 前置校验：read-before-edit + mtime 冲突
                validation_error: str | None = None
                if call.name == "file_write":
                    if path.exists():
                        validation_error = (
                            ensure_read_before_edit(ctx.read_state, path)
                            or check_mtime_conflict(ctx.read_state, path)
                        )
                else:  # file_edit
                    if not path.exists():
                        validation_error = f"error: file does not exist: {path_str}"
                    else:
                        validation_error = (
                            ensure_read_before_edit(ctx.read_state, path)
                            or check_mtime_conflict(ctx.read_state, path)
                        )
```

**第三处**，把 v1 拦截块里 "3) 算 new_content" 这一段（4 行）：

```python
                # 3) 算 new_content（v1 file_edit 还没接，先跳过 diff）
                new_content: str | None = None
                if call.name == "file_write":
                    new_content = call.arguments.get("content", "")
```

替换成下面这版——`file_edit` 先跑一次 `apply_single_replace` 算 new_content。如果它返回 error（多匹配 / 0 匹配 / `old==new` / `old==""`），把 error 写进 `validation_error`：

```python
                # 3) 算 new_content：file_write 直接拿 content；file_edit 试跑替换
                new_content: str | None = None
                if call.name == "file_write":
                    new_content = call.arguments.get("content", "")
                elif call.name == "file_edit" and validation_error is None:
                    new_content, replace_err = apply_single_replace(
                        old_content,
                        call.arguments.get("old_string", ""),
                        call.arguments.get("new_string", ""),
                        bool(call.arguments.get("replace_all", False)),
                    )
                    if replace_err is not None:
                        validation_error = replace_err
```

第 4)、5) 步（校验失败 → 跳过 diff/confirm，直接 error observation；校验通过 → diff + confirm）原样保留。这一版的全部变化就是：所有 file_edit / file_write 的校验路径都先经过 `validation_error`，diff 只在校验通过时出现，confirm 只问那些 diff 真的能落盘的编辑。

### 2.4 跑四个验证

**(a) 成功 edit 一次——感受手术刀的形状：**

```bash
$ uv run agent-code "先 read_file hello.txt，再用 file_edit 把里面的 goodbye 改成 hola"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: read_file {'path': 'hello.txt'}
observation: goodbye from agent
tool_call: file_edit {'file_path': 'hello.txt', 'old_string': 'goodbye', 'new_string': 'hola'}

Diff for hello.txt:
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-goodbye from agent
+hola from agent
Apply this edit to hello.txt? [y/N]: y
observation: Edited hello.txt: replaced 7 chars with 4 chars
final: 修改成功！hello.txt 的内容已从 goodbye from agent 变更为 hola from agent。
```

注意 `tool_call: file_edit` 紧跟着 `Diff for ...`、再跟着 `Apply this edit?` 提示。这是流式 trace + 拦截块协作的样子：模型说"我要 edit"→ harness 立刻把 diff 摆在你面前 → 你按 y 才落盘。

**(b) 没读过就直接 edit——read-before-edit 拒绝：**

模型默认会先 `read_file` 再 `file_edit`，所以要触发这条 error，得在 prompt 里明确禁止先读：

```bash
$ uv run agent-code "不要调用 read_file。直接用 file_edit 把 hello.txt 里的 hola 改成 hello。"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: file_edit {'file_path': 'hello.txt', 'old_string': 'hola', 'new_string': 'hello'}
observation: error: file has not been read yet. Read hello.txt first before editing.
tool_call: read_file {'path': 'hello.txt'}
observation: hola from agent
...
```

注意：`error: file has not been read yet` 直接出现在 `observation:` 那一行——校验失败时**没有**渲染 diff、**没有**弹 confirm 提示。这正是 v2.3 拦截块第 4) 步的预期行为：校验失败一律走 error observation 路径。模型收到 error 之后通常会立刻回去 `read_file` 再补一次 `file_edit`——这是结构化 error 在驱动模型自我修正。

真实模型可能会在补完 `read_file` 后继续给出第二次 `file_edit`，这时终端会出现新的 `Diff for hello.txt` 和 `Apply this edit?`。这不算失败，说明 error feedback 生效了；你可以按 `y` 看它修正，也可以按 `n` 停在拒绝路径。

**(c) 多匹配：**

新建一个文件含两处相同文本：

```bash
$ echo -e "line one: hello\nline two: hello" > dup.txt
```

然后：

```bash
$ uv run agent-code --max-steps 2 "先 read_file dup.txt，再用 file_edit 把里面的 hello 改成 hi。file_edit 的 old_string 必须精确等于 hello，不要包含 line one，不要包含 line two，replace_all 必须是 false。"
...
tool_call: read_file {'path': 'dup.txt'}
observation: line one: hello
line two: hello

tool_call: file_edit {'file_path': 'dup.txt', 'old_string': 'hello', 'new_string': 'hi', 'replace_all': False}
observation: error: found 2 matches for old_string. Use replace_all=True to replace all, or make old_string more specific.
final: reached max_steps=2
```

模型看到这个 error 后，会学乖——要么加上 `replace_all=True`，要么把 `old_string` 写得更具体（比如 `line one: hello`）。

**(d) mtime 冲突——故意在 read 之后改文件：**

真实 Agent 运行时，mtime 检查发生在 diff 渲染之前，所以很难靠手速稳定卡在"模型刚 read 完、还没 edit"的瞬间。先用一段确定性小脚本验证这个纯函数：

```bash
$ uv run python -c "
from pathlib import Path
from agent_code.fs_safety import ReadFileState, check_mtime_conflict

p = Path('dup.txt')
p.write_text('before\\n', encoding='utf-8')
state = ReadFileState()
state.record(p, p.read_text(encoding='utf-8'))
p.write_text('after\\n', encoding='utf-8')
print(check_mtime_conflict(state, p))
"
error: file was modified after read. Read dup.txt again before editing.
```

这证明 `ReadFileState.record()` 记录的是读文件时的 mtime，后续文件被外部程序改过后，`check_mtime_conflict()` 会返回 error。真实 Agent 路径里，这个 error 会作为 `tool_result` 回灌给模型，模型得到后应该重新 `read_file` 再编辑。

这里不做 content-equals 兜底。mtime 变就判冲突，读者再 `read_file` 一次就能刷新状态。这是刻意的简化——省掉 content 缓存的内存开销，也让你先建立"mtime 是读-写之间的一把锁"这个直觉。

## v3：文件历史备份

v2 能安全编辑了，但每次写盘都是覆盖——旧内容丢了就没了。`file_history` 在每次成功写盘前，把文件当前内容快照到 `.agent/history/`，不增加工具、不增加模型感知。

这是 harness 的全局安全网，模型不知道它的存在。

### 3.1 新建 `agent_code/file_history.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def backup(cwd: Path, path: Path, old_content: str) -> Path | None:
    """写盘前把文件旧内容备份到 .agent/history/<rel>/<ts>。
    备份不是工具，模型看不到它——它是 harness 的全局安全网。
    失败不阻塞编辑，返回 None。"""
    try:
        rel = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return None  # 路径在 cwd 外，不备份

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    backup_dir = cwd / ".agent" / "history" / rel
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / ts

    try:
        backup_path.write_text(old_content, encoding="utf-8")
    except OSError:
        return None
    return backup_path
```

### 3.2 改 `agent_code/tools.py`：写盘前调 `backup`

顶部 import 加一行：

```python
from .file_history import backup
```

**在 `file_edit` 里**，把 `path.read_text` 读出来的 `content` 在 `apply_single_replace` 之前传进 `backup`。找到这两行：

```python
    try:
        content = path.read_text(encoding="utf-8")
```

在 `content = path.read_text(encoding="utf-8")` **之后**、`new_content, err = apply_single_replace(...)` **之前**插入一行：

```python
    backup(ctx.cwd, path, content)  # 写盘前备份旧内容
```

**在 `file_write` 里**，如果文件存在，在 `path.write_text` 之前加备份。找到：

```python
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
```

在这两行**之前**插入：

```python
    if path.exists():
        # 备份旧内容（备份失败不阻塞写盘）
        try:
            old = path.read_text(encoding="utf-8")
            backup(ctx.cwd, path, old)
        except Exception:
            pass
```

### 3.3 跑验收

先确认 hello.txt 存在，然后编辑它，再去看 `.agent/history/`：

```bash
$ uv run agent-code "先 read_file hello.txt，再把 hello 改成 hola"
...
tool_call: read_file {'path': 'hello.txt'}
observation: hello from agent

tool_call: file_edit {'file_path': 'hello.txt', 'old_string': 'hello', 'new_string': 'hola'}

Diff for hello.txt:
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello from agent
+hola from agent
Apply this edit to hello.txt? [y/N]: y
observation: Edited hello.txt: replaced 5 chars with 4 chars
final: 成功将 hello.txt 中的 hello 改成了 hola。
```

```bash
$ ls .agent/history/hello.txt/
20260526T120000.123Z
```

```bash
$ cat .agent/history/hello.txt/20260526T120000.123Z
hello from agent
```

看到 `hello from agent`——旧内容被原样保存了。文件名是 ISO 时间戳（精确到毫秒），每次编辑留一份快照。

**文件备份不是工具**。模型不能调它、不能读它、prompt 里不会出现它。它的角色类似于编辑器的本地历史或 git reflog——只对开发者可见，属于 harness 基础设施。我们把备份放在写盘前调用，是为了保证只要文件要被覆盖，旧内容就先有一份快照。

## 收尾：今天的终版文件改动清单

今天动了 6 个文件，新增 2 个。不重新贴整文件了，列出每个文件的改动段：

| 文件 | 新增 | 改动 |
|---|---|---|
| `agent_code/diff_ui.py` | 新文件：`render_diff` + `confirm_edit` | — |
| `agent_code/file_history.py` | 新文件：`backup` | — |
| `agent_code/fs_safety.py` | `ensure_read_before_edit` + `check_mtime_conflict` + `apply_single_replace` | — |
| `agent_code/tools.py` | `file_write` + `file_edit` + 两个注册 + import `backup` 和三个 safety 函数 | 升级 `file_write` 的 read-before-edit 从内联到调 `ensure_read_before_edit` + 补 mtime 检查 + 加 backup 调用 |
| `agent_code/agent.py` | import `ToolResult`、`resolve_in_cwd`、`apply_single_replace`、`render_diff`、`confirm_edit`、`Console` | 在工具循环里插入 ~20 行拦截块，对 `file_write` / `file_edit` 出 diff 并确认 |
| `agent_code/cli.py` | — | `run_once` 不再重复打印 trace，因为 `run_agent` 已经流式输出 |

今天不需要改 `model.py`。`ToolContext` 的 `read_state` 字段 Day 3 就在了，今天只是真正读它。

## 手动 trace 一遍

输入 `"用 file_edit 把 README.md 标题改成 # Agent Code"`，从 CLI 到写盘完整的链路：

```txt
1. CLI 解析 prompt、cwd、provider，进入 run_agent。
2. Agent Loop 把 prompt + 工具列表发给模型。
3. 模型返回 tool_use: read_file {"path": "README.md"}。
   —— 模型被训练成"编辑前先读"，harness 也强制要求。
4. read_file 执行：fs_safety 校验通过 → 读内容 → truncate → record(read_state, mtime)。
5. Agent Loop 把 read_file 的 tool_result 打包进下一条 user message。
6. 模型看到 README 内容，返回：
   tool_use: file_edit {"file_path": "README.md", "old_string": "# My Project", "new_string": "# Agent Code"}
7. Agent Loop 进入 file_edit 拦截块：
   a. resolve_in_cwd → 锁在 cwd 内
   b. 从磁盘读 old_content
   c. 内存试跑 apply_single_replace(old_content, "# My Project", "# Agent Code", False) → 成功
   d. render_diff(old_content, new_content, "README.md") → rich 着色 unified diff
   e. confirm_edit("README.md") → 终端输出 Diff，等用户按 y/N
8. 用户按 y。
9. tools.run(file_edit) 进入工具函数体：
   a. ensure_read_before_edit → read_state 有记录，通过
   b. check_mtime_conflict → 盘上 mtime 没变，通过
   c. path.read_text → 拿到当前内容
   d. backup(ctx.cwd, path, content) → 旧内容写入 .agent/history/README.md/<ts>
   e. apply_single_replace → 精确匹配 1 次，生成 new_content
   f. path.write_text(new_content) → 落盘
   g. ctx.read_state.record(path, new_content) → 刷新 mtime
10. 返回 observation: "Edited README.md: replaced 10 chars with 11 chars"
11. Agent Loop 打包 tool_result，发起下一次模型请求。
12. 模型看到编辑成功，返回 final 文本。
```

两步关键 harness 介入：(7) 出 diff 问人，(9b) 复查 mtime。两步之间如果有人在另一个终端改了 README.md，(9b) 会拦住，(9e) 在 diff 生成之后再次读盘并匹配时也会兜底拦住——即使你按了 y，落盘前还有两道门。

## 今天有了什么

- **`file_write` + `file_edit`**：两个写工具，一个整文件覆盖、一个字符串替换。模型有了"改代码"的能力，但必须通过 harness 的检查链。
- **read-before-edit + mtime 冲突检测**：从 Day 3 的 `ReadFileState` 钩子出发，今天把它变成了两道实打实的安全门。模型不先读就不能写；读完之后文件被外部改了也会被拦下。
- **diff preview + y/N 确认**：每次写盘前，harness 渲染 unified diff 到终端，你亲自决定要不要让这次编辑通过。今天先用最小的 y/N 确认；Day 5 的权限系统会把确认升级成完整的 `PermissionDecision`。
- **文件历史备份**：每次成功写盘前，旧内容自动存到 `.agent/history/<rel>/<ts>`。模型不知道它的存在——它是 harness 的安全网。
- **fs_safety 边界扩展到写路径**：Day 3 的 `fs_safety.py` 只管读（cwd 锁定、二进制拒绝、大小、截断、skip），今天新增的三个纯函数把写路径的安全检查也统一收进了这个文件。

## 常见问题

### `file_edit` 报 `string to replace not found in file`，但我明明看到模型给的就是文件里的字

大概率是缩进不对。模型拿到的 `read_file` 输出前有行号前缀（Day 3 没加，但如果用了 `cat -n` 之类的），或者模型自己在脑子里加了空格。让模型再读一次目标行，用更完整的上下文当 `old_string`。或者先用 `grep` 把目标行的 exact text 捞出来，再传给 `file_edit`。

### `old_string` 和 `new_string` 一模一样也报错

`apply_single_replace` 第一行就拦了 `old == new`。这通常是模型在"空编辑"——它觉得文件需要改，但给的 old 和 new 其实是同一段。让模型重试，给出真正有差异的 old/new。

### 按了 `n` 之后模型怎么处理

Agent Loop 把 `error: edit rejected by user` 当作一条 `is_error=True` 的 tool_result 交回给模型。模型看到这条 error observation 通常会给一个替代方案——比如换个写法、用别的工具、或者直接回答"我无法修改"。这和 read-before-edit / mtime 冲突 error 的处理一致：error 是 feedback，驱动下一轮推理。

### 为什么 "先 read 再在另一个终端改文件" 会报 mtime 冲突，但 "先 read 再不改内容只 touch" 也会报

Day 3 的 `ReadFileState` 只存 `(mtime_ns, char_count)`，不存文件内容。所以它只看 mtime 变了没，不判断内容是不是真的被改过。`touch` 会改变 mtime，所以这里也会被判为冲突。再 `read_file` 一次就能刷新记录。这是刻意的简化：先不做 content-equals 兜底，让你建立"mtime 是读写之间的锁"这个直觉。

### 不同文件系统上的 `st_mtime_ns` 精度问题

`st_mtime_ns` 是 Python 暴露的纳秒整数，但底层文件系统不一定真的有纳秒级分辨率。某些平台或网络盘上，短时间内连续 read/edit 可能出现误判。碰到这种情况，最简单的 workaround 是在 `check_mtime_conflict` 里加一个小容忍窗口（比如 1 秒），或者改成同时缓存文件内容 hash。今天先不内置这个逻辑，保持 mtime 锁的模型足够直观。

## 课后挑战

1. **实现 rewind**：给 `file_history` 加一个函数 `restore(cwd, path, ts) -> None`，从 `.agent/history/<rel>/<ts>` 读回旧内容覆盖当前文件。再给 CLI 加一个 slash 命令 `/rewind <path> <ts>`。
2. **`--permission-mode acceptEdits` 雏形**：在 `agent.py` 的拦截块里加一个 `permission_mode` 参数。`acceptEdits` 模式下跳过 `confirm_edit`，直接走 `tools.run`。`default` 模式保持现在 y/N。
3. **curly quote 归一化**：模型偶尔会把 `"hello"`（直引号）写成 `“hello”`（弯引号），导致 `old_string` 匹配失败。在 `apply_single_replace` 里加一个预处理，把 content 和 old_string 里的弯引号统一换成直引号再匹配。
4. **`.agent/history/` 容量上限**：给 `file_history.py` 加一个 `MAX_BACKUPS_PER_FILE = 50`。备份前检查目录里已有多少个快照，超过就删最旧的，保证不会无限膨胀。
5. **加一个 `file_diff` 工具**：只出 diff、不写盘。让模型能在调用 `file_edit` 之前先对比两个文件，或者比较当前文件和历史快照——帮助模型做更精准的编辑决策。
6. **搜索 + 编辑组合**：让模型先用 `grep` 找出所有匹配行，再用 `file_edit` 逐个改。对比单次 `replace_all` 行为差异，思考为什么 harness 不把搜索和替换合进一个工具。

## 思考题

1. **read-before-edit 为什么不直接拒绝工具的调用，而是返回结构化 error 给模型？** 答案是"harness 不替模型做决策"。如果把调用彻底吞掉，模型就不知道发生了什么，也学不会"先读再写"这条规则。error 是 feedback——它告诉模型你缺了一步，模型可以自己补。

2. **diff preview 为什么是 CLI/harness 层的交互，而不是 `file_edit` / `file_write` 工具内部的功能？** 

3. **mtime 冲突检测为什么先不做 content-equals 兜底？** 如果有人在 read 和 edit 之间 `touch` 了文件（mtime 变了但内容没变），今天的实现会报冲突。这在实际使用中有什么影响？如果要补一个兜底，你会把文件内容缓存放在哪里？

4. **为什么 `file_history` 不做成模型可见的工具？** 如果让模型能调 `restore` 或 `list_snapshots`，Agent 的行为会发生什么变化？

## 下一天

今天 Agent 能安全改文件了。每次写盘前，harness 出 diff、让你按 y/N、备份旧内容——一套完整的读写保护链。

下一天我们要让 Agent 跑命令：`bash` 工具 + 权限引擎。模型可以调 `bash` 跑任意 shell 命令，但运行前要经过 `PermissionRequest` → `PermissionDecision` 的完整决策链。今天的 y/N 确认会被抽象成 `PermissionDecision.ask`，再加上超时、白名单、输出截断和 cwd 固定——做出一个真正能管理"让不让模型跑命令"的权限系统。
