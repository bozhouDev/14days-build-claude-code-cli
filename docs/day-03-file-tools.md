# Day 3：File Tools + Web Tools

Day 2 我们接入了真实模型和 tool calling，但 Agent 还是个瞎子——它看不见项目里的代码，也拿不到外部世界的信息。今天给它装上眼睛。

- 先接本地：`read_file` 读文件、`list_files` 列目录、`glob` 按名字找、`grep` 按内容搜、`project_tree` 一张全景图看清项目结构。
- 再接外部：`web_fetch` 抓网页、`web_search` 搜东西。
- 跑完之后，你问"这个项目的入口文件在哪里？"它会先 `list_files` → 再 `read_file pyproject.toml` → 再回答；你问"PEP 8 里最重要的 4 条规则是什么？"它会先 `web_fetch` 抓页面 → 再回答。

今天从 Day 2 的 `agent-code` 项目继续改。仓库里的 `packages/day-*` 是参考答案快照，**不是**让你每天新建一个目录。本文是当天的主线实现说明，按教程从 v1 走到 v4 即可。

## 起手：今天的起点

Day 2 已经有 `AnthropicProvider`、多步 Agent Loop、`echo` 和 `system_date` 两个工具。今天我们围绕这个骨架加四件事：

- 一个新模块 `fs_safety.py`，集中放"文件系统边界"。
- 七个新工具：`read_file`、`list_files`、`glob`、`grep`、`project_tree`、`web_fetch`、`web_search`。
- 工具签名多接一个 `ctx: ToolContext`，让工具能拿到 `cwd`、skip 规则、`readFileState` 这些**运行时上下文**。
- 两个新依赖：`pathspec`（解析 `.gitignore`）、`html2text`（把 HTML 转 markdown）。`httpx` 在 Day 2 已经装过。

先装：

```bash
uv add pathspec html2text
```

提前点个坑：v1 我们会把 `Tool.run` 的签名扩成 `Callable[[dict, ToolContext], str]`。这意味着 Day 2 的 `echo` 和 `system_date` 函数头要顺手加一个 `ctx` 形参。老逻辑不动，只是签名对齐。

## v1：`read_file` + `list_files`

先让 Agent 能读一个文件，再能列一层目录。这一版要把 `fs_safety.py` 的骨架一次铺好：cwd 边界、文本判断、单文件大小、输出截断、skip 规则、`ReadFileState`。Skip 规则一开始就要有，否则 `list_files` 在项目根目录一跑，`.venv/`、`__pycache__/`、`.git/` 会瞬间把上下文塞满。

### 1.1 新建 `agent_code/fs_safety.py`

这是今天新增的第一个文件。它的职责很窄：**任何工具碰文件之前，都从这里要一个安全的 `Path`，或者一个被截断过的字符串。**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# 文本文件后缀白名单：直接放行，不用 peek 文件头。
TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".cfg", ".ini", ".env", ".sh", ".bash", ".zsh", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".sql", ".lock", ".gitignore",
}

# 单文件大小上限：超过就拒绝读取整文件。教学版先用 256 KiB，后面再做 offset/limit。
MAX_READ_BYTES = 256 * 1024

# 单次工具 observation 上限。模型上下文有限，过长直接截尾。
DEFAULT_MAX_CHARS = 8000

# 默认跳过的目录名。任意祖先目录命中名单，整条路径都被剔除。
DEFAULT_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "dist", "build",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


@dataclass
class SkipPolicy:
    # 默认 skip 集合。v2 还会塞 .gitignore matcher 进来。
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS

    @classmethod
    def default(cls) -> "SkipPolicy":
        return cls()


@dataclass
class ReadFileState:
    # path -> (mtime_ns, char_count)。Day 4 的 read-before-edit 要靠它判断
    # "模型读过这个文件之后，文件在磁盘上是不是又被改过"。今天先只做记录。
    entries: dict[Path, tuple[int, int]] = field(default_factory=dict)

    def record(self, path: Path, content: str) -> None:
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return
        self.entries[path] = (mtime_ns, len(content))


def resolve_in_cwd(cwd: Path, user_path: str) -> Path:
    # 把模型给的相对路径解析成绝对路径，并强制锁回 cwd 子树。
    # 越界直接抛错，由调用方包成 observation。
    candidate = (cwd / user_path).resolve()
    cwd_resolved = cwd.resolve()
    try:
        candidate.relative_to(cwd_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes cwd: {user_path}") from exc
    return candidate


def ensure_text_file(path: Path) -> None:
    # 白名单后缀直接放行；其余文件 peek 首 1 KB，看到 NUL 就当二进制拒绝。
    if path.suffix.lower() in TEXT_SUFFIXES:
        return
    with path.open("rb") as f:
        if b"\x00" in f.read(1024):
            raise ValueError(f"binary file: {path.name}")


def ensure_within_size(path: Path, max_bytes: int = MAX_READ_BYTES) -> None:
    # 整文件读取的硬上限。Day 3 不做 offset/limit，超限直接拒绝，
    # 课后挑战里再补按行读。
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"file too large: {size} bytes > {max_bytes}; "
            f"read a smaller file or use grep instead"
        )


def should_skip(rel_path: Path, policy: SkipPolicy) -> bool:
    return any(part in policy.skip_dirs for part in rel_path.parts)


def truncate_output(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated {len(text) - max_chars} chars]"
```

所有函数都是同步、纯函数，不接触全局状态。`ReadFileState` 是当天唯一带状态的对象，但它只有 `record()`，不暴露给工具，由 `read_file` 调用。

### 1.2 改 `agent_code/tools.py`：引入 `ToolContext`

工具现在要知道当前 cwd、skip 规则、`ReadFileState`。我们不让每个工具去摸全局，而是包一个 `ToolContext` dataclass，由 Agent Loop 在调用工具时显式传进来。

把 `agent_code/tools.py` 顶部的 import 段从：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .model import ToolCall, ToolResult
```

改成：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .fs_safety import (
    ReadFileState,
    SkipPolicy,
    ensure_text_file,
    ensure_within_size,
    resolve_in_cwd,
    should_skip,
    truncate_output,
)
from .model import ToolCall, ToolResult
```

然后在 `ToolFunc = ...` 那一行**之前**，新增 `ToolContext`，并把 `ToolFunc` 的签名加一个 `ToolContext` 参数：

```python
@dataclass
class ToolContext:
    # 工具运行时上下文。Day 3 装 cwd、skip 规则、ReadFileState；后面天会塞更多。
    cwd: Path
    skip_policy: SkipPolicy = field(default_factory=SkipPolicy.default)
    read_state: ReadFileState = field(default_factory=ReadFileState)


ToolFunc = Callable[[dict[str, Any], ToolContext], str]
```

`Tool` dataclass 不用改 —— `run` 字段的类型注解依然是 `ToolFunc`，只是 `ToolFunc` 自己变了。

> **顺序说明**：先写 `class ToolContext`，再写 `ToolFunc = Callable[[dict[str, Any], ToolContext], str]`。第二行是普通赋值，执行时 Python 会立刻去找 `ToolContext`，所以类必须写在上面。
>
> 文件顶部的 `from __future__ import annotations`（Day 1 就有）别删。它管的是函数参数、类字段上的类型标注（比如 `ctx: ToolContext`），让这些标注不会在定义时立刻报错。和 `ToolFunc = ...` 那行不是同一机制，但整个文件都在用类型标注，这行 import 要保留。

`echo` 和 `system_date` 两个老工具改成新签名。`ctx` 形参先留着不用：

```python
def echo(args: dict[str, Any], ctx: ToolContext) -> str:
    return str(args.get("text", ""))


def system_date(args: dict[str, Any], ctx: ToolContext) -> str:
    # system_date 是模型看不到系统时钟时，需要向 harness 请求的能力。
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
```

在 `system_date` 函数和 `class ToolRegistry` 之间插入 `read_file` 和 `list_files`。后面 v2/v3/v4 还会往这段里继续塞 5 个工具函数，今天 `tools.py` 的最终顺序是 **工具函数 → `ToolRegistry` → `default_tools`**：

```python
def read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    # 模型只给相对路径；fs_safety 把它锁回 cwd 内，探测二进制，再卡大小上限。
    path_str = args.get("path", "")
    if not path_str:
        return "error: missing required argument 'path'"
    try:
        path = resolve_in_cwd(ctx.cwd, path_str)
        ensure_text_file(path)
        ensure_within_size(path)
        text = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        return f"error: {exc}"

    # 记录"模型看到的版本"。Day 4 的 file_edit 会比对 mtime，判断是否被改过。
    ctx.read_state.record(path, text)
    return truncate_output(text)


def list_files(args: dict[str, Any], ctx: ToolContext) -> str:
    path_str = args.get("path", ".")
    try:
        base = resolve_in_cwd(ctx.cwd, path_str)
    except ValueError as exc:
        return f"error: {exc}"
    if not base.is_dir():
        return f"error: not a directory: {path_str}"

    entries: list[str] = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        rel = child.relative_to(ctx.cwd)
        if should_skip(rel, ctx.skip_policy):
            continue
        entries.append(f"{child.name}/" if child.is_dir() else child.name)
    return truncate_output("\n".join(entries) or "(empty)")
```

然后把 `ToolRegistry` 里的 `run()` 方法改成下面这样。这里有两个点要一起改：方法签名多接 `ctx`，最后执行工具时也要把 `ctx` 传给 `tool.run()`。

```python
def run(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
    # 未知工具也返回 observation，不让 Agent Loop 崩掉。
    tool = self._tools.get(call.name)
    if tool is None:
        return ToolResult(
            tool_call_id=call.id,
            content=f"unknown tool: {call.name}",
            is_error=True,
        )
    return ToolResult(tool_call_id=call.id, content=tool.run(call.arguments, ctx))
```

`default_tools()` 在 `system_date` 那一行**之后**追加两段注册：

```python
registry.register(
    Tool(
        name="read_file",
        description="Read a text file inside the project. Path is relative to cwd.",
        run=read_file,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path inside cwd."},
            },
            "required": ["path"],
        },
    )
)
registry.register(
    Tool(
        name="list_files",
        description="List files and directories at a path inside cwd.",
        run=list_files,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path; defaults to '.'.",
                    "default": ".",
                },
            },
            "required": [],
        },
    )
)
```

### 1.3 改 `agent_code/agent.py`：把 `cwd` 串进 Agent Loop

`run_agent` 多接一个 `cwd` 参数，构造 `ToolContext`，再传给 `tools.run`。顶部 import 改成：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import ModelProvider, ModelResponse
from .tools import ToolContext, ToolRegistry
```

把 Day 2 的整个 `run_agent` 函数（包括签名和函数体；Day 2 里它长这样：`def run_agent(prompt: str, provider: ModelProvider, tools: ToolRegistry, max_steps: int = 8) -> AgentResult:`）替换成下面这版。两处变化：签名末尾多了 `cwd: Path | None = None`；函数顶部多了一行构造 `ToolContext` 并把 `ctx` 传给 `tools.run`：

```python
def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
) -> AgentResult:
    ctx = ToolContext(cwd=cwd or Path.cwd())
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    trace: list[str] = []

    for step in range(max_steps):
        response = provider.complete(messages, tools=tools.list())
        messages.append(_assistant_message(response))

        if not response.tool_calls:
            final = response.text or ""
            trace.append(f"final: {final}")
            return AgentResult(final=final, trace=trace, messages=messages)

        for call in response.tool_calls:
            trace.append(f"tool_call: {call.name} {call.arguments}")
            result = tools.run(call, ctx)
            trace.append(f"observation: {result.content}")
            messages.append(_tool_result_message(result.tool_call_id, result.content, result.is_error))

    final = f"reached max_steps={max_steps}"
    trace.append(f"final: {final}")
    return AgentResult(final=final, trace=trace, messages=messages)
```

`_assistant_message` 和 `_tool_result_message` 这两个 helper 一行都不用改。

### 1.4 改 `agent_code/cli.py`：把 `resolved_cwd` 透传给 `run_agent`

`run_once()` 里 Day 2 调用：

```python
result = run_agent(prompt, provider, default_tools(), max_steps=max_steps)
```

替换成：

```python
result = run_agent(prompt, provider, default_tools(), max_steps=max_steps, cwd=cwd)
```

REPL 分支（注释1）只需要保留 Day 2 收尾阶段已经传 `provider/model/base_url/max_steps` 那几行，不用动。

### 1.5 跑一下

在你的 `agent-code` 项目根目录执行：

```bash
$ uv run agent-code "用 read_file 工具读 pyproject.toml 的前几行，然后告诉我项目名"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: read_file {'path': 'pyproject.toml'}
observation: [project]
name = "agent-code"
version = "0.1.0"
...
final: 项目名是 agent-code。
```

```bash
$ uv run agent-code "用 list_files 列出项目顶层目录"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: list_files {'path': '.'}
observation: agent_code/
tests/
README.md
pyproject.toml
uv.lock
final: 项目顶层目录有 agent_code/、tests/，以及 README.md、pyproject.toml、uv.lock。
```

注意第二条 `observation` 里**没有** `.venv/`、`__pycache__/`、`.git/` —— 那些目录在本机一定存在，被 `SkipPolicy` 兜底过滤掉了。

v1 现在能读文件、能列目录。但模型只要被问"哪些文件里写了 TODO"，它就抓瞎 —— 它不能按文件名 pattern 找，也不能按内容搜。v2 解决它。

## v2：`glob` + `grep` + `.gitignore` 过滤

这一版做两件事：

- 把 `.gitignore` 接进 `SkipPolicy`，让 skip 规则不再只是硬编码列表。
- 加 `glob`（按文件名找）和 `grep`（按内容找）两个工具。`grep` 优先调用 `ripgrep`，找不到就退化成纯 Python。

### 2.1 改 `fs_safety.py`：加 `load_gitignore`，扩展 `SkipPolicy`

顶部 import 加一行：

```python
import pathspec
```

把 `SkipPolicy` 改成下面这样（多一个 `gitignore` 字段和 `default()` 参数）：

```python
@dataclass
class SkipPolicy:
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS
    gitignore: pathspec.PathSpec | None = None

    @classmethod
    def default(cls, gitignore: pathspec.PathSpec | None = None) -> "SkipPolicy":
        return cls(gitignore=gitignore)
```

`should_skip` 多一段 `.gitignore` 判定：

```python
def should_skip(rel_path: Path, policy: SkipPolicy) -> bool:
    if any(part in policy.skip_dirs for part in rel_path.parts):
        return True
    if policy.gitignore is not None and policy.gitignore.match_file(str(rel_path)):
        return True
    return False
```

在文件末尾新增 `load_gitignore`：

```python
def load_gitignore(cwd: Path) -> pathspec.PathSpec | None:
    # 只读 cwd 根的 .gitignore；嵌套 gitignore 留作课后挑战。
    gitignore = cwd / ".gitignore"
    if not gitignore.exists():
        return None
    lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)
```

### 2.2 改 `tools.py`：新增 `glob` 和 `grep`

顶部 import 加 `re` 和 `shutil`、`subprocess`：

```python
import re
import shutil
import subprocess
```

在 `list_files` **之后**新增两个工具：

```python
def glob(args: dict[str, Any], ctx: ToolContext) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "error: missing required argument 'pattern'"

    matches: list[Path] = []
    try:
        for path in ctx.cwd.rglob(pattern):
            rel = path.relative_to(ctx.cwd)
            if should_skip(rel, ctx.skip_policy):
                continue
            matches.append(path)
    except NotImplementedError as exc:
        return f"error: {exc}"
    # 按 mtime 倒序，让"最近改过的文件"排在前面。
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    matches = matches[:200]

    lines = [str(p.relative_to(ctx.cwd)) for p in matches]
    return truncate_output("\n".join(lines) or "(no matches)")


def grep(args: dict[str, Any], ctx: ToolContext) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "error: missing required argument 'pattern'"
    path_arg = args.get("path", ".")
    glob_arg = args.get("glob")
    ignore_case = bool(args.get("ignore_case", False))

    try:
        base = resolve_in_cwd(ctx.cwd, path_arg)
    except ValueError as exc:
        return f"error: {exc}"

    # 系统装了 ripgrep 就走它，速度差一个数量级；否则退化纯 Python。
    if shutil.which("rg"):
        return _grep_ripgrep(pattern, base, glob_arg, ignore_case, ctx)
    return _grep_python(pattern, base, glob_arg, ignore_case, ctx)


def _grep_ripgrep(
    pattern: str,
    base: Path,
    glob_arg: str | None,
    ignore_case: bool,
    ctx: ToolContext,
) -> str:
    # ripgrep 自带 .gitignore 解析和 VCS 目录跳过，我们只需要追加自定义 skip。
    args: list[str] = ["rg", "--line-number", "--no-heading", "--max-columns", "500"]
    if ignore_case:
        args.append("-i")
    for name in ctx.skip_policy.skip_dirs:
        args.extend(["--glob", f"!{name}/**"])
    if glob_arg:
        args.extend(["--glob", glob_arg])
    args.append(pattern)
    # rg 必须收绝对路径才能让 --glob 的相对规则可预测；
    # 但输出给模型前要把每行的绝对前缀切回相对路径，省 token、和 _grep_python 保持一致。
    args.append(str(base))
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"error: {exc}"

    # ripgrep 没匹配会返回 exit code 1，这不是错；真错才看 stderr。
    if proc.returncode not in (0, 1):
        return f"error: rg: {proc.stderr.strip() or proc.returncode}"
    return truncate_output(_relativize_rg_output(proc.stdout, ctx.cwd) or "(no matches)")


def _relativize_rg_output(stdout: str, cwd: Path) -> str:
    # rg 每行形如 "/abs/path:lineno:content"。命中 cwd 前缀的就切成相对路径，
    # 不命中（罕见）原样保留，避免吞掉模型可能想看到的诊断信息。
    cwd_prefix = f"{cwd}/"
    lines = [
        line[len(cwd_prefix):] if line.startswith(cwd_prefix) else line
        for line in stdout.splitlines()
    ]
    return "\n".join(lines).strip()


def _grep_python(
    pattern: str,
    base: Path,
    glob_arg: str | None,
    ignore_case: bool,
    ctx: ToolContext,
) -> str:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return f"error: invalid regex: {exc}"

    if base.is_file():
        candidates: list[Path] = [base]
    else:
        candidates = []
        try:
            for path in base.rglob(glob_arg or "*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(ctx.cwd)
                if should_skip(rel, ctx.skip_policy):
                    continue
                candidates.append(path)
        except NotImplementedError as exc:
            return f"error: {exc}"

    hits: list[str] = []
    for path in candidates:
        try:
            ensure_text_file(path)
        except ValueError:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(ctx.cwd)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{rel}:{lineno}:{line}")
    return truncate_output("\n".join(hits) or "(no matches)")
```

`default_tools()` 在 `list_files` 注册**之后**再追加两段：

```python
registry.register(
    Tool(
        name="glob",
        description="Find files by glob pattern, e.g. '**/*.py'.",
        run=glob,
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern."},
            },
            "required": ["pattern"],
        },
    )
)
registry.register(
    Tool(
        name="grep",
        description="Search file contents with a regular expression (ripgrep if available).",
        run=grep,
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression."},
                "path": {
                    "type": "string",
                    "description": "Relative path; defaults to '.'.",
                    "default": ".",
                },
                "glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.py'."},
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive match.",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    )
)
```

### 2.3 改 `agent.py`：接进 `.gitignore`，并把同一轮的 `tool_result` 打包

`agent.py` 这一步要改两处：把 `.gitignore` 接进 `ToolContext`；把内循环改成"先收齐 `tool_result` block，循环外一次性 `append` 一条 user 消息"。

**第一处：接 `.gitignore`**。顶部 import 区域加一行：

```python
from .fs_safety import SkipPolicy, load_gitignore
```

把 `run_agent` 里 `ctx = ToolContext(cwd=cwd or Path.cwd())` 这一行换成三行：

```python
resolved_cwd = cwd or Path.cwd()
ctx = ToolContext(
    cwd=resolved_cwd,
    skip_policy=SkipPolicy.default(gitignore=load_gitignore(resolved_cwd)),
)
```

**第二处：归一化 `tool_result`**。v1 那个内循环"每跑完一个工具就 `append` 一条 user 消息"，到 v2 加进 `grep` 后会立刻挂——你跑 `uv run agent-code "用 grep 找出所有 TODO，再总结"` 会看到：

```txt
BadRequestError: 400 - messages.1:`tool_use` ids were found without
`tool_result` blocks immediately after: call_01_... Each `tool_use` block
must have a corresponding `tool_result` block in the next message.
```

原因是 Anthropic Messages API 要求：同一轮 assistant 里所有 `tool_use` 对应的 `tool_result`，必须打包进**紧接着的那一条** user 消息。grep 上线后，模型更容易在同一轮里发出多个 `tool_use`；一个结果一条 user 消息地塞回去，就会被 API 拒绝。

这一步的目的是**把"调几次工具"和"发几条消息"解耦**：调用次数由模型决定，但每一轮 assistant 之后我们只回填一条 user 消息，里头按顺序装齐这一轮所有 `tool_result` block。把 `run_agent` 里 `for call in response.tool_calls:` 这一段（v1.3 的最内层循环，一直到 `messages.append(_tool_result_message(...))` 那行）整段替换成：

```python
        tool_result_blocks: list[dict[str, Any]] = []
        for call in response.tool_calls:
            trace.append(f"tool_call: {call.name} {call.arguments}")
            result = tools.run(call, ctx)
            trace.append(f"observation: {result.content}")
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

Day 2 留下的 `_tool_result_message` 因为签名假设"一条结果一条消息"，从这一步起 Agent Loop 不再调用，留着或删都可以。

### 2.4 跑两个验收

```bash
$ uv run agent-code "用 grep 找出所有 TODO，再总结"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: grep {'pattern': 'TODO'}
observation: agent_code/cli.py:42:    # TODO: 引入 slash 命令注册系统
final: 项目里找到 TODO，例如 cli.py 第 42 行计划引入 slash 命令注册系统。
```

```bash
$ uv run agent-code "用 glob 找出所有 Python 文件，告诉我有几个"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: glob {'pattern': '**/*.py'}
observation: agent_code/cli.py
agent_code/tools.py
agent_code/agent.py
agent_code/model.py
agent_code/fs_safety.py
agent_code/__init__.py
tests/test_smoke.py
final: 一共 7 个 Python 文件。
```

具体命中行和数量会随你的项目变。重点是看到 `tool_call: glob` / `tool_call: grep` 和按 `path:line:content` 格式排好的结果。系统装了 `ripgrep` 时走外部进程，没装就走纯 Python；输出格式刻意保持一致，方便模型解析。

### 2.5 反向验证：cwd 越界会被拦下

故意让模型试一个越界路径，看 `resolve_in_cwd` 是不是真兜底：

```bash
$ uv run agent-code "请用 read_file 读 ../../../etc/passwd"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: read_file {'path': '../../../etc/passwd'}
observation: error: path escapes cwd: ../../../etc/passwd
final: 这个路径在工作目录之外，工具拒绝读取。
```

到这里，本地工具能读、能列、能找名字、能搜内容，且都遵守 cwd 边界和 `.gitignore`。v3 再加一个便利工具：一次拿到整张项目结构图。

## v3：`project_tree`

`list_files` 只看一层。模型想了解整个项目结构时，会反复调它，又慢又费 token。`project_tree` 是一个"高密度全景图"工具：一次调用，输出受控深度的目录树。

### 3.1 改 `tools.py`：新增 `project_tree`

在 `grep` 之后新增：

```python
def project_tree(args: dict[str, Any], ctx: ToolContext) -> str:
    max_depth = int(args.get("max_depth", 3))
    max_nodes = 200
    lines: list[str] = [f"{ctx.cwd.name}/"]
    nodes = 0

    def walk(directory: Path, depth: int) -> None:
        nonlocal nodes
        if depth > max_depth:
            return
        children = sorted(
            (
                c for c in directory.iterdir()
                if not should_skip(c.relative_to(ctx.cwd), ctx.skip_policy)
            ),
            key=lambda p: (not p.is_dir(), p.name),
        )
        for child in children:
            if nodes >= max_nodes:
                if nodes == max_nodes:
                    lines.append("  " * depth + "...[truncated]")
                    nodes += 1
                return
            suffix = "/" if child.is_dir() else ""
            lines.append("  " * depth + child.name + suffix)
            nodes += 1
            if child.is_dir():
                walk(child, depth + 1)

    walk(ctx.cwd, 1)
    return truncate_output("\n".join(lines))
```

`default_tools()` 末尾追加：

```python
registry.register(
    Tool(
        name="project_tree",
        description="Show the project directory tree from cwd.",
        run=project_tree,
        parameters={
            "type": "object",
            "properties": {
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum recursion depth.",
                    "default": 3,
                },
            },
            "required": [],
        },
    )
)
```

### 3.2 跑一下

```bash
$ uv run agent-code "用 project_tree 画一下项目结构，max_depth 设 2"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: project_tree {'max_depth': 2}
observation: your-project/
  agent_code/
    __init__.py
    agent.py
    cli.py
    fs_safety.py
    model.py
    tools.py
  README.md
  pyproject.toml
  uv.lock
final: ...
```

`project_tree` 节点封顶 200，深度封顶 3，输出再过 `truncate_output`。哪怕仓库有十万文件，也不会冲爆上下文。

到这里本地能力已经齐了：读、列、找名字、搜内容、画全景。下一步给 Agent 加"上网"的能力。

## v4：`web_fetch` + `web_search`

Agent 现在只看得见 cwd 里的代码。让它能去外部世界取信息，需要两件事：

- `web_fetch`：给一个 URL，抓回页面正文，转成 markdown 喂给模型。
- `web_search`：给一组关键词，返回搜索引擎前 N 条结果（标题 + URL）。

`web_fetch` 的核心边界都在 `httpx.get(...)` 之前完成（URL 校验、scheme 限制、超长拒绝），抓回来再过 `html2text` 转 markdown，最后截断。`web_search` 默认走 DuckDuckGo 的 HTML 端点做兜底；任何时候你都可以把它换成 Tavily/Serper/Brave。

这两个工具都很小，但职责不一样：

```txt
web_fetch:  validate url -> httpx.get -> 按 content-type 转文本 -> truncate_output
web_search: query -> DuckDuckGo HTML -> 抽 title/url -> 解包跳转链接 -> truncate_output
```

注意它们都不让模型直接碰网络细节。模型只会说"我要抓这个 URL"或"我要搜这个 query"；真正的 URL 校验、超时、content-type 判断、搜索结果清洗都在 harness 工具里完成。

### 4.1 改 `tools.py`：新增 `web_fetch`

顶部 import 多两行：

```python
from urllib.parse import parse_qs, unquote, urlparse

import html2text
import httpx
```

在 `project_tree` 之后新增：

```python
# Web 工具的硬约束放在这里，和 fs_safety 的常量一样不外泄到调用点。
WEB_USER_AGENT = "agent-code/0.1 (+https://example.com/agent-code)"
WEB_FETCH_MAX_BYTES = 10 * 1024 * 1024
WEB_FETCH_MAX_CHARS = 20_000
WEB_URL_MAX_LENGTH = 2000
WEB_FETCH_TIMEOUT_S = 30.0
WEB_SEARCH_TIMEOUT_S = 15.0


def _validate_url(url: str) -> None:
    # URL 校验是 web_fetch 的第一道门，所有失败都在 httpx 真正发请求之前。
    if len(url) > WEB_URL_MAX_LENGTH:
        raise ValueError(f"url too long: {len(url)} > {WEB_URL_MAX_LENGTH}")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme or '(none)'}")
    if parsed.username or parsed.password:
        raise ValueError("url with credentials is not allowed")
    if not parsed.hostname or "." not in parsed.hostname:
        raise ValueError(f"invalid hostname: {parsed.hostname}")


def _html_to_markdown(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.body_width = 0  # 关掉硬换行，保留模型上下文里更长的段落。
    converter.ignore_images = True
    converter.ignore_emphasis = False
    return converter.handle(html).strip()


def web_fetch(args: dict[str, Any], ctx: ToolContext) -> str:
    url = args.get("url", "")
    if not url:
        return "error: missing required argument 'url'"
    try:
        _validate_url(url)
    except ValueError as exc:
        return f"error: {exc}"

    headers = {"User-Agent": WEB_USER_AGENT, "Accept": "text/html,text/*;q=0.9,*/*;q=0.5"}
    try:
        with httpx.Client(timeout=WEB_FETCH_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"error: {exc}"

    if len(resp.content) > WEB_FETCH_MAX_BYTES:
        return f"error: response too large: {len(resp.content)} > {WEB_FETCH_MAX_BYTES}"

    content_type = resp.headers.get("content-type", "").lower()
    if "text/html" in content_type or "application/xhtml" in content_type:
        body = _html_to_markdown(resp.text)
    elif content_type.startswith("text/") or "json" in content_type or "xml" in content_type:
        body = resp.text
    else:
        return f"error: unsupported content-type: {content_type or '(none)'}"

    return truncate_output(body, max_chars=WEB_FETCH_MAX_CHARS)
```

`default_tools()` 追加：

```python
registry.register(
    Tool(
        name="web_fetch",
        description="Fetch a URL and return its content as markdown (or raw text).",
        run=web_fetch,
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
            },
            "required": ["url"],
        },
    )
)
```

### 4.2 改 `tools.py`：新增 `web_search`

`web_search` 不是在实现一个完整搜索引擎。教学版只做一件事：把 DuckDuckGo HTML 页面里每条搜索结果的标题和真实 URL 摘出来，整理成模型容易读的列表。

这里有两个小坑提前说清：

- DuckDuckGo 的结果链接经常不是目标 URL，而是 `/l/?uddg=ENCODED_URL&rut=...` 这种跳转链接，所以要用 `_unwrap_ddg_url()` 把 `uddg` 解出来。
- HTML 端点没有稳定 API schema，这里用正则只抓 `result__a` 这类结果链接。够教学跑通，但生产里应该换 Tavily / Serper / Brave 这类正式搜索 API。

在 `web_fetch` 之后新增：

```python
def _unwrap_ddg_url(href: str) -> str:
    # DuckDuckGo HTML 端点返回的 href 形如 /l/?uddg=ENCODED_URL&rut=...
    # 这里把真实目标 URL 提出来，让模型看到的就是最终落地址。
    if "/l/" not in href:
        return href
    if href.startswith("//"):
        parsed = urlparse(f"https:{href}")
    elif href.startswith("/"):
        parsed = urlparse(f"https://duckduckgo.com{href}")
    else:
        parsed = urlparse(href)
    params = parse_qs(parsed.query)
    if "uddg" in params:
        return unquote(params["uddg"][0])
    return href


def _duckduckgo_search(query: str, max_results: int) -> list[dict[str, str]]:
    # DuckDuckGo 没有官方 API。HTML 端点是教学版的兜底；
    # 想稳定就换成 Tavily/Serper/Brave 等带 API key 的搜索 provider。
    headers = {"User-Agent": WEB_USER_AGENT}
    with httpx.Client(timeout=WEB_SEARCH_TIMEOUT_S, follow_redirects=True) as client:
        resp = client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
        )
        resp.raise_for_status()

    pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for href, title_html in pattern.findall(resp.text):
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        url = _unwrap_ddg_url(href)
        if not title or not url:
            continue
        results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break
    return results


def web_search(args: dict[str, Any], ctx: ToolContext) -> str:
    query = args.get("query", "")
    if not query:
        return "error: missing required argument 'query'"
    max_results = max(1, min(int(args.get("max_results", 5)), 10))
    try:
        results = _duckduckgo_search(query, max_results=max_results)
    except httpx.HTTPError as exc:
        return f"error: {exc}"
    if not results:
        return "(no results)"
    lines = [f"- {r['title']}\n  {r['url']}" for r in results]
    return truncate_output("\n".join(lines))
```

`default_tools()` 追加最后一段：

```python
registry.register(
    Tool(
        name="web_search",
        description="Search the web (DuckDuckGo) and return top results.",
        run=web_search,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {
                    "type": "integer",
                    "description": "How many results to return (1-10).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )
)
```

### 4.3 跑两个验收

抓网页：

```bash
$ uv run agent-code "用 web_fetch 去 https://peps.python.org/pep-0008/ 看一下，告诉我里面最重要的 4 条规则"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: web_fetch {'url': 'https://peps.python.org/pep-0008/'}
observation: # PEP 8 – Style Guide for Python Code

  * Author: Guido van Rossum ...
...
final: 最重要的 4 条规则大致是：
1. 缩进每级 4 个空格，不要混用 tab 和 space。
2. 每行最长 79 个字符，docstring 和注释 72。
3. 顶层函数和类之间空两行；类内方法空一行。
4. 模块名小写带下划线；类名 CapWords；常量全大写带下划线。
```

搜一个东西：

```bash
$ uv run agent-code "用 web_search 找一下 'uv python package manager release notes'，给我前 3 条结果"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: web_search {'query': 'uv python package manager release notes', 'max_results': 3}
observation: - astral-sh/uv: An extremely fast Python package and project manager...
  https://github.com/astral-sh/uv
- Releases · astral-sh/uv · GitHub
  https://github.com/astral-sh/uv/releases
- Changelog | uv - Astral
  https://docs.astral.sh/uv/reference/changelog/
final: uv 的 release notes 最权威的入口在 GitHub releases 页 https://github.com/astral-sh/uv/releases，
对应文档站镜像在 https://docs.astral.sh/uv/reference/changelog/。
```

搜索结果会随时间和地区变化，DuckDuckGo HTML 端点偶尔会反爬。能稳定看到 `tool_call: web_search` 一行，并且 `observation` 是 `- 标题\n  URL` 的格式，就说明工具链通了。

## 手动 trace 一遍

输入 `这个项目的入口文件在哪里？` 后，从 CLI 到模型再到 Agent Loop 大致发生了什么。模型具体先调 `list_files`、`project_tree` 还是直接 `read_file pyproject.toml` 不固定，下面按最常见的 `list_files -> read_file` 路径 trace 一遍：

```txt
1. CLI 解析 cwd=/your/project、provider=anthropic、model=deepseek-v4-flash。
2. run_agent 构造 ToolContext(cwd, SkipPolicy.default(gitignore=load_gitignore(cwd)), ReadFileState())。
3. 第一次请求模型，provider 把 9 个工具描述传给 Anthropic Messages API。
4. tool_use: list_files {"path": "."}。
5. fs_safety 把每个 entry 过 should_skip，.venv/、__pycache__/、.git/、.gitignore 命中的都被剔掉。
6. Agent Loop 把这一轮所有 tool_result block 收进同一条 user message，再发起下一次模型请求。
7. 模型看到顶层目录里有 pyproject.toml，决定读它。
8. tool_use: read_file {"path": "pyproject.toml"}。
9. fs_safety 把路径 resolve 到 cwd 内，ensure_text_file 通过，ensure_within_size 通过，read_text 后过 truncate_output。
10. read_file 顺手把 (mtime_ns, char_count) 写入 ctx.read_state，给 Day 4 留底。
11. Agent Loop 再次把 read_file 的 tool_result 打包成下一条 user message。
12. 模型从 pyproject.toml 看到 [project.scripts] agent-code = "agent_code.cli:main"。
13. 模型返回 text "入口在 agent_code/cli.py 的 main()"。
14. CLI 把 trace 打印出来：tool_call、observation、final。
```

如果同一个问题被换成 `去 PEP 8 看一下最重要的 4 条规则`，trace 通常会变成 `web_fetch` 一次 → final 一次。工具不一样，但 Agent Loop 形状一致：模型给 `tool_use`，harness 执行工具，把 `tool_result` 打包回下一条 user message，再让模型继续回答。

## 今天有了什么

- **`fs_safety` 边界**：cwd 圈定、二进制拒绝、单文件 256 KiB、输出截断、skip 规则集中在一个文件里，工具实现里不重复这些判断。
- **`read_file` + `ReadFileState`**：Agent 第一次能读到项目内容，且每次读都被 harness 记录下来，为 Day 4 的 read-before-edit 留好钩子。
- **`list_files` / `project_tree`**：Agent 第一次能"看见"项目结构，默认 skip 把 `.venv/`、`__pycache__/` 之类的噪声挡在上下文外。
- **`glob` / `grep`**：按文件名找、按内容找，都遵守 cwd 和 `.gitignore`；`grep` 优先 ripgrep，没装就纯 Python 兜底。
- **`web_fetch` / `web_search`**：Agent 第一次能去外部世界拿信息；URL 校验、超时、大小、HTML→markdown、结果截断都集中在工具内部。
- **`ToolContext`**：工具签名第一次有了"运行时上下文"这个抽象，今天装 `cwd / skip_policy / read_state`；后面要往里塞 permission 模式、session 信息时，不用再改每个工具函数头。

## 常见问题

### `ModuleNotFoundError: No module named 'pathspec'` 或 `'html2text'`

今天新加两个依赖。补一次就行：

```bash
uv add pathspec html2text
```

### `web_fetch` 报 `httpx.ConnectError`

通常是没走代理。Day 2 已经装过 `httpx[socks]`，`httpx` 会自动读取 `ALL_PROXY` / `HTTPS_PROXY`。在终端里 `export ALL_PROXY=socks5://127.0.0.1:1080`（按你本地代理改）后再跑一次。

### `web_search` 返回 `(no results)` 或一直 502

DuckDuckGo HTML 端点没有官方 API，长期来看不稳定。教学版用它做兜底，碰到反爬就稍等再试，或者切到真正的搜索 API（Tavily/Serper/Brave，见课后挑战）。

### `web_fetch` 总是被截断

当前 `WEB_FETCH_MAX_CHARS = 20_000`，对长文档是有意的限制 —— 模型上下文不便宜。需要更长就改这个常量，或者按段抓（课后挑战）。

### `.gitignore` 在父目录或子目录，没生效

Day 3 的 `load_gitignore(cwd)` 只读 cwd 根目录下的那一个 `.gitignore`。这是为了教学简单。嵌套 `.gitignore`（每个子目录自带一份）留作课后挑战。

如果你想让父目录的 `.gitignore` 生效，最直接的办法是 `agent-code --cwd /that/parent ...`。

### 读到二进制文件报错 `binary file: foo.bin`

这是 `ensure_text_file` 在拦截：白名单后缀直接放行，其他文件 peek 首 1 KB 看到 NUL 字节就拒绝。

如果你确定这个文件是文本（例如自定义无后缀的脚本），最快的办法是给它加 `.txt` 或对应的代码后缀。要更细粒度的支持，请看课后挑战的"hex 预览工具"。

### 读到大文件报错 `file too large: ... > 262144`

`ensure_within_size` 在拦截。这是有意的边界：单文件超过 256 KiB 整文件读会撑爆上下文。建议改用 `grep` 找具体行，或先让模型告诉 harness "我想看哪一段"再用 `read_file`（课后挑战的 `offset`/`limit` 版本就是干这个的）。

## 课后挑战

1. 给 `read_file` 加 `offset` 和 `limit` 参数，按行而不是按总字节数截断；当文件超过 `MAX_READ_BYTES` 时引导模型用 offset/limit。
2. 给 `grep` 加 `output_mode`（`content` / `files_with_matches` / `count`）和 `head_limit`，让大仓库下的输出更可控。
3. 支持嵌套 `.gitignore`：从命中文件的所在目录向上走，合并每一层的 gitignore matcher。
4. 加一个 `hex_preview` 工具，专门给 `ensure_text_file` 拦下的二进制文件做前 N 字节的十六进制预览。
5. 给 `web_fetch` 加一个 15 分钟的内存 LRU 缓存，命中时直接返回旧 markdown，避免重复抓页面浪费 token。
6. 用 `readability-lxml` 或 `trafilatura` 替换 `html2text`，对比同一个 PEP 页面的正文抽取质量。
7. 把 `web_search` 抽成 provider 接口，新增一个用 `TAVILY_API_KEY` 或 `SERPER_API_KEY` 的真实搜索 provider，在 `ctx` 里按可用性选择。
8. 给 `web_fetch` 加 allow/deny 域名白名单（环境变量驱动），命中 deny 直接拒绝；这一步是 Day 5 权限系统的预演。

## 思考题

几个开放性问题，先自己憋一句话答案，再继续往下看。面试官真问起工具系统和 harness 边界这块，能不能讲清楚就看这关。

1. **为什么要把所有"文件系统边界"——cwd 锁定、二进制判断、单文件大小、输出截断、skip 规则——集中到 `fs_safety.py` 一个文件？** 让 `read_file`、`list_files`、`grep` 各自处理一份会出什么问题？

2. **`ToolContext` 在 harness 里担任什么角色？** 让每个工具自己 `Path.cwd()` 或读 `AGENT_CWD` 环境变量拿当前目录不行吗？

3. **`read_file` 顺手把 `(mtime_ns, char_count)` 写进 `ReadFileState`，但 Day 3 里没有任何代码读这份记录。这一步是过度设计吗？** 

4. **`web_fetch` 在真正 `httpx.get(...)` 之前做了哪些校验？** 这些检查为什么必须放在工具内部，让模型自己保证 URL 合法不行吗？

## 下一天

今天 Agent 第一次能"看"代码 + 能"看"外部世界：读文件、列目录、按名字找、按内容搜、抓网页、做搜索。

下一天我们让它能"改"代码 —— 但**不是让模型绕过 harness 直接写**。模型会调用 `file_edit(file_path, old_string, new_string)` 或 `file_write(file_path, content)`；真正写盘前，harness 会先检查它是否读过目标文件（用今天种下的 `ReadFileState`）、渲染 diff、让你按 y/n 确认。今天的 `read_file` 是下一天 read-before-edit 的依据，`fs_safety` 仍然是写盘前的最后一道边界。

## 注释说明

**注释1 · REPL 分支**：`agent-code "..."` 带 prompt → 跑一轮就退出；只敲 `agent-code` 不带 prompt → 进交互模式，在 `>` 后面反复输入。Day 3 只改前者里的 `cwd`，交互循环不用动。
