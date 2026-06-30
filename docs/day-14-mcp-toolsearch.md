# Day 14：MCP 生态 + ToolSearch

前 13 天，我们的工具都写在 `agent_code/tools.py` 里。这样适合教学起步，但真实代码 Agent 不可能把所有能力都内置进去。

今天做最后一块：MCP。外部 server 可以通过标准协议把工具交给 harness；工具多到塞不进 prompt 时，再用 ToolSearch 按需发现。

跑完之后你会看到：

- `agent-code` 能用 stdio 启动一个 MCP server。
- 最小 JSON-RPC 流程是 `initialize -> initialized -> tools/list -> tools/call`。
- MCP 工具会被命名成 `mcp__<server>__<tool>`，像普通工具一样回填 `tool_result`。
- `.mcp.json` 决定项目工具池，换工具只改配置。
- MCP 工具默认 deferred，不把完整 schema 全塞进首轮 prompt。
- `tool_search(query)` 会把匹配的 deferred 工具“解锁”为可调用工具。

代码约 760 行，新增约 480 行。

今天分五版：

1. v1：stdio MCP client 骨架，连 echo server 并列工具。
2. v2：把 MCP 工具接入 ToolRegistry。
3. v3：`.mcp.json` 配置发现和生命周期。
4. v4：ToolSearch + deferred tools。
5. v5：git/sqlite 端到端 2.0。

## 起手：今天的起点

Day 14 新增这些模块：

```txt
agent_code/mcp/protocol.py
agent_code/mcp/client.py
agent_code/mcp/config.py
agent_code/mcp/registry.py
agent_code/tool_pool.py
agent_code/tools/tool_search.py
```

今天也需要两个 demo server。教程里会让你创建：

```txt
examples/mcp-echo-server/server.py
examples/mcp-sqlite-server/server.py
```

先记住一条边界：MCP server 不是模型，MCP tool 也不是模型自己执行。它只是把工具 schema 和调用入口交给 harness。模型仍然只发 `tool_use`；真正 `tools/call` 的还是 Python harness。

## v1：先跑通 stdio JSON-RPC

第一版不接 Agent Loop。我们只证明：能启动一个 server，发 `initialize`，再发 `tools/list`。

### 1.1 创建 echo MCP server

在项目根目录执行：

```bash
mkdir -p examples/mcp-echo-server
cat > examples/mcp-echo-server/server.py <<'EOF'
from __future__ import annotations

import json
import sys


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(req: dict) -> None:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo", "version": "0.1.0"},
            },
        })
        return

    if method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [{
                    "name": "echo",
                    "description": "Return the input text.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                    "annotations": {"readOnlyHint": True},
                }]
            },
        })
        return

    if method == "tools/call":
        params = req.get("params", {})
        text = params.get("arguments", {}).get("text", "")
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": str(text)}]},
        })
        return

    if req_id is not None:
        send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method: {method}"}})


for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    # initialized 是 notification，没有 id，不需要响应
    if msg.get("method") == "notifications/initialized":
        continue
    handle(msg)
EOF
```

这个 server 用一行 JSON 作为一条 JSON-RPC message。够教学，生产里可以换正式 MCP SDK。

### 1.2 新增 `agent_code/mcp/protocol.py`

```python
from __future__ import annotations

import json
import subprocess
import threading
from typing import Any


class JsonRpcPeer:
    def __init__(self, proc: subprocess.Popen[str], timeout: float = 10.0) -> None:
        self.proc = proc
        self.timeout = timeout
        self._next_id = 1
        self._lock = threading.Lock()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                payload["params"] = params
            self._write(payload)
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP server closed stdout")
                msg = json.loads(line)
                if msg.get("id") != req_id:
                    continue
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", msg["error"]))
                return msg.get("result", {})

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def _write(self, payload: dict[str, Any]) -> None:
        self.proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
```

### 1.3 新增 `agent_code/mcp/client.py`

```python
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .protocol import JsonRpcPeer


@dataclass
class McpServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class McpClient:
    def __init__(self, name: str, config: McpServerConfig) -> None:
        self.name = name
        self.config = config
        self.proc: subprocess.Popen[str] | None = None
        self.peer: JsonRpcPeer | None = None

    def connect(self) -> list[dict[str, Any]]:
        env = os.environ.copy()
        env.update(self.config.env)
        self.proc = subprocess.Popen(
            [self.config.command, *self.config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.peer = JsonRpcPeer(self.proc)
        self.peer.request("initialize", {"clientInfo": {"name": "agent-code", "version": "0.1.0"}})
        self.peer.notify("notifications/initialized")
        return self.list_tools()

    def list_tools(self) -> list[dict[str, Any]]:
        assert self.peer is not None
        result = self.peer.request("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        assert self.peer is not None
        result = self.peer.request("tools/call", {"name": name, "arguments": arguments})
        parts: list[str] = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)

    def shutdown(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.proc.kill()
```

### 1.4 跑最小连接

```bash
$ uv run python - <<'PY'
from agent_code.mcp.client import McpClient, McpServerConfig

client = McpClient("echo", McpServerConfig("python", ["examples/mcp-echo-server/server.py"]))
tools = client.connect()
print(f"[mcp:echo] connected, tools={[t['name'] for t in tools]}")
client.shutdown()
PY
[mcp:echo] connected, tools=['echo']
```

v1 到这里就够了：stdio、JSON-RPC、initialize、tools/list 跑通。

## v2：把 MCP 工具接进工具池

现在要把 echo server 的 `echo` 变成模型能调用的工具名：

```txt
mcp__echo__echo
```

注意：模型看到的是 `mcp__echo__echo`，但发给 server 的仍然是原始工具名 `echo`。

### 2.1 新增 `agent_code/mcp/registry.py`

```python
from __future__ import annotations

import re
from typing import Any

from .client import McpClient
from ..model import ToolCall, ToolResult
from ..tools import Tool, ToolContext


def normalize_part(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")


def build_mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp__{normalize_part(server)}__{normalize_part(tool)}"


def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


class McpRegistry:
    def __init__(self) -> None:
        self.clients: dict[str, McpClient] = {}
        self.tool_to_raw: dict[str, tuple[str, str]] = {}

    def register_client(self, name: str, client: McpClient, schemas: list[dict[str, Any]]) -> list[Tool]:
        self.clients[name] = client
        tools: list[Tool] = []
        for schema in schemas:
            raw_name = schema["name"]
            tool_name = build_mcp_tool_name(name, raw_name)
            self.tool_to_raw[tool_name] = (name, raw_name)
            read_only = bool(schema.get("annotations", {}).get("readOnlyHint", False))
            tools.append(Tool(
                name=tool_name,
                description=schema.get("description", ""),
                run=self._make_runner(tool_name),
                parameters=schema.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
                is_read_only=read_only,
            ))
        return tools

    def _make_runner(self, tool_name: str):
        def run(args: dict[str, Any], ctx: ToolContext) -> str:
            server, raw_tool = self.tool_to_raw[tool_name]
            return self.clients[server].call_tool(raw_tool, args)
        return run

    def shutdown_all(self) -> None:
        for client in self.clients.values():
            client.shutdown()
```

### 2.2 把 MCP tool 注册进 `ToolRegistry`

先手动接 echo：

```python
from agent_code.mcp.client import McpClient, McpServerConfig
from agent_code.mcp.registry import McpRegistry
from agent_code.tools import default_tools

tools = default_tools()
mcp = McpRegistry()
client = McpClient("echo", McpServerConfig("python", ["examples/mcp-echo-server/server.py"]))
schemas = client.connect()
for tool in mcp.register_client("echo", client, schemas):
    tools.register(tool)
```

真实接入时，这段会放进 CLI 启动流程；v2 先用 Python 验证：

```bash
$ uv run python - <<'PY'
from pathlib import Path
from agent_code.mcp.client import McpClient, McpServerConfig
from agent_code.mcp.registry import McpRegistry
from agent_code.model import ToolCall
from agent_code.tools import ToolContext, default_tools

tools = default_tools()
mcp = McpRegistry()
client = McpClient("echo", McpServerConfig("python", ["examples/mcp-echo-server/server.py"]))
for tool in mcp.register_client("echo", client, client.connect()):
    tools.register(tool)

result = tools.run(ToolCall("call_1", "mcp__echo__echo", {"text": "hello mcp"}), ToolContext(cwd=Path.cwd()))
print(result.content)
mcp.shutdown_all()
PY
hello mcp
```

到这里，MCP 工具对 Agent Loop 来说就是普通工具了。

## v3：`.mcp.json` 决定工具池

手动写连接代码不现实。项目应该用 `.mcp.json` 声明工具池。

### 3.1 新增配置文件

项目根目录新建：

```json
{
  "mcpServers": {
    "echo": {
      "command": "python",
      "args": ["examples/mcp-echo-server/server.py"],
      "env": {}
    }
  }
}
```

用户级配置放：

```txt
~/.config/agent-code/mcp.json
```

项目级覆盖用户级。

### 3.2 新增 `agent_code/mcp/config.py`

```python
from __future__ import annotations

import json
from pathlib import Path

from .client import McpServerConfig


def _load(path: Path) -> dict[str, McpServerConfig]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    servers: dict[str, McpServerConfig] = {}
    for name, raw in data.get("mcpServers", {}).items():
        servers[name] = McpServerConfig(
            command=raw["command"],
            args=list(raw.get("args", [])),
            env=dict(raw.get("env", {})),
        )
    return servers


def load_mcp_config(cwd: Path) -> dict[str, McpServerConfig]:
    user = _load(Path.home() / ".config" / "agent-code" / "mcp.json")
    project = _load(cwd / ".mcp.json")
    merged = dict(user)
    merged.update(project)
    return merged
```

### 3.3 CLI 启动 connect，退出 shutdown

在 `cli.py` 里：

```python
from .mcp.config import load_mcp_config
from .mcp.client import McpClient
from .mcp.registry import McpRegistry


def connect_mcp_tools(cwd: Path, tools) -> McpRegistry:
    registry = McpRegistry()
    for name, config in load_mcp_config(cwd).items():
        client = McpClient(name, config)
        schemas = client.connect()
        for tool in registry.register_client(name, client, schemas):
            tools.register(tool)
        console.print(f"[dim][mcp:{name}] connected, tools={[s['name'] for s in schemas]}[/dim]")
    return registry
```

交互模式：

```python
tools = default_tools()
mcp_registry = connect_mcp_tools(state.cwd, tools)
try:
    run_interactive_shell(...)
finally:
    mcp_registry.shutdown_all()
```

one-shot 也一样，`run_once()` 里创建 tools 后 connect，结束后 shutdown。

跑验证：

```bash
$ uv run agent-code --provider mock "用 echo 工具说 hi"
[mcp:echo] connected, tools=['echo']
...
```

真实模型验证：

```bash
$ uv run agent-code "必须调用 mcp__echo__echo，参数 text=hello"
tool_call: mcp__echo__echo {'text': 'hello'}
final: hello
```

## v4：ToolSearch，不要把所有 schema 常驻 prompt

如果你接了 5 个 MCP server，每个 20 个工具，把全部 schema 都塞进首轮请求，上下文会被工具描述挤掉。

所以 MCP 工具默认 deferred：首轮只告诉模型有哪些名字；模型需要时先调用 `tool_search(query)`，命中的工具再进入可调用集合。

### 4.1 `RuntimeState` 记录 discovered

```python
    deferred_tools: dict[str, str] = field(default_factory=dict)      # name -> description
    discovered_tool_names: set[str] = field(default_factory=set)
```

### 4.2 新增 `agent_code/tool_pool.py`

```python
from __future__ import annotations

from .tools import ToolRegistry


class ToolPool:
    def __init__(self, registry: ToolRegistry, deferred_names: set[str]) -> None:
        self.registry = registry
        self.deferred_names = deferred_names

    def visible_registry(self, discovered: set[str]) -> ToolRegistry:
        visible = ToolRegistry()
        for tool in self.registry.list():
            if tool.name in self.deferred_names and tool.name not in discovered:
                continue
            visible.register(tool)
        return visible

    def render_deferred(self, discovered: set[str]) -> str:
        lines = ["<available-deferred-tools>"]
        for tool in self.registry.list():
            if tool.name in self.deferred_names and tool.name not in discovered:
                lines.append(f"- {tool.name}: {tool.description}")
        lines.append("</available-deferred-tools>")
        return "\n".join(lines) if len(lines) > 2 else ""
```

MCP 工具放进 `deferred_names`，内置工具不放。`tool_search` 自己也不能 defer。

### 4.3 新增 `tool_search`

新建 `agent_code/tools/tool_search.py`：

```python
from __future__ import annotations

from typing import Any

from ..tools import ToolContext


def tool_search(args: dict[str, Any], ctx: ToolContext) -> str:
    state = ctx.runtime_state
    if state is None:
        return "error: no runtime state"
    query = str(args.get("query", "")).lower().strip()
    if not query:
        return "error: missing required argument 'query'"

    matches: list[str] = []
    for name, description in state.deferred_tools.items():
        haystack = f"{name} {description}".lower()
        if query in haystack or any(part in haystack for part in query.split()):
            matches.append(name)
    if not matches:
        return "(no matching deferred tools)"

    state.discovered_tool_names.update(matches)
    lines = ["Discovered tools:"]
    for name in matches[:10]:
        lines.append(f"- {name}: {state.deferred_tools.get(name, '')}")
    return "\n".join(lines)
```

注册到内置 tools：

```python
registry.register(Tool(
    name="tool_search",
    description="Search deferred tools by keyword and make matching tools available in later turns.",
    run=tool_search,
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    is_read_only=False,
))
```

### 4.4 provider 只看 visible tools

在 `agent.py` provider call 前：

```python
pool = getattr(state, "tool_pool", None)
if pool is not None:
    visible_tools = pool.visible_registry(state.discovered_tool_names)
else:
    visible_tools = tools
visible_tools = visible_tools.filtered(state.skill_allowed_tools)
```

system prompt 里追加 deferred 列表：

```python
if state is not None and getattr(state, "tool_pool", None) is not None:
    deferred = state.tool_pool.render_deferred(state.discovered_tool_names)
    if deferred:
        parts.append(deferred)
```

CLI connect MCP 后：

```python
deferred_names = set()
for mcp_tool in mcp_tools:
    tools.register(mcp_tool)
    deferred_names.add(mcp_tool.name)
    state.deferred_tools[mcp_tool.name] = mcp_tool.description
state.tool_pool = ToolPool(tools, deferred_names)
```

### 4.5 `/context` 显示工具数量

在 Day 11 的 `/context` 里追加：

```python
if ctx.state and getattr(ctx.state, "tool_pool", None):
    total_deferred = len(ctx.state.deferred_tools)
    discovered = len(ctx.state.discovered_tool_names)
    message += f"\nInline tools discovered: {discovered}\nDeferred tools: {total_deferred - discovered}"
```

跑验证：

```bash
> /context
Inline tools discovered: 0
Deferred tools: 1

> 请先 tool_search 搜 echo，再调用 MCP echo 工具说 hello
tool_call: tool_search {'query': 'echo'}
tool_call: mcp__echo__echo {'text': 'hello'}
final: hello
```

如果模型第一轮直接调用 `mcp__echo__echo`，provider 看不到这个 schema，正常不会成功。它应该先 `tool_search`。

## v5：端到端 2.0，git/sqlite 都从 MCP 来

最后证明一件事：工具池可以换，harness 不用改。

### 5.1 SQLite MCP server

准备一个最小 sqlite server：

```bash
mkdir -p examples/mcp-sqlite-server
cat > examples/mcp-sqlite-server/server.py <<'EOF'
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).with_name("demo.db")


def ensure_db() -> None:
    conn = sqlite3.connect(DB)
    conn.execute("create table if not exists notes(id integer primary key, title text)")
    conn.execute("insert or ignore into notes(id, title) values(1, 'hello from sqlite mcp')")
    conn.commit()
    conn.close()


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(req_id, text: str) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}})


ensure_db()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "sqlite", "version": "0.1.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{
            "name": "query",
            "description": "Run a read-only SQL query against the demo database.",
            "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
            "annotations": {"readOnlyHint": True}
        }]}})
    elif method == "tools/call":
        sql = msg.get("params", {}).get("arguments", {}).get("sql", "")
        if not sql.strip().lower().startswith("select"):
            result(req_id, "error: only SELECT is allowed")
            continue
        conn = sqlite3.connect(DB)
        rows = conn.execute(sql).fetchall()
        conn.close()
        result(req_id, "\n".join(str(row) for row in rows))
EOF
```

上面这段故意只允许 `SELECT`，因为 Day 14 不是数据库权限系统教程。

### 5.2 `.mcp.json` 接 sqlite 和 git

```json
{
  "mcpServers": {
    "sqlite": {
      "command": "python",
      "args": ["examples/mcp-sqlite-server/server.py"],
      "env": {}
    },
    "git": {
      "command": "uvx",
      "args": ["mcp-server-git"],
      "env": {}
    }
  }
}
```

git MCP 的工具名要以实际 `tools/list` 为准。你可能会看到类似：

```txt
mcp__git__git_status
mcp__git__git_diff
```

不要凭记忆写死，先看启动日志或让模型 `tool_search("git diff")`。

### 5.3 跑端到端

```bash
$ uv run agent-code
[mcp:sqlite] connected, tools=['query']
[mcp:git] connected, tools=[...]

> 先 tool_search 搜 sqlite，然后查 notes 表
tool_call: tool_search {'query': 'sqlite'}
tool_call: mcp__sqlite__query {'sql': 'select * from notes'}
final: notes 表里有一条记录：...

> 先 tool_search 搜 git diff，然后用 git MCP 看当前 diff
tool_call: tool_search {'query': 'git diff'}
tool_call: mcp__git__...
final: ...
```

这就是 Day 14 的最终论点：内置工具不再是边界。换工具池，只改 `.mcp.json`。

## 收尾：今天改了哪些文件

今天新增六个模块：

```txt
agent_code/mcp/protocol.py
agent_code/mcp/client.py
agent_code/mcp/config.py
agent_code/mcp/registry.py
agent_code/tool_pool.py
agent_code/tools/tool_search.py
```

今天改了五个已有文件：

```txt
agent_code/cli.py        启动 connect MCP，退出 shutdown
agent_code/agent.py      provider tools 改走 ToolPool visible_registry
agent_code/tools.py      注册 tool_search
agent_code/runtime.py    deferred/discovered/tool_pool 状态
agent_code/slash.py      /context 显示 inline/deferred 工具数
```

教程里还让你创建：

```txt
examples/mcp-echo-server/server.py
examples/mcp-sqlite-server/server.py
.mcp.json
```

## 手动 trace 一遍

### 路径一：MCP 连接

```txt
1. CLI 读取 .mcp.json。
2. 对每个 server 启动子进程。
3. 发送 initialize。
4. 发送 initialized notification。
5. 发送 tools/list。
6. 把 server 工具转换成 mcp__server__tool。
```

### 路径二：MCP 工具调用

```txt
1. 模型发 tool_use: mcp__echo__echo。
2. registry 解析出 server=echo, raw_tool=echo。
3. client 发送 tools/call 给 echo server。
4. server 返回 content。
5. harness 包成普通 tool_result 回填模型。
```

### 路径三：ToolSearch

```txt
1. MCP 工具默认 deferred，只在 prompt 里露出名字列表。
2. 模型调用 tool_search("sqlite")。
3. harness 把 mcp__sqlite__query 加入 discovered_tool_names。
4. 下一轮 provider tools 里包含 mcp__sqlite__query 的完整 schema。
5. 模型调用 query。
```

## 今天有了什么

- **MCP stdio client**：最小 JSON-RPC 协议跑通。
- **动态工具注入**：MCP tool 变成 `mcp__server__tool`。
- **配置驱动工具池**：`.mcp.json` 决定项目有哪些外部工具。
- **ToolSearch**：MCP 工具默认 deferred，用到时再解锁 schema。
- **端到端 2.0**：git/sqlite 能力从 MCP server 进来，不再写死在 `tools.py`。

## 常见问题

### MCP server 启动后没响应

先手动跑：

```bash
python examples/mcp-echo-server/server.py
```

确认它不会主动打印非 JSON 日志到 stdout。stdio MCP 的 stdout 必须留给 JSON-RPC；日志应该写 stderr。

### `.mcp.json` 配了但没加载

确认你启动 `agent-code` 的 cwd 就是 `.mcp.json` 所在项目根。教学版只查当前 cwd，不向父目录递归查。

### 为什么 MCP 工具默认 deferred？

MCP server 数量没有上限。全部 schema 常驻会把代码上下文挤掉。deferred 让模型先看名字，需要时再 `tool_search`。

### `tool_search` 能不能也 deferred？

不能。它是打开 deferred 工具库的钥匙。如果它自己也被藏起来，模型就没有入口发现其它工具。

### 为什么不做 OAuth / HTTP / elicitation？

那些是生产连接层。Day 14 先讲工具协议闭环：stdio、tools/list、tools/call、ToolSearch。授权和表单交互可以放扩展篇。

## 课后挑战

1. 支持从父目录向上查找 `.mcp.json`。
2. 给 MCP server 增加启动超时和健康检查。
3. 把 `resources/list` 接成只读资源工具。
4. 给 `tool_search` 加简单 BM25 排序，而不是关键词包含。
5. 给 MCP 工具加权限：readOnlyHint 自动 allow，destructiveHint 自动 ask。

## 思考题

1. **为什么模型看到的是 `mcp__server__tool`，但 server 收到的是原始 tool name？**
2. **`.mcp.json` 放项目里，比用户全局配置多解决了什么问题？**
3. **MCP 默认 deferred，内置工具默认 inline，这个差异背后的上下文预算逻辑是什么？**
4. **如果没有 ToolSearch，接入 100 个 MCP 工具会发生什么？**

## 下一步

到这里，14 天主线完成了。

你已经从一个回声 CLI，一步步搭出一个教学版代码 Agent harness：模型 provider、工具调用、文件和 Web 工具、安全编辑、bash 权限、session/memory、slash/hooks/cron、交互 shell、Plan Mode、skills、subagents、context/cost、coordinator、worktree、MCP 和 ToolSearch。

它不是完整复刻任何生产 CLI，但它覆盖了大部分可教学的核心骨架。真正重要的是：你现在知道一个大模型是怎么被 harness 变成“能读代码、改文件、跑命令、管理上下文、接外部工具”的代码 Agent。
