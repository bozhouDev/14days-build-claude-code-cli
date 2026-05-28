# Day 6：Session + Memory（三层记忆）

Day 5 让 Agent 能跑命令、有权限控制了。但每次 `agent-code` 退出后，对话历史就丢了——下次启动时模型完全不记得你上一轮说过什么、改过哪些文件。

今天给 Agent 加三种"记得住上下文"的能力：

- **会话持久化**：每次对话自动落盘，下次可以"接着上次说"。
- **项目规则**：你写的 `AGENT.md` 自动进入模型的 system prompt。
- **跨 session 长期记忆**：模型把关于你和项目的事实存到本地文件夹，新会话里也能调回来。

跑完之后你会看到：

- `--resume <id>` / `-c` 恢复历史会话，模型记得上一轮说过什么
- cwd 下的 `AGENT.md` 内容自动注入系统提示，模型知道你的项目规则
- 长会话自动触发压缩——把早期消息压成确定性摘要，不爆上下文
- 两个新工具让模型把长期事实写进本地，并能在新会话里搜回来
- `.agent/sessions/` 下存 JSONL 会话日志，`.agent/memory/` 下存长期记忆文件

代码约 400 行新增，改动 4 个老文件，新增 7 个文件。


Day 6 是前 7 天里"上下文工程"最集中的一天——前面 5 天让 Agent 能跑工具，今天让它能"记得住"。

这里用的项目规则文件名是 `AGENT.md`（单数），只服务我们正在手写的教学 CLI。仓库根目录里如果已经有给 Codex / 其他 Agent 用的 `AGENTS.md` 或 `CLAUDE.md`，今天的教学实现不会自动读取它们。想复用已有规则，可以复制一份到 `AGENT.md`，或者把下面的 `project_memory.py` 改成你喜欢的文件名。

## 起手：今天的起点

从 Day 5 的 `agent-code` 项目继续改。不需要新依赖——`uuid`、`json`、`datetime` 都是标准库。

新增：

```txt
agent_code/session.py           session 创建、加载、JSONL 落盘
agent_code/project_memory.py    读取 cwd 下的 AGENT.md
agent_code/compact_basic.py     确定性压缩（不调 LLM）
agent_code/memdir/__init__.py   memdir 子包 re-export
agent_code/memdir/paths.py      .agent/memory/ 目录布局 + 截断常量
agent_code/memdir/types.py      MemoryEntry 数据类 + slug 生成
agent_code/memdir/store.py      写入、召回、索引加载
```

改动：

```txt
agent_code/model.py        ModelProvider.complete() 加 system 参数；AnthropicProvider 转发到 SDK
agent_code/agent.py        run_agent() 加 session / system_prompt 参数；公开 build_system_prompt；每轮落盘；触发 compact
agent_code/cli.py          加 --resume / --continue；session 创建/加载；cold start 拼一次 system_prompt
agent_code/permissions.py  memory_recall 进 _READONLY_TOOLS；memory_write 单独进 _LOW_RISK_WRITES，plan 仍 deny
```

今天分四步。v1 让会话能保存和恢复，v2 让 AGENT.md 自动注入，v3 让长会话能压缩，v4 加上跨 session 长期记忆。

## v1：Session JSONL + --resume/--continue

先把最痛的问题解决：每次退出后对话历史就没了。让 harness 在每次对话时自动把消息落到磁盘上，下次启动时可以恢复。

v1 要做的事：新建 `session.py` 管理会话生命周期，改 `agent.py` 让消息初始化和落盘走 session，改 `cli.py` 加 `--resume` 和 `-c` 选项。先专注让会话能落盘+恢复，`model.py` 的 system 参数等到 v2 跟 AGENT.md 一起改。

### 1.1 新建 `agent_code/session.py`

`Session` 类管三件事：创建新会话（生成 session id + 创建 JSONL 文件）、加载已有会话（按 id 或按最近）、读/写消息历史。

JSONL 格式选它是因为 append-only——每轮对话末尾追加一行 JSON，不需要维护文件头尾结构。每行 JSON 包含 `role`、`content`、`timestamp`——`role` 和 `content` 直接对应 Anthropic Messages API 的消息字段，恢复时不需要做协议转换。

```python
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sanitize_cwd(cwd: Path) -> str:
    """把绝对路径转成合法目录名：/ 替换为 _。"""
    path_str = str(cwd.resolve())
    # Windows 盘符的 : 和反斜杠也换掉
    sanitized = path_str.replace("/", "_").replace(":", "_").replace("\\", "_")
    # 去掉前导下划线
    return sanitized.lstrip("_")


def _sessions_dir(cwd: Path) -> Path:
    """返回 .agent/sessions/<sanitized_cwd>/，自动创建。"""
    dir_path = cwd / ".agent" / "sessions" / _sanitize_cwd(cwd)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


class Session:
    """一次会话。管理 session id、JSONL 落盘、读取历史消息。"""

    def __init__(self, cwd: Path, session_id: str, file_path: Path, resumed: bool = False) -> None:
        self.cwd = cwd
        self.session_id = session_id
        self.file_path = file_path
        # resumed=True 表示这是 --resume / -c 拉起来的，CLI 用它决定打印 "(resumed)"
        self.resumed = resumed

    @classmethod
    def create(cls, cwd: Path) -> "Session":
        """新建会话：生成 12 位 hex session_id，创建空 JSONL 文件。"""
        sid = uuid.uuid4().hex[:12]
        file_path = _sessions_dir(cwd) / f"{sid}.jsonl"
        file_path.touch()  # 创建空文件，标记 session 存在
        return cls(cwd=cwd, session_id=sid, file_path=file_path, resumed=False)

    @classmethod
    def load_latest(cls, cwd: Path) -> "Session | None":
        """加载最近一次会话（按 mtime）。没有则返回 None。"""
        sessions_dir = _sessions_dir(cwd)
        jsonl_files = list(sessions_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None
        latest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
        sid = latest.stem  # 文件名去 .jsonl 就是 session_id
        return cls(cwd=cwd, session_id=sid, file_path=latest, resumed=True)

    @classmethod
    def load_id(cls, cwd: Path, session_id: str) -> "Session | None":
        """按 session_id 加载指定会话。找不到则返回 None。"""
        file_path = _sessions_dir(cwd) / f"{session_id}.jsonl"
        if not file_path.exists():
            return None
        return cls(cwd=cwd, session_id=session_id, file_path=file_path, resumed=True)

    @property
    def history(self) -> list[dict[str, Any]]:
        """解析 JSONL 文件，返回 messages 列表（去掉 timestamp）。"""
        messages: list[dict[str, Any]] = []
        if not self.file_path.exists():
            return messages
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue  # 跳过损坏行，不阻塞整个 session 恢复
            # 只保留 role 和 content——模型不需要 timestamp
            messages.append({"role": data["role"], "content": data["content"]})
        return messages

    def append_messages(self, msgs: list[dict[str, Any]]) -> None:
        """向 JSONL 文件追加消息。每条自动加 UTC timestamp。"""
        now = datetime.now(timezone.utc).isoformat()
        with open(self.file_path, "a", encoding="utf-8") as f:
            for msg in msgs:
                record = {"role": msg["role"], "content": msg["content"], "timestamp": now}
                # separators 强制紧凑输出（默认带空格），让一行一条 JSON 看起来更稳
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
```

`create()` / `load_latest()` / `load_id()` 三个工厂方法是 session 生命周期入口——cli.py 会根据 CLI flag 调用它们。`history` 是只读属性，`append_messages` 只追加。这套接口把"文件怎么组织"隔离在 `Session` 内部，`agent.py` 只用 `history` 和 `append_messages` 两个方法。

在接入 `agent.py` / `cli.py` 之前，先用一行 Python 验证 `Session` 自身能跑：

```bash
$ uv run python -c "
from pathlib import Path
from agent_code.session import Session

s = Session.create(Path.cwd())
print('created:', s.session_id, 'resumed:', s.resumed)
s.append_messages([
    {'role': 'user', 'content': 'hello'},
    {'role': 'assistant', 'content': 'hi back'},
])
print('history:', s.history)

s2 = Session.load_latest(Path.cwd())
print('latest:', s2.session_id, 'resumed:', s2.resumed)
"
```

预期输出：

```
created: a1b2c3d4e5f6 resumed: False
history: [{'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi back'}]
latest: a1b2c3d4e5f6 resumed: True
```

`session_id` 因为是随机 UUID，你的值会不同，但前后两次 `s` / `s2` 一定相同——因为 `load_latest` 拿到的就是 `s` 刚创建的那份 jsonl。`resumed` 字段第一次是 `False`、第二次 `True`，CLI 后面用它决定是否打 `(resumed)` 后缀。

注意这个 session 只保存模型可见的 `messages`，不保存 harness 的运行时对象。比如 Day 4 的 `ReadFileState`、Day 5 的后台进程、当前权限确认选择，都不会写进 JSONL。恢复会话后，如果模型想继续编辑上次读过的文件，仍然应该先重新 `read_file` 一次，让 read-before-edit 和 mtime 检查重新建立当前进程里的安全状态。

验证过 Session 自己能跑，再去接 Agent Loop。

### 1.2 改 `agent_code/agent.py`：接受 session，从历史恢复消息，每轮落盘

v1 改三处：顶部 import、`run_agent` 签名、消息初始化和落盘。

**第一处**，顶部 import 追加 `Session`：

```python
from .session import Session  # Day 6：会话持久化
```

放在 `from .prompt_ui import ...` 之后。

**第二处**，`run_agent()` 签名加一个参数。找到这一行（约第 64 行）：

```python
def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    permission_mode: str = "default",
) -> AgentResult:
```

改成：

```python
def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    permission_mode: str = "default",
    session: Session | None = None,  # Day 6：传 None 退化为 Day 5 行为
) -> AgentResult:
```

**第三处**，消息初始化。找到这一行（约第 81 行）：

```python
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
```

替换成：

```python
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
```

**第四处**，每次模型返回 final 时落盘。找到三行（约第 101-103 行）：

```python
        if not response.tool_calls:
            final = response.text or ""
            emit(f"final: {final}")
            return AgentResult(final=final, trace=trace, messages=messages)
```

在 `return` 之前加 session 落盘：

```python
            # Day 6：把最终 assistant 消息落盘
            if session:
                session.append_messages([messages[-1]])
```

**第五处**，每轮工具结果落盘。找到这一行（约第 289 行，在 Agent Loop 的最后一个 `messages.append` 之后）：

```python
        messages.append({"role": "user", "content": tool_result_blocks})
```

在它之后加：

```python
        # Day 6：每轮结束后把 assistant + tool_result 两条消息落盘
        if session:
            session.append_messages(messages[-2:])
```

落盘时机选在每轮末尾（而不是实时逐条写）：一次 `complete()` 调用 + 一次工具执行 = 一轮。一轮产生两条新消息——assistant 和 tool_result——一起落盘。如果中途崩溃，最多丢一回合。

其他 agent.py 代码（`_assistant_message`、`_tool_result_message`、权限引擎拦截块）这一版都不动。

### 1.3 改 `agent_code/cli.py`：--resume 和 --continue

**第一处**，顶部 import 追加 `Session`：

```python
from .session import Session  # Day 6：会话持久化
```

**第二处**，`run_once()` 签名加 `session` 参数。找到 `run_once` 定义（约第 33 行），把签名改成：

```python
def run_once(
    prompt: str,
    cwd: Path,
    provider_name: str,
    model: str,
    base_url: str | None,
    max_steps: int,
    permission_mode: str,
    session: Session | None = None,  # Day 6：可选的 session
) -> None:
```

然后在 `render_header` 之后、`provider = create_provider(...)` 之前，加一行打印 session id。带 `(resumed)` 后缀表明这是从磁盘拉起来的，不带就是新建的：

```python
    if session:
        suffix = " (resumed)" if session.resumed else ""
        console.print(f"[dim]session: {session.session_id}{suffix}[/dim]")
```

最后在 `run_agent()` 调用里传 `session`：

```python
    run_agent(
        prompt, provider, default_tools(),
        max_steps=max_steps, cwd=cwd,
        permission_mode=permission_mode, session=session,
    )
```

**第三处**，`main_command()` 加两个 CLI option。找到 `main_command` 的参数列表（约第 47-56 行），在 `permission_mode` 之后追加：

```python
    # Day 6：会话持久化入口
    resume: str | None = typer.Option(None, "--resume", help="按 session id 恢复指定会话"),
    continue_: bool = typer.Option(False, "--continue", "-c", help="恢复 cwd 最近一次会话"),
```

**第四处**，`main_command` 的主体逻辑。找到 `resolved_cwd = cwd.resolve()` 之后、`text = prompt.strip()` 之前，插入 session 创建/加载分支：

```python
    # Day 6：按 flag 分支决定 session 来源；具体打印交给 run_once，
    # 避免新建路径打一次、恢复路径打两次
    session: Session | None = None
    if continue_:
        session = Session.load_latest(resolved_cwd)
        if session is None:
            console.print("[red]没有找到历史会话，无法 --continue。[/red]")
            raise typer.Exit(code=1)
    elif resume:
        session = Session.load_id(resolved_cwd, resume)
        if session is None:
            console.print(f"[red]找不到 session: {resume}[/red]")
            raise typer.Exit(code=1)
```

**第五处**，一次性模式（`if text:` 分支）里，如果没通过 flag 指定 session，新建一个。找到：

```python
    if text:
        run_once(text, resolved_cwd, provider, model, base_url, max_steps, permission_mode)
        return
```

改成：

```python
    if text:
        if session is None:
            session = Session.create(resolved_cwd)
        run_once(text, resolved_cwd, provider, model, base_url, max_steps, permission_mode, session=session)
        return
```

**第六处**，REPL 分支。找到 REPL 注释后的 `render_header(resolved_cwd, ...)` 行（约第 65 行），在它之后立刻创建 session：

```python
    # Day 6：REPL 整个周期共享一个 session（run_once 每轮自己打印 session 头）
    if session is None:
        session = Session.create(resolved_cwd)
```

再把 REPL 循环里的 `run_once` 调用（约第 76 行）：

```python
        run_once(line, resolved_cwd, provider, model, base_url, max_steps, permission_mode)
```

替换成：

```python
        run_once(line, resolved_cwd, provider, model, base_url, max_steps, permission_mode, session=session)
```

REPL 一个进程内多轮对话共享同一个 `Session` 对象，每条 prompt 都会接着写同一个 jsonl 文件。

### 1.4 跑四个验证

**(a) 新 session 落盘：**

```bash
$ uv run agent-code "用 echo 工具说 hello"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash
session: a1b2c3d4e5f6

tool_call: echo {'text': 'hello'}
observation: hello
final: 已经用 echo 工具说出了 "hello"。
```

结束后检查 JSONL 文件是否生成：

```bash
$ ls .agent/sessions/*/
a1b2c3d4e5f6.jsonl

$ wc -l .agent/sessions/*/a1b2c3d4e5f6.jsonl
4 .agent/sessions/.../a1b2c3d4e5f6.jsonl
```

4 行 = user prompt + assistant(tool_use) + user(tool_result) + assistant(final)。用 `head` 看第一行格式：

```bash
$ head -1 .agent/sessions/*/a1b2c3d4e5f6.jsonl
{"role":"user","content":"用 echo 工具说 hello","timestamp":"2026-05-27T..."}
```

**(b) `-c` 恢复最近会话：**

```bash
$ uv run agent-code -c "我们上次说了什么"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash
session: a1b2c3d4e5f6 (resumed)

final: 我们上次用 echo 工具说了 "hello"。
```

模型看到了之前的历史消息——`-c` 把最近一次 session 的 JSONL 全量加载到 messages 数组开头，模型能引用之前的对话内容。

**(c) `--resume <id>` 恢复指定会话：**

```bash
$ uv run agent-code --resume a1b2c3d4e5f6 "继续"
session: a1b2c3d4e5f6 (resumed)
```

按 id 恢复的效果和 `-c` 一样，区别只是你指定了具体的 session id。

**(d) 不带 flag = 新 session，不记得之前内容。这里用一个不会诱导模型探索代码库的 prompt，能更稳定看到"模型确实没有历史"的反应：

```bash
$ uv run agent-code "echo 一下 'fresh start'"
session: f7e8d9c0a1b2

tool_call: echo {'text': 'fresh start'}
observation: fresh start
final: 已用 echo 输出 'fresh start'。
```

确定性判断点：

- `session:` 行**没有** `(resumed)` 后缀，说明这是新建的；
- `ls .agent/sessions/<sanitized_cwd>/` 现在能看到至少两个不同的 `.jsonl` 文件——这一次的 session 和 (a) 那次是独立两份。

如果你换一个更开放的 prompt（比如 "我们上次说了什么"），模型可能反而会主动调 `project_tree` / `bash` 去查上下文——那是模型策略问题，不影响"新 session = 没有历史"这件事，session id 不带 `(resumed)` 就是确定信号。

v1 的会话能保存和恢复了。但模型仍然不知道你的项目规则——每次启动都需要你在 prompt 里重复"这个项目用 pytest、Python 3.10+、type hints 必须写"。下一版让 harness 自动读 `AGENT.md`，注入到 system prompt。

## v2：AGENT.md 项目记忆

v1 让 Agent 能续上上次的对话，但不会记住项目规则。开发者一般会在项目根目录放一份规则文件——约定用什么测试框架、Python 版本要求、代码规范——模型应该在启动时就"知道"这些东西。

v2 做四件事：

- 新建 `project_memory.py` 读 cwd 下的 `AGENT.md`；
- 改 `model.py` 给 `complete()` 加 `system` 参数，让 system prompt 能透传到 Anthropic API；
- 改 `agent.py`，让 `run_agent` 接受 `system_prompt` 参数并转发给 `provider.complete`；
- 改 `cli.py`，在 cold start 时调一次 `build_system_prompt(cwd)`、把结果传进 `run_agent`——这样每条 prompt 不会重新读磁盘，REPL 多轮共享同一份规则。

### 2.1 新建 `agent_code/project_memory.py`

这个模块只做一件事：读文件、包装成 XML 块。用 XML 标签把项目规则和核心 system prompt 区分开——模型看到 `<project-rules>` 就知道这是"要遵守的规则"，不是"通用行为指南"。

```python
from __future__ import annotations

from pathlib import Path

# AGENT.md 大小上限：超过就截断保护，避免一份超长规则文件挤掉别的上下文
_MAX_AGENT_MD_BYTES = 50 * 1024


def load_agent_md(cwd: Path) -> str | None:
    """读取 cwd 下的 AGENT.md，包装成 <project-rules> 块。
    文件不存在返回 None——不是错误，只是没配置。"""
    agent_md = cwd / "AGENT.md"
    if not agent_md.exists():
        return None
    content = agent_md.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return None
    # 超过 50KB 就截到字节边界 + 一行提示，避免规则文件意外巨大爆 system prompt
    if len(content.encode("utf-8")) > _MAX_AGENT_MD_BYTES:
        truncated = content.encode("utf-8")[:_MAX_AGENT_MD_BYTES].decode("utf-8", errors="replace")
        content = truncated + "\n\n[... AGENT.md truncated at 50 KB ...]"
    # 用 XML 标签和核心 system prompt 隔开，让模型识别这是项目规则
    return f"<project-rules>\n{content}\n</project-rules>"
```

这里先只读 cwd 一层 `AGENT.md`，不向父目录 walk、不支持多文件合并。读者在项目根目录 `cat AGENT.md` 就能看到模型看到的规则，调试很直观。注意它不会读取 `AGENTS.md` 或 `CLAUDE.md`；如果你的项目已经有这些文件，要么复制成 `AGENT.md`，要么把 `agent_md = cwd / "AGENT.md"` 改成自己的约定。

### 2.2 改 `agent_code/model.py`：加 system 参数

Anthropic Messages API 把 system prompt 放在请求顶层的 `system` 字段，和 `messages` 数组是平级的两路输入。`ModelProvider` 接口要把这个边界暴露出来。

**第一处**，给 `ModelProvider` 协议加 `system` 参数。找到 `class ModelProvider(Protocol):` 里的 `complete()` 方法签名：

```python
class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
    ) -> ModelResponse:
        ...
```

改成：

```python
class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        system: str | None = None,  # Day 6：system prompt 注入入口
    ) -> ModelResponse:
        ...
```

**第二处**，给 `AnthropicProvider.complete()` 签名加 `system` 参数。找到这个方法（约第 77 行）：

```python
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
    ) -> ModelResponse:
```

改成：

```python
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        system: str | None = None,  # Day 6：通过 Anthropic 的 system= 参数注入
    ) -> ModelResponse:
```

然后在 `kwargs` 构造完之后（`"messages": messages,` 那行之后）、`if tools:` 之前，加入 system 转发：

```python
        # system prompt 独立于 messages——Anthropic API 把它放在请求顶层
        if system:
            kwargs["system"] = system
```

**第三处**，给 `MockProvider.complete()` 签名加 `system` 参数。Mock 不读 system，但签名要对齐协议：

```python
class MockProvider:
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        system: str | None = None,  # Mock 不读 system，但签名要对齐协议
    ) -> ModelResponse:
```

model.py 其他代码（`_content_block_to_dict`、`_to_anthropic_tools`、`_parse_tool_input`、`create_provider`）这一版都不动。

### 2.3 改 `agent_code/agent.py`：接受 system_prompt 参数

v2 让 `run_agent()` 接受外部传进来的 `system_prompt`，转发给 `provider.complete()`。`build_system_prompt(cwd)` 也写在 `agent.py` 里、对外公开，方便 cli.py 在 cold start 时调一次。

**第一处**，顶部 import 追加：

```python
from .project_memory import load_agent_md  # Day 6：AGENT.md 项目记忆
```

**第二处**，在 `AgentResult` dataclass 后面、`_assistant_message()` 前面，加核心 system prompt 常量和公开的组装函数：

```python
# Day 6：核心 system prompt——Agent 的行为指南
_SYSTEM_CORE = (
    "You are an AI coding agent running inside a CLI harness. "
    "You have access to tools for reading/writing files, running shell commands, "
    "searching the web, and asking the user questions. "
    "Use tools when needed; respond directly when you can."
)


def build_system_prompt(cwd: Path) -> str:
    """组装 system prompt：核心指南 + AGENT.md 项目规则。
    cli.py 在 cold start 时调一次，结果传给 run_agent——避免每条 prompt 重读 AGENT.md。"""
    parts: list[str] = [_SYSTEM_CORE]
    agent_md = load_agent_md(cwd)
    if agent_md:
        parts.append(agent_md)
    return "\n\n".join(parts)
```

**第三处**，`run_agent()` 签名加 `system_prompt` 参数：

```python
def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
    cwd: Path | None = None,
    permission_mode: str = "default",
    session: Session | None = None,
    system_prompt: str | None = None,  # Day 6 v2：cli.py 冷启动时拼好传进来
) -> AgentResult:
```

**第四处**，把 `provider.complete()` 调用（约第 84 行）加上 `system=system_prompt`：

```python
        response = provider.complete(
            messages, tools=tools.list(), system=system_prompt
        )
```

### 2.4 改 `agent_code/cli.py`：冷启动时拼一次 system prompt

**第一处**，顶部 import 追加：

```python
from .agent import build_system_prompt  # Day 6 v2：cold start 拼项目规则
```

**第二处**，`run_once()` 签名加 `system_prompt` 参数。找到 `run_once` 定义里 `session: Session | None = None` 那一行之后追加：

```python
    system_prompt: str | None = None,  # Day 6 v2：由 main_command 拼好传入
```

然后把 `run_agent()` 调用里加上 `system_prompt=system_prompt`：

```python
    run_agent(
        prompt, provider, default_tools(),
        max_steps=max_steps, cwd=cwd,
        permission_mode=permission_mode,
        session=session,
        system_prompt=system_prompt,
    )
```

**第三处**，在 `main_command()` 里 session 分支之后、`if text:` 之前，调一次 `build_system_prompt`：

```python
    # Day 6 v2：cold start 时把 AGENT.md 读一次，整轮 CLI 共享同一份 system prompt
    system_prompt = build_system_prompt(resolved_cwd)
```

**第四处**，一次性模式和 REPL 的 `run_once` 调用都把 `system_prompt=system_prompt` 透传过去。一次性模式：

```python
        run_once(text, resolved_cwd, provider, model, base_url, max_steps,
                 permission_mode, session=session, system_prompt=system_prompt)
```

REPL 循环里的 `run_once`：

```python
        run_once(line, resolved_cwd, provider, model, base_url, max_steps,
                 permission_mode, session=session, system_prompt=system_prompt)
```

整个进程生命周期只读一次 `AGENT.md`，REPL 多轮对话用同一份缓存的 system prompt。

### 2.5 跑三个验证

下面三个验证会读写 cwd 下的 `AGENT.md`。**如果你已经有真实的项目规则文件**，先用一个带 PID 的临时备份名挪走它，避免覆盖已有的 `.bak` / `.real` 文件：

```bash
$ BAK="AGENT.md.before-day6-$$"
$ [ -f AGENT.md ] && mv AGENT.md "$BAK"   # $$ 是当前 shell 的 PID，文件名唯一
```

验证全部跑完之后再恢复：`[ -f "$BAK" ] && mv "$BAK" AGENT.md`。整个 shell 会话内 `$BAK` 都是同一个值，跨进程不冲突。

**(a) 确定性检查 `build_system_prompt` 自身的输出。**先建一份临时 AGENT.md，再用一行 Python 看 `build_system_prompt` 是否把它包进了 `<project-rules>` 块：

```bash
$ cat > AGENT.md << 'EOF'
# 项目规则（demo）
- 所有代码必须用 Python 3.10+，写完整 type hints。
- 测试框架用 pytest，测试文件放在 tests/ 目录下。
- 不要用 print 打日志，用 logging 模块。
EOF

$ uv run python -c "
from pathlib import Path
from agent_code.agent import build_system_prompt
print(build_system_prompt(Path.cwd()))
"
```

预期输出（节选）：

```
You are an AI coding agent running inside a CLI harness. ...

<project-rules>
# 项目规则（demo）
- 所有代码必须用 Python 3.10+，写完整 type hints。
- 测试框架用 pytest，测试文件放在 tests/ 目录下。
- 不要用 print 打日志，用 logging 模块。
</project-rules>
```

这个检查不依赖模型行为——只要 `build_system_prompt` 实现正确，输出就稳定。AGENT.md 不存在时 `<project-rules>` 块整段消失，只剩 core prompt。

**(b) 没有 AGENT.md——和 Day 5 行为一样。真实规则已经在开头挪到 `$BAK`，cwd 下现在只有 (a) 写的 demo，直接删掉：

```bash
$ rm AGENT.md
$ uv run agent-code "echo 一下 'no rules here'"
...
tool_call: echo {'text': 'no rules here'}
observation: no rules here
final: 已用 echo 输出 'no rules here'。
```

确定性判断点是"AGENT.md 不存在时，`build_system_prompt` 不会再输出 `<project-rules>` 块"——这在 (a) 已经间接验过了。这里换成 `echo` 这种不诱发探索的 prompt 是为了避免模型在没有规则文件时主动调 `project_tree` / `bash` 找配置（一次性模式下 bash 还会卡在 confirm 上）。如果你想看模型对"我有什么规则"的自然反应，去 REPL 里跑同样的问句更舒服。

**(c) 创建 AGENT.md 后模型能引用。重新写一份 demo 规则：

```bash
$ cat > AGENT.md << 'EOF'
# 项目规则（demo）
- 所有代码必须用 Python 3.10+，写完整 type hints。
- 测试框架用 pytest，测试文件放在 tests/ 目录下。
- 不要用 print 打日志，用 logging 模块。
EOF

$ uv run agent-code "这个项目有什么规则"
...
final: 根据项目规则文件，这个项目有以下约定：
1. 必须使用 Python 3.10+，所有函数写完整 type hints
2. 测试用 pytest，放在 tests/ 目录
3. 日志用 logging 模块，不用 print
```

(a) 是 harness 自身的确定性验证，(b)(c) 让模型把 AGENT.md 内容复述出来——两者结合证明 system prompt 注入真的通了。

## v3：最简压缩（Compact）

v1 的 session 把所有消息都存下来。如果一次对话跑了几十个回合，messages 数组可能数百条——很容易超过模型的上下文窗口。注意被压的是 `messages` 数组，**不**是 v2 拼好的 system prompt——后者每轮通过 `provider.complete(system=...)` 单独传，永远不会进 `messages`，也永远不会被 compact 触及。

v3 做确定性压缩：把 `messages` 历史分成三段——**pinned**（messages 数组里最早的两条，通常是首轮 user prompt 和首轮 assistant 回复，能让模型记住任务起点）、**working**（最近 8 条，保持原样）、**compressed**（中间的早期消息压成一条结构化摘要）。

这里**不调 LLM 做摘要**——只统计消息数量、工具调用频次、读了哪些文件、改了哪些文件、跑了哪些命令。这比 LLM 摘要便宜、快、确定性强。更加接近原版的LLM 摘要设计留给 Day 11，我们先了解一下最简单的压缩功能。

### 3.1 新建 `agent_code/compact_basic.py`

压缩函数接受 messages 列表，返回压缩后的新列表。核心逻辑：提取前 2 条作为 pinned→扫描中间部分建统计→保留最后 8 条→拼接。

```python
from __future__ import annotations

from typing import Any


def compact(messages: list[dict[str, Any]], keep: int = 8) -> list[dict[str, Any]]:
    """确定性压缩消息历史。不调 LLM。

    返回三段拼接：
    1. pinned: 前 2 条（任务定义，不能丢）
    2. compressed: 一条概括中间消息的摘要 user message
    3. working: 最后 keep 条（最近的交互，保持完整）

    如果消息总数 <= keep + 2，不做压缩，直接返回原列表。"""
    pin_count = 2
    if len(messages) <= keep + pin_count:
        return messages  # 消息还不够多，不需要压缩

    pinned = messages[:pin_count]
    working = messages[-keep:]
    middle = messages[pin_count:-keep]
    compressed = _build_compressed_block(middle)
    return pinned + [compressed] + working


def _build_compressed_block(msgs: list[dict[str, Any]]) -> dict[str, Any]:
    """扫描被压缩的消息，提取结构化统计。
    如果 msgs 里包含上一轮 compact 留下的 <compacted-history> 块，保留为
    <previous-summary> 子块——重复 compact 时早期信息才不会丢。"""
    total = len(msgs)
    tool_names: set[str] = set()
    tool_count = 0
    files_read: set[str] = set()
    files_edited: set[str] = set()
    commands: list[str] = []
    previous_summary: str | None = None

    for msg in msgs:
        content = msg.get("content")
        # 识别上一轮 compact 自己写进来的 <compacted-history> 块，单独保留
        if isinstance(content, str) and content.startswith("<compacted-history>"):
            previous_summary = content
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                # assistant 消息里的工具调用请求
                tool_count += 1
                name = block.get("name", "")
                if name:
                    tool_names.add(name)
                args = block.get("input", {}) or {}
                # 分类统计文件操作和命令
                if name in ("read_file",) and args.get("path"):
                    files_read.add(str(args["path"]))
                elif name in ("file_write", "file_edit") and args.get("file_path"):
                    files_edited.add(str(args["file_path"]))
                elif name == "bash" and args.get("command"):
                    cmd = str(args["command"])
                    commands.append(cmd[:80] + "..." if len(cmd) > 80 else cmd)
            elif btype == "tool_result":
                # user 消息里的工具结果——不额外统计，tools-used 已经覆盖
                pass

    lines = ["<compacted-history>"]
    # 把上一轮摘要原文嵌进来——读者能直观看到"这是第 N 轮压缩，前面的事还在"
    if previous_summary:
        lines.append("  <previous-summary>")
        for ln in previous_summary.splitlines():
            lines.append("    " + ln)
        lines.append("  </previous-summary>")
    lines.extend([
        f"  <message-count>{total}</message-count>",
        f"  <tool-calls>{tool_count}</tool-calls>",
        f"  <tools-used>{', '.join(sorted(tool_names)) if tool_names else '(none)'}</tools-used>",
        f"  <files-read>{', '.join(sorted(files_read)) if files_read else '(none)'}</files-read>",
        f"  <files-edited>{', '.join(sorted(files_edited)) if files_edited else '(none)'}</files-edited>",
        f"  <commands-run>{', '.join(commands[:20]) if commands else '(none)'}</commands-run>",
        f"  <conclusions>(not yet supported — see Day 11)</conclusions>",
        "</compacted-history>",
    ])
    return {"role": "user", "content": "\n".join(lines)}
```

`_build_compressed_block` 扫描被压缩的消息，从 `tool_use` block 里提取工具名和参数——文件来自 `read_file`/`file_write`/`file_edit` 的 `path`/`file_path` 字段，命令来自 `bash` 的 `command` 字段。统计结果包在 `<compacted-history>` XML 块里，作为一条 user message 插入到 pinned 和 working 之间。

### 3.2 改 `agent_code/agent.py`：自动触发 compact

**第一处**，顶部 import 追加：

```python
from .compact_basic import compact  # Day 6：确定性压缩
```

**第二处**，在 Agent Loop 的 `provider.complete()` 之前（约第 84 行），加一段 compact 检查：

```python
        # Day 6：消息超过 40 条时自动压缩（保持上下文不爆）
        if len(messages) > 40:
            messages = compact(messages, keep=8)
            console.print(f"[dim]compacted: {len(messages)} messages remaining[/dim]")
```

Day 6 把阈值写死成 40，只是为了让我们在本地容易触发 compact，亲眼看到"旧消息被折叠成摘要、最近消息保留原样"这条链路。
真正的自动 compact 应该看 token 用量、tool_result 大小、模型上下文窗口和预留 buffer。Day 11 再把这个固定数字换成基于 token 预算的动态阈值。

这里的 compact 只改当前 `run_agent()` 里的内存 `messages`。Day 6 的 session JSONL 仍然是 append-only，不会被重写成压缩版；下一次 `--resume` 还是会从磁盘加载完整历史。这样做保留了原始审计日志，但也意味着长 session 的磁盘文件会继续增长。真正的“压缩后回写 session”留到 Day 11，再一起处理 token 计数和历史重写。

### 3.3 跑两个验证

直接跑 40 条消息太贵。v3 写一个小脚本验证 `compact()` 的输出。在 `agent-code` 项目根目录跑：

```bash
$ uv run python -c "
from agent_code.compact_basic import compact

# 构造 45 条假消息：user + assistant + tool_use + tool_result 交替
msgs = []
for i in range(15):
    msgs.append({'role': 'user', 'content': f'task step {i}'})
    msgs.append({'role': 'assistant', 'content': [
        {'type': 'text', 'text': f'doing step {i}'},
        {'type': 'tool_use', 'id': f'c{i}', 'name': 'read_file', 'input': {'path': f'file{i}.py'}},
    ]})
    msgs.append({'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': f'c{i}', 'content': f'content of file{i}'}
    ]})

print(f'before: {len(msgs)} messages')
result = compact(msgs, keep=8)
print(f'after: {len(result)} messages')
print()
# 看中间那条压缩摘要
for msg in result:
    content = msg['content']
    if isinstance(content, str) and 'compacted-history' in content:
        print(content)
"
```

预期输出：

```
before: 45 messages
after: 11 messages

<compacted-history>
  <message-count>35</message-count>
  <tool-calls>11</tool-calls>
  <tools-used>read_file</tools-used>
  <files-read>file1.py, file10.py, file11.py, file2.py, file3.py, file4.py, file5.py, file6.py, file7.py, file8.py, file9.py</files-read>
  <files-edited>(none)</files-edited>
  <commands-run>(none)</commands-run>
  <conclusions>(not yet supported — see Day 11)</conclusions>
</compacted-history>
```

11 条 = 2 pinned + 1 摘要 + 8 working。pinned 拿走第一轮 `file0.py` 的 tool_use，working 拿走最后三轮 `file12.py / file13.py / file14.py`，所以摘要只统计到 `file1.py` 到 `file11.py` 这 11 次 `read_file`。文件名按字典序排——这就是为什么 `file10.py` 排在 `file2.py` 前面。

再跑一个二次 compact 的小验证。如果一轮 compact 已经写过 `<compacted-history>`，下一轮 compact 不应该把这块当成普通消息丢掉——它应该被嵌进新摘要的 `<previous-summary>` 里：

```bash
$ uv run python -c "
from agent_code.compact_basic import compact

# 在已经 compact 过一次的 messages 上再追加 40 条，看二次 compact 怎么处理旧摘要
first_round = [{'role': 'user', 'content': 'kickoff'}, {'role': 'assistant', 'content': 'ok'}]
first_round.append({'role': 'user',
                    'content': '<compacted-history>\n  <message-count>35</message-count>\n  <tool-calls>11</tool-calls>\n</compacted-history>'})
for i in range(40):
    first_round.append({'role': 'user', 'content': f'new step {i}'})

result = compact(first_round, keep=8)
for msg in result:
    if isinstance(msg['content'], str) and 'compacted-history' in msg['content']:
        print(msg['content'])
        break
"
```

预期输出（节选）：

```
<compacted-history>
  <previous-summary>
    <compacted-history>
      <message-count>35</message-count>
      <tool-calls>11</tool-calls>
    </compacted-history>
  </previous-summary>
  <message-count>33</message-count>
  ...
</compacted-history>
```

旧 `<compacted-history>` 被原封不动嵌进新摘要的 `<previous-summary>` 子块——重复 compact 不会丢早期统计。`33` 这个数字来自：第二轮 `first_round` 共 `2 + 1 + 40 = 43` 条消息，`pin_count=2` 拿走前 2 条、`keep=8` 拿走后 8 条，middle 就剩 `43 - 2 - 8 = 33` 条（含 1 条旧 summary + 32 条新消息）。

v3 的压缩让会话不会无限膨胀。但三层记忆还差最后一层——跨 session 记忆。现在的状态：同一个 session 内能记住对话、AGENT.md 能注入项目规则，但如果你关闭终端、明天再开，模型完全不记得"你之前说过你是数据科学家"。v4 做这件事。

## v4：Memdir 长期记忆

v1-v3 的记忆都在"同一个 session"或"同一个项目规则文件"里。v4 加的是**跨 session 长期记忆**：模型在今天的 session 里知道了"用户是数据科学家，主要研究观测性"，把它写到一个记忆文件里。明天你新开一个 session，不用 `--resume`，模型也能通过 system prompt 里的记忆索引看到"这个用户好像研究观测性"，然后主动调 `memory_recall` 拉详细内容。

### 4.1 新建 `agent_code/memdir/` 子包

memdir 是四个文件组成的小型子包：

```
agent_code/memdir/
  __init__.py    re-export 公共 API
  paths.py       .agent/memory/ 目录布局 + 截断常量
  types.py       MemoryEntry 数据类 + slug 生成
  store.py       写入、召回、索引加载
```

#### 4.1.1 `agent_code/memdir/__init__.py`

```python
from .paths import get_memdir, ensure_memdir
from .types import MemoryEntry, make_slug, MEMORY_TYPES
from .store import write_memory, recall_memory, load_index
```

#### 4.1.2 `agent_code/memdir/paths.py`

管理 `.agent/memory/` 目录结构和 MEMORY.md 截断常量。

```python
from __future__ import annotations

from pathlib import Path

# memdir 根目录——放在 .agent/ 下，和 sessions/ 同级
MEMORY_DIR = ".agent/memory"
INDEX_FILE = "MEMORY.md"
# 索引文件保护：200 行 + 25KB 截断
# 索引一旦塞太多条，模型读 system prompt 时就被这一段挤掉别的上下文
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024


def get_memdir(cwd: Path) -> Path:
    """返回 .agent/memory 目录路径。"""
    return cwd / MEMORY_DIR


def ensure_memdir(cwd: Path) -> Path:
    """确保 .agent/memory 和四个类型子目录存在。返回 memdir 路径。"""
    memdir = get_memdir(cwd)
    memdir.mkdir(parents=True, exist_ok=True)
    for sub in ("user", "feedback", "project", "reference"):
        (memdir / sub).mkdir(exist_ok=True)
    return memdir


def index_path(cwd: Path) -> Path:
    """返回 MEMORY.md 索引文件路径。"""
    return get_memdir(cwd) / INDEX_FILE


def topic_path(cwd: Path, mem_type: str, slug: str) -> Path:
    """返回 .agent/memory/<type>/<slug>.md 路径。"""
    return get_memdir(cwd) / mem_type / f"{slug}.md"
```

四种类型对应记忆的不同用处：`user` 是关于你这个人（角色、偏好），`feedback` 是你给过的纠正和肯定，`project` 是项目背景（deadline、谁在做什么），`reference` 是外部系统的指针（Linear 项目名、Slack 频道）。

#### 4.1.3 `agent_code/memdir/types.py`

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# 四种记忆类型：user 是用户本身的事实，feedback 是用户反馈的做法，
# project 是项目里此刻在做什么，reference 是外部系统指针。
MEMORY_TYPES = ("user", "feedback", "project", "reference")


@dataclass
class MemoryEntry:
    """一条记忆的完整数据。"""
    mem_type: str       # user / feedback / project / reference
    title: str          # 人类可读标题
    slug: str           # 文件名安全标识
    body: str           # 正文（markdown）
    file_path: str      # 相对于 cwd 的路径，如 .agent/memory/user/my-role.md


def make_slug(title: str, max_len: int = 64) -> str:
    """把 title 转成文件名安全的 slug：只留 ASCII 字母数字 + 短横。
    title 是纯中文/纯日文等非 ASCII 内容时退到 hash slug，保证跨平台稳定。"""
    slug = title.lower().strip()
    # 只保留 a-z / 0-9 / 空格 / 短横；中文、表情等都会被丢掉
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug.strip("-")[:max_len]
    if not slug:
        # title 完全没 ASCII（例如纯中文）时，用 sha1 前 8 位兜底
        slug = "mem-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return slug
```

#### 4.1.4 `agent_code/memdir/store.py`

三个核心操作：`load_index`（启动时把 MEMORY.md 读进 system prompt）、`write_memory`（写 topic 文件 + 追加索引行）、`recall_memory`（关键字 grep 扫描 topic 文件）。

```python
from __future__ import annotations

from pathlib import Path

from .paths import ensure_memdir, get_memdir, index_path, topic_path, INDEX_MAX_LINES, INDEX_MAX_BYTES
from .types import MemoryEntry, MEMORY_TYPES, make_slug


def load_index(cwd: Path) -> str | None:
    """读取 MEMORY.md 索引文件。超过行数或字节上限时截断。
    文件不存在返回 None。"""
    ipath = index_path(cwd)
    if not ipath.exists():
        return None
    text = ipath.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return None
    # 截断保护：先按行数，再按字节数
    lines = text.splitlines()
    if len(lines) > INDEX_MAX_LINES:
        header = lines[:2] if lines[0].startswith("#") else []
        body = lines[2:] if header else lines
        # 保留头部 + 最新 200 行（尾部），丢掉中间
        lines = header + body[-(INDEX_MAX_LINES - len(header)):]
        text = "\n".join(lines)
    text_bytes = text.encode("utf-8")
    if len(text_bytes) > INDEX_MAX_BYTES:
        # 在字节边界截断：从后往前找最后一个完整 UTF-8 字符的换行
        truncated = text_bytes[:INDEX_MAX_BYTES].decode("utf-8", errors="replace")
        last_nl = truncated.rfind("\n")
        text = truncated[:last_nl] if last_nl > 0 else truncated
    return text


def write_memory(cwd: Path, mem_type: str, title: str, body: str) -> MemoryEntry:
    """写入一条长期记忆。同时做两件事：
    1. 写 .agent/memory/<type>/<slug>.md（带 frontmatter）
    2. 在 MEMORY.md 索引末尾追加一行引用

    两步放在同一个函数里走，正常路径会一起完成；如果中途索引更新失败，
    topic 文件已经写入，仍能被 recall 时的 scan 找到，不会丢记忆。"""
    if mem_type not in MEMORY_TYPES:
        raise ValueError(f"unknown memory type: {mem_type}, expected one of {MEMORY_TYPES}")

    ensure_memdir(cwd)
    slug = make_slug(title)

    # 防文件名冲突：如果 slug 已存在，加数字后缀
    tpath = topic_path(cwd, mem_type, slug)
    counter = 1
    while tpath.exists():
        tpath = topic_path(cwd, mem_type, f"{slug}-{counter}")
        counter += 1

    # 写 topic 文件——frontmatter 用 type + title 两个字段：
    #   type 是必填，决定文件归属哪个子目录、recall 时是否过滤
    #   title 是给人看的标题，也用来生成索引行的链接文本
    # 索引里的 hook 直接从 body 前 60 字派生，所以 topic 文件本身只需要这两个字段
    frontmatter = f"---\ntype: {mem_type}\ntitle: {title}\n---\n\n"
    tpath.write_text(frontmatter + body, encoding="utf-8")

    # 生成一句 hook：取 body 的前 60 个字符，截到最后一个完整词
    hook = body.strip()[:60]
    if len(body.strip()) > 60:
        hook = hook[:hook.rfind(" ")] + "..."

    # 追加索引行
    ipath = index_path(cwd)
    index_line = f"- [{title}]({mem_type}/{tpath.name}) — {hook}\n"
    if not ipath.exists():
        ipath.write_text("# Memory Index\n\n" + index_line, encoding="utf-8")
    else:
        with open(ipath, "a", encoding="utf-8") as f:
            f.write(index_line)

    return MemoryEntry(
        mem_type=mem_type,
        title=title,
        slug=tpath.stem,
        body=body,
        file_path=str(tpath.relative_to(cwd)),
    )


def recall_memory(cwd: Path, query: str, top_k: int = 5) -> list[MemoryEntry]:
    """关键字召回：扫描四个子目录下所有 .md 文件，把 query 按空格拆成
    keyword 列表，每个 keyword 在 title + body 里命中就加一分（不区分大小写）。
    按总分降序、同分按 mtime 倒序，返回 top_k 条匹配。"""
    # recall_memory 是纯只读——目录不存在时直接返回空，不要 mkdir，
    # 否则 plan 模式（只读硬约束）下调用 recall 会偷偷创建目录
    memdir = get_memdir(cwd)
    if not memdir.is_dir():
        return []
    keywords = query.lower().split()
    if not keywords:
        return []

    # (score, mtime, entry) 三元组：score 高的优先，同分按 mtime 新优先
    scored: list[tuple[float, float, MemoryEntry]] = []
    for mtype in MEMORY_TYPES:
        type_dir = memdir / mtype
        if not type_dir.is_dir():
            continue
        for md_file in type_dir.glob("*.md"):
            text = md_file.read_text(encoding="utf-8", errors="replace")
            text_lower = text.lower()
            # 简单 keyword match——每个 keyword 出现就加 1 分
            score = sum(1 for kw in keywords if kw in text_lower)
            if score == 0:
                continue
            # 解析 frontmatter 里的 title（取 --- 之间的 title: 行）
            title = md_file.stem.replace("-", " ").title()
            body = text
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].splitlines():
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip()
                    body = parts[2].strip()
            scored.append((
                score / len(keywords),  # 归一化到 0-1
                md_file.stat().st_mtime,
                MemoryEntry(
                    mem_type=mtype,
                    title=title,
                    slug=md_file.stem,
                    body=body,
                    file_path=str(md_file.relative_to(cwd)),
                )
            ))

    # 元组比较：先比 score 再比 mtime，两者都降序——分数高的优先，同分时新写的优先
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [entry for _, _, entry in scored[:top_k]]
```

`write_memory` 同时做两件事——写 topic 文件和追加索引行——这是刻意设计。如果拆成两个工具调用（先 Write 再 Edit MEMORY.md），模型可能只做第一步、忘记第二步，索引就永远不更新。绑在一个工具调用里保证一致性。

`recall_memory` 用 keyword grep 而不是 embedding——省掉向量数据库依赖，行为可预测、可调试。缺点是对中文分词不友好，思考题里会让读者琢磨这个问题。

### 4.2 改 `agent_code/permissions.py`：让 memory 工具走自动放行

`memory_write` / `memory_recall` 只动 `.agent/memory/`，不会改项目代码、不会跑命令。但要小心：

- 如果不动 permissions.py，它们会落到底部 `return PermissionDecision("ask")`——而 `agent.py` 的 ask 分发对 `memory_*` 没专用 confirm UI，会直接进入 `tools.run(call, ctx)` 执行，**用户根本看不到询问**。
- 如果把 `memory_write` 一起塞进 `_READONLY_TOOLS`，那 plan 模式（"硬只读"）也会让 `memory_write` 通过——破坏 Day 5 的 plan 模式边界。

主线为了少打断流程，分两个集合：`memory_recall` 是真只读，加进 `_READONLY_TOOLS`；`memory_write` 是低风险写，单独建一个集合，让它在 default / acceptEdits 自动放行，但 plan 模式仍然 deny。它仍然是一次持久化写入：如果你希望每条长期记忆都由人确认，可以不要加 `_LOW_RISK_WRITES`，而是在 `agent.py` 的 ask 分支给 `memory_write` 做一个专门的确认 UI。

**第一处**，找到 `_READONLY_TOOLS` 集合（约第 47 行），把 `memory_recall` 加进去：

```python
_READONLY_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep", "project_tree",
    "git_status", "git_diff",
    "system_date", "echo",
    "memory_recall",  # Day 6：memdir 召回是纯读，进 readonly 没问题
})
```

**第二处**，在 `_READONLY_TOOLS` 下面新增一个集合：

```python
# Day 6：写入范围被锁在 .agent/memory/ 的"低风险写"工具。
# default / acceptEdits 直接放行；plan 模式仍然 deny——plan 的硬约束就是只读。
_LOW_RISK_WRITES = frozenset({"memory_write"})
```

**第三处**，改 `decide_permission` 里 plan 模式的 deny 文案不需要动；只在 plan 模式分支之后、最后的 `return PermissionDecision("ask")` 之前，加一段处理 `_LOW_RISK_WRITES`。在原来的：

```python
    # 只读工具在所有模式下默认允许
    if tool_name in _READONLY_TOOLS:
        return PermissionDecision("allow")
```

之后，加上：

```python
    # Day 6：低风险写工具（memory_write）在 default / acceptEdits 自动放行；
    # plan 模式上面那段已经 deny 掉，不会走到这里
    if tool_name in _LOW_RISK_WRITES:
        return PermissionDecision("allow")
```

这样 `memory_write` 在 default 模式不弹确认窗（教学便利），在 plan 模式被 deny（保持 plan = 只读硬约束）；`memory_recall` 在所有模式都 allow。

### 4.3 改 `agent_code/tools.py`：注册两个新工具

**第一处**，在 `default_tools()` 函数里，`return registry` 之前，注册 `memory_write` 和 `memory_recall`。先把工具函数写在 `default_tools()` 之前的模块级（放在 `bash` 函数后面）：

```python
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
```

**第二处**，在 `default_tools()` 里注册。找到 `return registry` 之前的位置（`ask_user_question` 注册之后），加上两个工具注册：

```python
    # --- Day 6：长期记忆工具 ---
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
        )
    )
```

注册到这里之后，Day 5 权限引擎会按 4.2 加好的白名单走 allow 路径，工具直接执行，不弹任何确认窗口。

### 4.4 改 `agent_code/agent.py`：MEMORY.md 索引注入 system prompt

v2 的 `build_system_prompt` 只拼了 core + AGENT.md。v4 给它加上 MEMORY.md 索引这一段。`cli.py` 不用动——它仍然在 cold start 调一次 `build_system_prompt(cwd)`，结果里现在多了跨 session 记忆这一层。

把 v2 写的 `build_system_prompt` 整个替换成：

```python
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
```

注意 `load_memory_index` 的 import 写在函数体内——这是为了不强制 memdir 子包在 v2/v3 阶段就存在。读者跟着教程一步步加代码，v4 才创建 `memdir/` 目录。函数名继续叫 `build_system_prompt`（v2 已经从下划线前缀改成公开命名），cli.py 这边什么也不用改。

### 4.5 跑验证

下面分两步：先做**函数级确定性验证**，确认 `write_memory` / `recall_memory` / `build_system_prompt` 的实现是对的；然后再让真实模型在 `agent-code` 里走一遍，看 prompt + 工具调用是否能串起来。后者是行为演示，前者才是 ship-or-not 的硬验证。

**(a) 函数级确定性验证**——直接调 memdir API，在临时目录里跑，不污染读者真实项目的 `.agent/memory/`：

```bash
$ uv run python -c "
import tempfile
from pathlib import Path
from agent_code.memdir.store import write_memory, recall_memory
from agent_code.agent import build_system_prompt

with tempfile.TemporaryDirectory() as tmp:
    cwd = Path(tmp)
    entry = write_memory(cwd, 'user', '用户角色', '用户是数据科学家，主要研究方向是观测性。')
    print('written:', entry.file_path)
    hits = recall_memory(cwd, '数据科学家')
    print('recalled:', len(hits), 'entries')
    print('first hit type:', hits[0].mem_type, 'title:', hits[0].title)
    sp = build_system_prompt(cwd)
    print('project-memory in system prompt:', '<project-memory>' in sp)
"
```

预期输出：

```
written: .agent/memory/user/mem-247c9e11.md
recalled: 1 entries
first hit type: user title: 用户角色
project-memory in system prompt: True
```

这一步不经过模型、用 tempdir 隔离，所有断言都是确定的：写入成功、召回命中、索引注入 system prompt。任何一条出错都说明前面 store.py / agent.py 的代码没改对。tempdir 用完就回收，不会留下任何文件——下一步 (b) 真实写入的是读者自己 cwd 下的全新 memdir。

**(b) 真实模型 + agent-code 写入一条记忆**（行为演示，模型可能选不同的 title，路径里的 hash 也跟着变）：

```bash
$ uv run agent-code "我是数据科学家，主要研究观测性。记住这一点。"
Agent Code
cwd: /your/project
session: d1e2f3a4b5c6

tool_call: memory_write {'type': 'user', 'title': '用户角色', 'body': '用户是数据科学家，主要研究方向是观测性。'}
observation: Memory saved: [user] 用户角色 -> .agent/memory/user/mem-247c9e11.md
final: 已记住。下次对话时我会记得你是数据科学家，研究方向是观测性。
```

`mem-247c9e11` 是 `make_slug("用户角色")` 落到 sha1 兜底的结果——纯中文 title 没有 ASCII 字母数字，所以走 hash fallback。你的输出里这个后缀可能不同，因为它取决于具体 title 文本。

检查文件是否生成：

```bash
$ ls .agent/memory/user/
mem-247c9e11.md    # 具体哈希因 title 而异

$ cat .agent/memory/user/mem-*.md
---
type: user
title: 用户角色
---

用户是数据科学家，主要研究方向是观测性。

$ cat .agent/memory/MEMORY.md
# Memory Index

- [用户角色](user/mem-247c9e11.md) — 用户是数据科学家，主要研究方向是观测性。
```

**(c) 新 session 召回记忆。**为了稳定看到 `memory_recall` 被调用，prompt 直接指定工具名——否则模型可能从 `MEMORY.md` 索引里直接答（见 (d)）：

```bash
$ uv run agent-code "用 memory_recall 工具查一下你对我有什么记忆"    # 不加 -c，全新 session
Agent Code
cwd: /your/project
session: f8a1b2c3d4e5

tool_call: memory_recall {'query': '用户 数据科学家 观测性', 'top_k': 5}
observation: ## [user] 用户角色
  file: .agent/memory/user/mem-247c9e11.md
  用户是数据科学家，主要研究方向是观测性。

final: 根据我的记忆，你是一名数据科学家，主要研究观测性方向。
```

模型在新 session 里不记得你的对话历史（没加 `-c`），但通过 `memory_recall` 从 memdir 里找到了之前写的记忆。`query` 里的具体词由模型选，你看到的可能略有不同。

**(d) MEMORY.md 在 system prompt 里，模型不需要每次都调 recall。**注意这是模型行为演示，不保证每次都不调 `memory_recall`——确定性的"索引确实注入"已经在 (a) 用 `build_system_prompt` 验过；这里只是看模型用起来什么样：

```bash
$ cat .agent/memory/MEMORY.md
# Memory Index

- [用户角色](user/mem-247c9e11.md) — 用户是数据科学家，主要研究方向是观测性。
```

只要 `MEMORY.md` 存在并有内容，`build_system_prompt` 就会把它包进 `<project-memory>` 块注入 system。下面的 `agent-code` 输出展示的是"模型看到索引后通常会直接答"，不同 prompt 或不同模型可能会再调一次 recall：

```bash
$ uv run agent-code "你有什么关于我的记忆"
...
final: 根据系统提示中的记忆索引，我有一条关于你的记忆——你是一名数据科学家，研究方向是观测性。
```

因为 MEMORY.md 索引已经注入 system prompt（`<project-memory>` 块），模型有时直接引用索引就能回答，不需要额外调 `memory_recall`。只有当索引里的 hook 写得太简略、模型需要看详细正文时，它才会调 `memory_recall` 拉 topic 文件。

## 收尾：今天的终版文件改动清单

| 文件 | 改动 |
|---|---|
| `agent_code/session.py` | 新文件：`Session` 类——create / load_latest / load_id / history / append_messages |
| `agent_code/project_memory.py` | 新文件：`load_agent_md(cwd)`——读 AGENT.md 包装成 `<project-rules>` 块 |
| `agent_code/compact_basic.py` | 新文件：`compact(messages, keep=8)`——三段压缩 + 确定性统计摘要 |
| `agent_code/memdir/__init__.py` | 新文件：re-export 公共 API |
| `agent_code/memdir/paths.py` | 新文件：`.agent/memory/` 目录布局、`INDEX_MAX_LINES=200`、`INDEX_MAX_BYTES=25*1024` |
| `agent_code/memdir/types.py` | 新文件：`MemoryEntry` 数据类、`make_slug(title)`、`MEMORY_TYPES` 常量 |
| `agent_code/memdir/store.py` | 新文件：`load_index`、`write_memory`（写 topic + 追加索引行）、`recall_memory`（keyword grep） |
| `agent_code/model.py` | `ModelProvider.complete()` 加 `system` / `enable_prompt_cache` 参数；`AnthropicProvider` 转发 system prompt，并在可选开关打开时给 system / tools / message 前缀加 `cache_control`；`MockProvider` 签名对齐 |
| `agent_code/agent.py` | `run_agent()` 加 `session` / `system_prompt` / `enable_prompt_cache` 参数；消息初始化从 session.history 恢复；每轮落盘；公开函数 `build_system_prompt(cwd)` 组装 core + AGENT.md + MEMORY.md 索引；`provider.complete(..., system=...)` 转发；超 40 条消息触发 compact |
| `agent_code/cli.py` | `--resume <id>` 和 `-c`/`--continue` Option；`run_once` 加 `session` / `system_prompt` / `enable_prompt_cache` 参数；`main_command` cold start 调一次 `build_system_prompt`；`AGENT_CODE_PROMPT_CACHE=1` 时打开 prompt cache 标记；REPL 共享 session 和 system_prompt |
| `agent_code/permissions.py` | `memory_recall` 加进 `_READONLY_TOOLS`；新增 `_LOW_RISK_WRITES = {"memory_write"}` 在 default/acceptEdits allow、plan 仍 deny |
| `agent_code/tools.py` | `_memory_write` + `_memory_recall` 工具函数；`default_tools()` 注册两个新工具 |

## 手动 trace 一遍

输入 `"我是数据科学家，记住这一点。然后告诉我你知道什么关于我的信息。"`，不传任何 flag：

```txt
1. CLI 解析 prompt，没有 --resume / -c → Session.create(cwd) 生成新 session id。
2. run_agent() 调用：system_prompt = core + AGENT.md（如果有）+ MEMORY.md 索引（如果有）。
3. messages 从 [{"role":"user", "content":"我是数据科学家..."}] 冷启动。
4. 模型收到 system_prompt（含 MEMORY.md 索引，目前为空）+ messages + tools（含 memory_write/memory_recall）。
5. 模型返回 tool_use: memory_write {type:"user", title:"用户角色", body:"..."}。
6. Agent Loop 构造 PermissionRequest(tool_name="memory_write", ...) → decide_permission。
   v4.2 把 memory_write 加进自动放行集合 → allow → 直接执行，不弹确认。
7. memdir.store.write_memory()：写 .agent/memory/user/mem-<hash>.md + 追加 MEMORY.md 索引行。
8. tool_result 回灌 → 模型拿到 "Memory saved: [user] 用户角色 -> ..."。
9. 模型返回 tool_use: memory_recall {query:"用户 角色 信息", top_k:5}。
10. memdir.store.recall_memory()：扫描 user/feedback/project/reference/ 下所有 .md，
    在 title + body 中 keyword grep "用户" "角色" "信息"，返回匹配条目。
11. tool_result 回灌 → 模型看到刚才写的记忆全文。
12. 模型返回 final："根据记忆，你是一名数据科学家..."
13. Agent Loop 落盘：session.append_messages() 把本轮新增消息写入 JSONL。
```

## 今天有了什么

- **Session JSONL + --resume/--continue**：每次对话自动落盘到 `.agent/sessions/<sanitized_cwd>/<id>.jsonl`。`-c` 恢复最近会话，`--resume <id>` 恢复指定会话。不加 flag 开新 session。落盘格式是 `{role, content, timestamp}`，一条消息一行 JSON。
- **AGENT.md 项目规则注入**：启动时自动读 cwd 下的 `AGENT.md`，包装成 `<project-rules>` XML 块注入 system prompt。模型在每一次推理中都看得见项目规则——不需要你每次 prompt 里重复。
- **可选 Prompt Cache 标记**：`AGENT_CODE_PROMPT_CACHE=1` 时，provider 会给 `system`、`tools` 和最后一条 message 加 Anthropic `cache_control`。支持 prompt cache 的服务可以复用稳定前缀；不支持的兼容 endpoint 保持默认关闭。
- **确定性 Compact**：消息超过 40 条时自动触发压缩。把 `messages` 数组切成 pinned（最早两条）/ working（最近 8 条）/ compressed（中间压成结构化统计：消息数、工具调用、文件操作、命令）三段，不调 LLM。system prompt 通过 `provider.complete(system=...)` 单独传，不在 `messages` 里，也不会被 compact 触及。
- **Memdir 长期记忆**：四类型（user/feedback/project/reference）跨 session 记忆系统。`memory_write` 一次调用同时写 topic 文件 + 更新索引；`memory_recall` keyword grep 扫描 topic 文件全文。MEMORY.md 索引起步注入 system prompt，模型看到索引 hook 决定是否调 recall 拉详细。
- **System prompt 组装**：`core prompt → AGENT.md → MEMORY.md 索引`——三层拼接，通过 Anthropic API 的 `system=` 参数一次性注入。每一层都是可选的（文件不存在就跳过），不强制要求用户配置。

## 常见问题

### `-c` 报 "没有找到历史会话"

你的 cwd 下还没有 `.agent/sessions/` 目录。先跑一次不带 flag 的命令创建 session，退出后再用 `-c`。

```bash
$ uv run agent-code "hello"    # 创建第一个 session
$ uv run agent-code -c "继续"  # 现在可以 --continue 了
```

### `--resume` 之后为什么还要重新读文件

`--resume` 恢复的是模型上下文，不是本地进程状态。JSONL 里有上次的 `read_file` 结果，但新的 CLI 进程里 `ReadFileState` 是空的；如果直接 `file_edit`，Day 4 的 read-before-edit 保护会要求重新读取。这个限制是有意保留的：恢复历史不能证明磁盘文件没有被你或其他工具改过。

### compact 之后模型"忘了"之前的对话

这是预期行为——compressed block 只包含统计信息（调了什么工具、读了什么文件），不包含对话的具体语义。如果有一条重要结论在 working 之外被压缩掉了，模型确实会"忘记"。Day 11 的 LLM 摘要会补上语义压缩。目前可以靠 `memory_write` 把关键结论记进 memdir——这不受 compact 影响。

### `memory_recall` 搜中文的命中规则是什么

`query` 先按空格拆成关键词列表，每个关键词在 topic 文件的 title+body 里命中就加一分，分数高的排前面。**任一个关键词命中就会被召回，不是必须所有词都出现**——所以"数据 科学"和"数据科学家"都能找到包含"数据科学家"的那条记忆，前者两词都命中得 2 分，后者 1 分。中文没空格分隔，整句当一个 keyword，所以搜"数据科学家"只算一分。要换 embedding 或中文分词当然可以，但会引入额外依赖；课后挑战里我们试着给 `recall_memory` 加一个简单的 bigram 分词看看效果。

### memory_write 之后为什么文件名是 `mem-247c9e11.md` 不是拼音

`make_slug` 只保留 ASCII 字母数字。纯中文/日文 title（如"用户角色"）会被正则过滤成空字符串，函数会落到 `hashlib.sha1(title)` 兜底，生成 `mem-<前 8 位>` 这种文件名。今天先不引入 `pypinyin` 依赖，保持标准库实现；这也避免了不同操作系统/文件系统的 Unicode 归一化差异。

要自己复刻例子里的 hash：

```bash
$ python3 -c "import hashlib; print('mem-' + hashlib.sha1('用户角色'.encode('utf-8')).hexdigest()[:8])"
mem-247c9e11
```

换个 title hash 就变，但同一个 title 跨进程跨机器都是同一个 slug——这是 sha1 的确定性保证。

### MEMORY.md 超过 200 行或 25KB 怎么办

`load_index` 在读出时做截断——超过 200 行就保留头部 + 最新 200 行，超过 25KB 就按字节边界截。写入时不做截断——下次启动时读到截断版，下次写入还是拼到完整版后面。这会在极端情况下导致 MEMORY.md 越来越大（虽然不会爆上下文），课后挑战可以补上写入时截断。

## 课后挑战

1. **`/sessions` 命令列出历史会话**：给 `cli.py` 加一个 slash command，扫描 `.agent/sessions/` 下所有 jsonl 文件，打印 session_id、消息数、最后更新时间。数消息数时只计 user 和 assistant。

2. **session 导出 markdown**：给 `session.py` 加一个 `export_markdown(output_path)` 方法，把 JSONL 历史转成人类友好的 markdown 格式——每条消息标上 role 和时间，tool_use 用代码块包裹。

3. **中文分词 recall**：给 `recall_memory` 加一个可选的分词策略。在不引入 jieba 的前提下，可以用"每个字作为 unigram + 相邻两个字作为 bigram"——搜"数据"能同时命中"数"和"据"和"数据"。这种策略的精确率和召回率会怎么变化？

4. **写入时截断 MEMORY.md**：在 `write_memory` 加一截断逻辑：写完后如果索引超过 200 行，去掉最老的条目（保留头部 `# Memory Index`），在尾部追加。这比只在 `load_index` 读时截断更干净——索引文件始终保持在可控大小。

5. **从 session 历史自动提取 memdir**：写一个小脚本扫描 session JSONL，从对话里提取"用户说过的关于自己的事实"（我是 XX、我在做 YY），自动调 `memory_write` 存进 memdir。不要求高准确率，能提个 3-5 条就行。

## 思考题

1. **为什么三层记忆（session JSONL / AGENT.md / memdir）职责要分开，不能全塞进一个文件？** （提示：三种记忆的写入者不一样——session 是 harness 自动写、AGENT.md 是你手动写、memdir 是模型调工具写。它们的生命周期和可靠性要求有什么不同？）

2. **compact 的确定性统计和 LLM 语义摘要，什么场景下前者够用、什么场景下必须后者？** （提示：想一下模型在第一次 compact 之后被要求"继续上次没做完的 bug fix"——模型从统计里能知道"上次改过哪些文件"，但它知道"bug 是什么、排查到哪一步了"吗？）

3. **MEMORY.md 索引注入 system prompt vs 让模型每次都主动调 `memory_recall`，这两个策略各有什么适用场景？** （提示：索引全文有多大？模型看 500 条索引 hook 的开销 vs 每次 `memory_recall` 一个来回的延迟开销。）

4. **`memory_write` 同时写 topic 文件和索引行——如果这中间崩溃（写完 topic 但没来得及更新索引），`recall_memory` 能不能找到这条记忆？启动时的 `load_index` 呢？如果反过来（索引更新了但 topic 没写完），又是什么结果？** （提示：`recall_memory` 扫描的是 topic 文件的 mtime 倒序，不看索引；`load_index` 只看索引文件本身。）

## 下一天

今天 Agent 有了三层记忆：session 历史记录、AGENT.md 项目规则、memdir 跨 session 长期记忆。单 Agent CLI 的核心能力到这里基本完整了——会读代码、改文件、跑命令、有权限控制、能记住上下文。

下一天进入 harness 的"自定义层"——**Slash Commands + Hooks + Cron**。让你能通过 `/` 命令控制 Agent 行为（不只是 `/help` 和 `/exit`），注册钩子函数在工具调用前后拦截，以及用 cron 表达式设置定时任务。这些能力让 Agent 从一个"模型 + 工具"的组合变成可编程、可扩展的命令行工作台。
