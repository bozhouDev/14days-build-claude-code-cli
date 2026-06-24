from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .bash_runner import run_sync as _bash_run_sync

import html2text
import httpx

from .fs_safety import (
    ReadFileState,
    SkipPolicy,
    ensure_text_file,
    ensure_within_size,
    resolve_in_cwd,
    should_skip,
    truncate_output,
    apply_single_replace,
)
from .model import ToolCall, ToolResult
from .file_history import backup
from .bash_runner import run_sync as _bash_run_sync
from .runtime import RuntimeState, TodoItem


@dataclass
class ToolContext:
    # 工具运行时上下文。Day 3 装 cwd、skip 规则、ReadFileState；后面天会塞更多。
    cwd: Path
    skip_policy: SkipPolicy = field(default_factory=SkipPolicy.default)
    read_state: ReadFileState = field(default_factory=ReadFileState)
    runtime_state: RuntimeState | None = None   # Day 8：工具读写共享运行态的入口


ToolFunc = Callable[[dict[str, Any], ToolContext], str]


@dataclass
class Tool:
    name: str
    description: str
    run: ToolFunc
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )
    is_read_only: bool = False   # Day 8 v4：只读工具可并行


def echo(args: dict[str, Any], ctx: ToolContext) -> str:
    return str(args.get("text", ""))


def system_date(args: dict[str, Any], ctx: ToolContext) -> str:
    # system_date 是模型看不到系统时钟时，需要向 harness 请求的能力。
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _render_todos(items: list[TodoItem]) -> str:
    icon = {"pending": "○", "in_progress": "◉", "completed": "✓"}
    return "\n".join(f"  {icon.get(t.status, '?')} {t.content}" for t in items) or "(no todos)"


def todo_write(args: dict[str, Any], ctx: ToolContext) -> str:
    """整表覆盖待办板。每次调用传来的 todos 就是新列表的全部。"""
    state = ctx.runtime_state
    if state is None:
        return "error: no runtime state"
    items = [
        TodoItem(
            content=t.get("content", ""),
            status=t.get("status", "pending"),
            active_form=t.get("activeForm", ""),
        )
        for t in args.get("todos", [])
    ]
    state.todo_store = items                  # 整表覆盖

    lines = [_render_todos(items), "", "Todos updated."]
    # verification nudge：本次关掉 3+ 个任务、且整张表没有任何验证项 → 提醒先验证
    completed = sum(1 for t in items if t.status == "completed")
    kws = ("test", "pytest", "verify", "lint", "check")
    has_verify = any(any(k in t.content.lower() for k in kws) for t in items)
    if completed >= 3 and not has_verify:
        lines.append("提示：关掉了 3+ 个任务但没有验证步骤，建议先加一个测试/验证项再收尾。")
    return "\n".join(lines)


def todo_read(args: dict[str, Any], ctx: ToolContext) -> str:
    state = ctx.runtime_state
    return _render_todos(state.todo_store) if state else "(no todos)"


def skill_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """给模型看的 skill 目录；和 /skills 共用同一份 loader。"""
    from .skills import SkillLoader

    loader = SkillLoader(ctx.cwd)
    return loader.render_list()


def skill_load(args: dict[str, Any], ctx: ToolContext) -> str:
    """按需加载 skill 正文。它只返回知识，不改变当前工具白名单。"""
    from .skills import SkillLoader

    name = str(args.get("name", "")).strip()
    if not name:
        return "error: missing required argument 'name'"
    skill = SkillLoader(ctx.cwd).load(name)
    if skill is None:
        return f"error: skill not found: {name}"
    return skill.body


def enter_plan_mode(args: dict[str, Any], ctx: ToolContext) -> str:
    """模型主动请求进 plan 模式。"""
    state = ctx.runtime_state
    if state is None:
        return "error: no runtime state"
    state.permission_mode = "plan"
    return (
        "Plan mode on. Write tools are denied. Draft a clear plan, then present it "
        "(or call exit_plan_mode(plan_summary)). The harness will ask the user to "
        "approve before writes unlock."
    )


def exit_plan_mode(args: dict[str, Any], ctx: ToolContext) -> str:
    """函数体很薄——渲染计划、等批准、翻模式都在 agent.py 的拦截块里做。"""
    return "Plan approved. Write tools are now enabled."


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


def _unwrap_ddg_url(href: str) -> str:
    # DuckDuckGo HTML 端点返回的 href 形如 /l/?uddg=ENCODED_URL&rut=...
    # 这里把真实目标 URL 提出来，让模型看到的就是最终落地址。
    if "/l/" not in href:
        return href
    parsed = urlparse(href if href.startswith("http") else f"https:{href}")
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
    if path.exists():
        # 备份旧内容（备份失败不阻塞写盘）
        try:
            old = path.read_text(encoding="utf-8")
            backup(ctx.cwd, path, old)
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    # 写盘后刷新 read_state，让下一次编辑基于最新内容
    ctx.read_state.record(path, content)
    return f"Wrote {len(content)} chars to {path_str}"


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


class ToolRegistry:
    def __init__(self) -> None:
        # 注册表是工具名和 Python 函数之间的 harness 边界。
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def filtered(self, allowed_names: list[str] | None) -> "ToolRegistry":
        """给模型看的工具面。None 表示不收敛，[] 表示不给任何工具。"""
        if allowed_names is None:
            return self
        registry = ToolRegistry()
        for name in allowed_names:
            tool = self.get(name)
            if tool is not None:
                registry.register(tool)
        return registry

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
    backup(ctx.cwd, path, content)  # 写盘前备份旧内容
    # 防 race：agent.py 已经做过一次 apply_single_replace 算 diff，
    # 如果 confirm 那一刻到现在 old_content 又被外部改过，这里会再兜一次。
    new_content, err = apply_single_replace(content, old_string, new_string, replace_all)
    if err:
        return err

    path.write_text(new_content, encoding="utf-8")
    ctx.read_state.record(path, new_content)
    return f"Edited {path_str}: replaced {len(old_string)} chars with {len(new_string)} chars"
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

    return _bash_run_sync(command, ctx.cwd, timeout=timeout)

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

def _memory_write(args: dict[str, Any], ctx: ToolContext) -> str:
    """写入一条长期记忆——工具函数只做薄包装。"""
    from .memdir.store import write_memory

    mem_type = args.get("type", "")
    title = args.get("title", "")
    body = args.get("body", "")
    if mem_type not in ("user", "feedback", "project", "reference"):
        return "error: type must be one of: user, feedback, project, reference"
    if not title:
        return "error: missing required argument 'title'"
    if not body:
        return "error: missing required argument 'body'"
    try:
        entry = write_memory(ctx.cwd, mem_type, title, body)
        return f"Memory saved: [{entry.mem_type}] {entry.title} -> {entry.file_path}"
    except Exception as exc:
        return f"error: {exc}"


def _memory_recall(args: dict[str, Any], ctx: ToolContext) -> str:
    """关键字搜索长期记忆——工具函数只做薄包装。"""
    from .memdir.store import recall_memory

    query = args.get("query", "")
    top_k = int(args.get("top_k", 5))
    if not query:
        return "error: missing required argument 'query'"
    try:
        entries = recall_memory(ctx.cwd, query, top_k=top_k)
        if not entries:
            return "(no matching memories found)"
        lines = []
        for e in entries:
            snippet = e.body[:200] + ("..." if len(e.body) > 200 else "")
            lines.append(f"## [{e.mem_type}] {e.title}")
            lines.append(f"  file: {e.file_path}")
            lines.append(f"  {snippet}")
            lines.append("")
        return "\n".join(lines)
    except Exception as exc:
        return f"error: {exc}"

def default_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo",
            description="Return the input text.",
            run=echo,
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to return."}},
                "required": ["text"],
            },
            is_read_only=True,
        )
    )
    registry.register(
        Tool(name="system_date", description="Return the current system date and time.", run=system_date, is_read_only=True)
    )
    registry.register(
        Tool(
            name="todo_write",
            description=(
                "Create and manage a structured task list. Use for multi-step tasks (3+ steps). "
                "Keep exactly ONE item in_progress. Mark completed immediately when done. "
                "The todos array is a FULL replacement—always send the entire list."
            ),
            run=todo_write,
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "Imperative task name."},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                "activeForm": {"type": "string", "description": "Present-continuous form."},
                            },
                            "required": ["content", "status", "activeForm"],
                        },
                    },
                },
                "required": ["todos"],
            },
            is_read_only=False,
        )
    )
    registry.register(
        Tool(
            name="todo_read",
            description="Read the current todo list.",
            run=todo_read,
            parameters={"type": "object", "properties": {}, "required": []},
            is_read_only=True,
        )
    )
    registry.register(
        Tool(
            name="skill_list",
            description="List available local skills with their descriptions.",
            run=skill_list,
            parameters={"type": "object", "properties": {}, "required": []},
            is_read_only=True,
        )
    )
    registry.register(
        Tool(
            name="skill_load",
            description="Load the full body of a local skill by name.",
            run=skill_load,
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name, e.g. debug-test."},
                },
                "required": ["name"],
            },
            is_read_only=True,
        )
    )
    registry.register(
        Tool(
            name="enter_plan_mode",
            description=(
                "Enter plan mode: draft a plan before writing. Write tools are denied until approval. "
                "Present the plan or call exit_plan_mode(plan_summary); the harness asks the user to approve."
            ),
            run=enter_plan_mode,
            parameters={"type": "object", "properties": {}, "required": []},
        )
    )
    registry.register(
        Tool(
            name="exit_plan_mode",
            description=(
                "Submit your plan for user approval. Use this when the plan is ready. "
                "Write tools unlock only after the user approves."
            ),
            run=exit_plan_mode,
            parameters={
                "type": "object",
                "properties": {"plan_summary": {"type": "string", "description": "The plan to review."}},
                "required": ["plan_summary"],
            },
        )
    )
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
            is_read_only=True,
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
            is_read_only=True,
        )
    )
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
            is_read_only=True,
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
            is_read_only=True,
        )
    )
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
            is_read_only=True,
        )
    )
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
    registry.register(
        Tool(
            name="git_status",
            description="Run git status to see the current state of the working directory.",
            run=_git_status,
            parameters={"type": "object", "properties": {}, "required": []},
            is_read_only=True,
        )
    )
    registry.register(
        Tool(
            name="git_diff",
            description="Run git diff to see unstaged changes in the working directory.",
            run=_git_diff,
            parameters={"type": "object", "properties": {}, "required": []},
            is_read_only=True,
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
    registry.register(
        Tool(
            name="memory_write",
            description=(
                "Write a fact to long-term memory. Memories persist across sessions. "
                "Use for: user preferences, project conventions, feedback received, "
                "technical references to external systems."
            ),
            run=_memory_write,
            parameters={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["user", "feedback", "project", "reference"],
                        "description": "Memory category.",
                    },
                    "title": {"type": "string", "description": "Short title for this memory."},
                    "body": {"type": "string", "description": "Full markdown content of the memory."},
                },
                "required": ["type", "title", "body"],
            },
        )
    )
    registry.register(
        Tool(
            name="memory_recall",
            description=(
                "Search long-term memory by keywords. Returns matching entries with snippets. "
                "Use when you need to recall facts about the user, project, or past decisions."
            ),
            run=_memory_recall,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search for."},
                    "top_k": {
                        "type": "integer",
                        "description": "Max results to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            is_read_only=True,
        )
    )
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
            is_read_only=True,
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
    return registry
