# Day 11：Context + Cost，上下文终于归 harness 管

Day 10 以后，主 Agent 已经能把复杂子任务委派给 subagent。主会话轻了很多，但另一个问题变得更明显：长会话总会爆上下文。

前面我们在 Day 6 做过一个很粗的 compact：`messages` 超过 40 条就压一下。它能救急，但解释不了这几个问题：

- 到底是谁把上下文撑大的？
- 工具结果太长时，是删掉，还是留预览？
- compact 前能不能先备份？
- prompt 太长报错时，harness 能不能自己压缩后重试？

今天让 harness 接管上下文和成本。跑完之后你会看到：

- `/cost` 能显示本轮 token / 美元成本，并按 model、tool 粗略归因。
- `/context` 能把上下文拆成 `Pinned / Working / Compressed` 三层。
- micro compact 只替换旧 `tool_result` 正文，不破坏 `tool_use_id` 配对。
- auto compact 按 token/USD 阈值触发，压缩前备份 transcript。
- `/compact --dry-run` 先预览，`/compact --apply` 再执行。
- 大工具结果落到 `.agent/tool-results/`，上下文里只留 preview 和路径。
- 429/529、prompt 太长这类错误有一个最小 recovery 流程。

代码约 900 行，新增约 520 行。Day 11 是重型天，版本会多一点，但每一版都能单独验证。

今天分七段：

1. v0 做 `CostTracker` 和 `/cost`。
2. v1 做三层 `/context`。
3. v2 做 micro compact。
4. v3 做 auto compact + transcript 备份。
5. v4 做 `compact()` 工具和 `/compact --dry-run|--apply`。
6. 收尾 a 做工具结果预算和溢出落盘。
7. 收尾 b 做最小 recovery。

## 起手：今天的起点

Day 10 的 `agent-code` 已经有这些东西：

```txt
agent.py        run_agent / build_system_prompt / tool loop
model.py        AnthropicProvider / ModelResponse
session.py      JSONL session
tools.py        ToolRegistry + file/bash/web/skill/subagent 工具
compact_basic.py  Day 6 的简化 compact
slash.py        /context /compact 目前还是轻量占位
```

今天不改工具能力本身，改的是“工具和模型调用产生的信息怎么被计量、压缩、备份和恢复”。

先把今天新增文件列出来：

```txt
agent_code/cost_prices.py
agent_code/cost.py
agent_code/context.py
agent_code/transcript.py
agent_code/token_budget.py
agent_code/compactor.py
agent_code/recovery.py
agent_code/tool_results.py
```

它们的职责要分清：

- `cost.py` 只管 usage 计费和归因。
- `context.py` 只管 Pinned / Working / Compressed 分类和估算。
- `compactor.py` 只管 micro / auto / manual compact。
- `tool_results.py` 只管大结果落盘和 preview。
- `recovery.py` 只管 provider 调用失败后怎么补救。

## v0：先把成本看见

auto compact 不能靠感觉。第一步是让每次模型调用都有 usage，能累计到 session 里。

### 0.1 `ModelResponse` 加 usage

打开 `agent_code/model.py`，新增一个 dataclass：

```python
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
```

然后给 `ModelResponse` 加字段：

```python
@dataclass
class ModelResponse:
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    assistant_content: list[dict[str, Any]] | None = None
    stop_reason: str = "end_turn"
    usage: Usage | None = None
```

`MockProvider` 可以不返回 usage。真实 provider 里，在 `response = self.client.messages.create(...)` 后解析：

```python
usage = Usage(
    input_tokens=getattr(response.usage, "input_tokens", 0),
    output_tokens=getattr(response.usage, "output_tokens", 0),
    cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
    cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0),
)
```

最后返回 `ModelResponse(..., usage=usage)`。

如果某个兼容 endpoint 没有 usage，`usage=None`。后面 `CostTracker` 会退回 `chars/4` 估算。

### 0.2 新增价格表

新建 `agent_code/cost_prices.py`：

```python
from __future__ import annotations


# 单位：每 100 万 token 美元。教学版只放常见模型，未知模型按 deepseek 默认价估算。
MODEL_PRICES_USD_PER_MTOKENS: dict[str, tuple[float, float]] = {
    "deepseek-v4-pro": (2.0, 8.0),
    "deepseek-v4-flash": (0.27, 1.10),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
}


def price_for_model(model: str) -> tuple[float, float]:
    if model in MODEL_PRICES_USD_PER_MTOKENS:
        return MODEL_PRICES_USD_PER_MTOKENS[model]
    if "flash" in model:
        return MODEL_PRICES_USD_PER_MTOKENS["deepseek-v4-flash"]
    return MODEL_PRICES_USD_PER_MTOKENS["deepseek-v4-pro"]
```

这不是账单级精确计费，只是让你看到趋势：哪个模型贵、哪类工具结果让下一轮变贵。

### 0.3 新增 `agent_code/cost.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field

from .cost_prices import price_for_model
from .model import Usage


@dataclass
class CostBucket:
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0

    def add(self, input_tokens: int, output_tokens: int, usd: float) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.usd += usd


@dataclass
class CostTracker:
    total: CostBucket = field(default_factory=CostBucket)
    by_model: dict[str, CostBucket] = field(default_factory=dict)
    by_tool: dict[str, CostBucket] = field(default_factory=dict)

    def record(
        self,
        model: str,
        usage: Usage | None,
        fallback_chars: int,
        previous_tools: list[str],
    ) -> None:
        if usage is None:
            input_tokens = max(1, fallback_chars // 4)
            output_tokens = 0
        else:
            input_tokens = usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
            output_tokens = usage.output_tokens

        in_price, out_price = price_for_model(model)
        usd = (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price

        self.total.add(input_tokens, output_tokens, usd)
        self.by_model.setdefault(model, CostBucket()).add(input_tokens, output_tokens, usd)

        names = previous_tools or ["_initial"]
        share_input = input_tokens // len(names)
        share_output = output_tokens // len(names)
        share_usd = usd / len(names)
        for name in names:
            self.by_tool.setdefault(name, CostBucket()).add(share_input, share_output, share_usd)

    def render(self) -> str:
        lines = [
            f"total: {self.total.input_tokens} input / {self.total.output_tokens} output / ${self.total.usd:.4f}",
            "",
            "by model:",
        ]
        for model, bucket in sorted(self.by_model.items()):
            lines.append(f"  {model}: {bucket.input_tokens}+{bucket.output_tokens} tokens / ${bucket.usd:.4f}")
        lines.append("")
        lines.append("by tool (rough API-round attribution):")
        for tool, bucket in sorted(self.by_tool.items(), key=lambda item: item[1].usd, reverse=True):
            lines.append(f"  {tool}: {bucket.input_tokens}+{bucket.output_tokens} tokens / ${bucket.usd:.4f}")
        return "\n".join(lines)
```

`by_tool` 是粗归因。规则是：一次模型调用的成本，平摊给上一轮所有 `tool_use`。因为工具结果是在下一次模型调用时才真正进入上下文。

### 0.4 `RuntimeState` 挂上 cost

打开 `agent_code/runtime.py`，在 `RuntimeState` 里新增：

```python
    cost_tracker: "CostTracker | None" = None
```

为了避免循环 import，可以在文件顶部加：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cost import CostTracker
```

然后在 `cli.py` 创建 `RuntimeState` 后初始化：

```python
from .cost import CostTracker

state.cost_tracker = CostTracker()
```

one-shot 和交互模式两处都要做。

### 0.5 `run_agent()` 记录 usage

打开 `agent_code/agent.py`，在 loop 外加：

```python
last_round_tool_names: list[str] = []
```

每次 provider 返回后立刻记录：

```python
if state.cost_tracker is not None:
    fallback_chars = sum(len(str(m.get("content", ""))) for m in messages)
    state.cost_tracker.record(
        model=state.model,
        usage=response.usage,
        fallback_chars=fallback_chars,
        previous_tools=last_round_tool_names,
    )
```

当这一轮有工具调用时，更新：

```python
last_round_tool_names = [call.name for call in response.tool_calls or []]
```

当没有工具调用、准备 final 时，可以清空：

```python
last_round_tool_names = []
```

### 0.6 `/cost`

打开 `agent_code/slash.py`，新增：

```python
def _cmd_cost(_args: list[str], ctx: SlashContext) -> SlashResult:
    if ctx.state is None or ctx.state.cost_tracker is None:
        return SlashResult(handled=True, message="cost: no tracker for this run")
    return SlashResult(handled=True, message=ctx.state.cost_tracker.render())
```

底部注册：

```python
register("cost", "显示 token / USD 成本估算", _cmd_cost)
```

跑一下：

```bash
$ uv run agent-code
> 今天几号？请用 system_date 工具回答
tool_call: system_date {}
final: ...
> /cost
total: 1234 input / 120 output / $0.0034

by model:
  deepseek-v4-pro: 1234+120 tokens / $0.0034

by tool (rough API-round attribution):
  _initial: ...
  system_date: ...
```

数字会不一样。只要能看到 total、by model、by tool，v0 就通了。

## v1：把上下文拆成三层

现在有成本了，下一步是把上下文分层。我们不再只说“messages 太多”，而是问：哪些必须 pinned？哪些是最近 working？哪些可以 compressed？

### 1.1 新增 `agent_code/context.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def estimate_tokens(value: object) -> int:
    return max(1, len(str(value)) // 4)


@dataclass
class ContextLayer:
    name: str
    tokens: int
    items: int


@dataclass
class ContextReport:
    pinned: ContextLayer
    working: ContextLayer
    compressed: ContextLayer

    @property
    def total_tokens(self) -> int:
        return self.pinned.tokens + self.working.tokens + self.compressed.tokens

    def render(self, cost_text: str = "") -> str:
        lines = [
            f"Pinned:     {self.pinned.tokens} tokens / {self.pinned.items} items",
            f"Working:    {self.working.tokens} tokens / {self.working.items} items",
            f"Compressed: {self.compressed.tokens} tokens / {self.compressed.items} items",
            f"Total:      {self.total_tokens} tokens",
        ]
        if cost_text:
            lines.extend(["", cost_text])
        return "\n".join(lines)


def analyze_context(system_prompt: str, messages: list[dict[str, Any]], todo_count: int = 0) -> ContextReport:
    pinned_tokens = estimate_tokens(system_prompt) + estimate_tokens(todo_count)
    working_messages = messages[-12:]
    older_messages = messages[:-12]

    compressed_items = [
        msg for msg in older_messages
        if "<compacted-history>" in str(msg.get("content", "")) or "[Previous:" in str(msg.get("content", ""))
    ]
    compressed_tokens = sum(estimate_tokens(msg) for msg in compressed_items)
    working_tokens = sum(estimate_tokens(msg) for msg in working_messages)

    return ContextReport(
        pinned=ContextLayer("Pinned", pinned_tokens, 1 + int(todo_count > 0)),
        working=ContextLayer("Working", working_tokens, len(working_messages)),
        compressed=ContextLayer("Compressed", compressed_tokens, len(compressed_items)),
    )
```

这个分类是教学版的。它的价值不是 tokenizer 精确，而是让你知道：系统规则、项目记忆、当前 todo 属于 pinned；最近消息属于 working；摘要和旧工具占位属于 compressed。

### 1.2 `/context` 增强

`/context` 需要能看到当前 session messages。最简单做法是在 `RuntimeState` 里挂一份最近 messages：

```python
    last_messages: list[dict] = field(default_factory=list)
    last_system_prompt: str = ""
```

`run_agent()` 每次进入 loop 前保存：

```python
state.last_system_prompt = system_prompt or ""
```

每次 messages 改动后更新：

```python
state.last_messages = messages
```

然后改 `slash.py` 的 `_cmd_context`：

```python
def _cmd_context(_args: list[str], ctx: SlashContext) -> SlashResult:
    if ctx.state is None:
        session = ctx.session_id or "(none)"
        return SlashResult(handled=True, message=f"cwd: {ctx.cwd}\nsession: {session}")

    from .context import analyze_context

    report = analyze_context(
        ctx.state.last_system_prompt,
        ctx.state.last_messages,
        todo_count=len(ctx.state.todo_store),
    )
    cost_line = ""
    if ctx.state.cost_tracker is not None:
        cost_line = f"Cost: ${ctx.state.cost_tracker.total.usd:.4f}"
    return SlashResult(handled=True, message=report.render(cost_line))
```

跑一下：

```bash
> /context
Pinned:     820 tokens / 1 items
Working:    2100 tokens / 8 items
Compressed: 0 tokens / 0 items
Total:      2920 tokens

Cost: $0.0041
```

到这里，上下文第一次变成了可观察对象。

## v2：micro compact 只压旧工具结果

长会话里最肥的通常不是用户问题，而是工具 observation：`grep` 一扫几十行、`bash` 一跑几千字、`web_fetch` 一抓整篇文档。

micro compact 的原则是：不删消息、不破坏工具配对，只把较老的工具结果正文替换成占位。

### 2.1 在 `compactor.py` 里写 micro

新建 `agent_code/compactor.py`：

```python
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _tool_name_by_id(messages: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names[str(block.get("id"))] = str(block.get("name"))
    return names


def micro_compact(messages: list[dict[str, Any]], keep_recent: int = 12) -> list[dict[str, Any]]:
    if len(messages) <= keep_recent:
        return messages

    result = deepcopy(messages)
    tool_names = _tool_name_by_id(result)
    cutoff = max(0, len(result) - keep_recent)

    for msg in result[:cutoff]:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id", ""))
            tool_name = tool_names.get(tool_use_id, "tool")
            block["content"] = f"[Previous: used {tool_name}]"
    return result
```

注意这里没有删除任何 `tool_result` block，也没有改 `tool_use_id`。Anthropic Messages API 需要 `tool_use` 和 `tool_result` 配对，micro compact 不能破坏这条协议。

### 2.2 `run_agent()` 里先 micro

在 `agent.py` 的 loop 顶部，把 Day 6 的：

```python
if len(messages) > 40:
    messages = compact(messages, keep=8)
```

先替换成：

```python
from .compactor import micro_compact

messages = micro_compact(messages, keep_recent=12)
```

这个版本只做轻压，不做 LLM 摘要。

跑一个本地验证：

```bash
$ uv run python - <<'PY'
from agent_code.compactor import micro_compact

messages = [
  {"role":"assistant","content":[{"type":"tool_use","id":"u1","name":"grep","input":{}}]},
  {"role":"user","content":[{"type":"tool_result","tool_use_id":"u1","content":"x" * 5000}]},
] + [{"role":"user","content":"keep"} for _ in range(12)]

out = micro_compact(messages, keep_recent=12)
print(out[1]["content"][0]["content"])
print(out[1]["content"][0]["tool_use_id"])
PY
[Previous: used grep]
u1
```

## v3：auto compact 要先备份

micro compact 只适合旧工具结果。会话继续变长时，还是需要把旧对话压成摘要。这个动作风险更大，所以先备份。

### 3.1 transcript 备份

新建 `agent_code/transcript.py`：

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def backup_transcript(cwd: Path, messages: list[dict[str, Any]]) -> Path:
    directory = cwd / ".agent" / "transcripts"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = directory / f"transcript_{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path
```

### 3.2 token / USD 阈值

新建 `agent_code/token_budget.py`：

```python
from __future__ import annotations


class TokenBudget:
    def __init__(self, max_tokens: int = 60_000, max_usd: float = 0.25) -> None:
        self.max_tokens = max_tokens
        self.max_usd = max_usd

    def should_compact(self, tokens: int, usd: float) -> bool:
        return tokens >= self.max_tokens or usd >= self.max_usd
```

教学版阈值可以偏低，方便你制造 demo。真实项目可以把它放到配置文件。

### 3.3 `auto_compact()`

在 `compactor.py` 里追加：

```python
from pathlib import Path

from .context import analyze_context
from .model import ModelProvider
from .transcript import backup_transcript


def _summary_prompt(messages: list[dict[str, Any]]) -> str:
    return (
        "Summarize this conversation for continuing a coding-agent session. "
        "Keep user goals, decisions, file changes, open questions, and tool outcomes. "
        "Do not include irrelevant raw logs.\n\n"
        f"{messages}"
    )


def auto_compact(
    cwd: Path,
    messages: list[dict[str, Any]],
    provider: ModelProvider,
    system_prompt: str,
    threshold_tokens: int,
) -> tuple[list[dict[str, Any]], str | None]:
    report = analyze_context(system_prompt, messages)
    if report.total_tokens < threshold_tokens:
        return messages, None

    backup_path = backup_transcript(cwd, messages)
    old = messages[:-8]
    recent = messages[-8:]
    response = provider.complete(
        [{"role": "user", "content": _summary_prompt(old)}],
        tools=[],
        system="You compact coding-agent transcripts into short continuation summaries.",
    )
    summary = response.text or "(compact summary unavailable)"
    compacted = [
        {
            "role": "user",
            "content": f"<compacted-history backup=\"{backup_path}\">\n{summary}\n</compacted-history>",
        }
    ]
    return compacted + recent, str(backup_path)
```

然后在 `run_agent()` 里，provider call 前加：

```python
from .compactor import auto_compact, micro_compact

messages = micro_compact(messages, keep_recent=12)
messages, backup_path = auto_compact(
    resolved_cwd,
    messages,
    provider,
    system_prompt or "",
    threshold_tokens=60_000,
)
if backup_path:
    console.print(f"[dim]compacted: backup={backup_path}[/dim]")
```

跑验证时可以把阈值临时改成 500，造一个长 messages，确认 `.agent/transcripts/transcript_*.jsonl` 出现。

## v4：`compact()` 工具和 `/compact`

auto compact 是 harness 自己触发。手动 compact 有两个入口：

- 用户：`/compact --dry-run` / `/compact --apply`
- 模型：`compact()`

### 4.1 `manual_compact_plan()`

在 `compactor.py` 里追加：

```python
def manual_compact_plan(messages: list[dict[str, Any]], system_prompt: str) -> str:
    report = analyze_context(system_prompt, messages)
    return "\n".join([
        "compact dry-run:",
        f"- pinned stays: {report.pinned.tokens} tokens",
        "- working keeps the latest 8-12 messages",
        "- older tool_result content may be replaced by [Previous: used <tool>]",
        "- old conversation will be summarized into <compacted-history>",
        "- a transcript backup will be written before apply",
    ])
```

### 4.2 `compact()` 工具

打开 `tools.py`，加一个函数：

```python
def compact_tool(args: dict[str, Any], ctx: ToolContext) -> str:
    """模型主动请求 compact。真正压缩在 agent.py turn boundary 里处理。"""
    state = ctx.runtime_state
    if state is None:
        return "error: no runtime state"
    state.compact_requested = True
    return "compact requested; the harness will compact at the next safe boundary"
```

`RuntimeState` 里新增：

```python
    compact_requested: bool = False
```

注册工具：

```python
registry.register(
    Tool(
        name="compact",
        description="Request the harness to compact old context at the next safe boundary.",
        run=compact_tool,
        parameters={"type": "object", "properties": {}, "required": []},
        is_read_only=False,
    )
)
```

在 `run_agent()` loop 顶部检查：

```python
if state.compact_requested:
    state.compact_requested = False
    messages, backup_path = auto_compact(resolved_cwd, messages, provider, system_prompt or "", threshold_tokens=1)
    if backup_path:
        console.print(f"[dim]manual compact: backup={backup_path}[/dim]")
```

### 4.3 `/compact`

打开 `slash.py`，把 `_cmd_compact` 改成：

```python
def _cmd_compact(args: list[str], ctx: SlashContext) -> SlashResult:
    if ctx.state is None:
        return SlashResult(handled=True, message="compact 需要交互 shell")

    from .compactor import manual_compact_plan

    if not args or args[0] == "--dry-run":
        return SlashResult(
            handled=True,
            message=manual_compact_plan(ctx.state.last_messages, ctx.state.last_system_prompt),
        )

    if args[0] == "--apply":
        ctx.state.compact_requested = True
        return SlashResult(handled=True, message="compact scheduled for the next safe boundary")

    return SlashResult(handled=True, message="用法: /compact --dry-run | /compact --apply")
```

跑验证：

```bash
> /compact --dry-run
compact dry-run:
- pinned stays: ...
- working keeps ...
...
> /compact --apply
compact scheduled for the next safe boundary
```

## 收尾 a：工具结果预算 + 溢出落盘

一轮 `bash` 或 `web_fetch` 可能返回几万字。上下文里不应该塞全文，但全文也不能丢。

### a.1 新增 `agent_code/tool_results.py`

```python
from __future__ import annotations

from pathlib import Path


MAX_TOOL_RESULT_CHARS = 8_000
PREVIEW_CHARS = 1_000


def persist_if_large(cwd: Path, tool_call_id: str, content: str) -> str:
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content

    directory = cwd / ".agent" / "tool-results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{tool_call_id}.txt"
    path.write_text(content, encoding="utf-8")
    head = content[:PREVIEW_CHARS]
    tail = content[-PREVIEW_CHARS:]
    return (
        f"[large tool result stored]\n"
        f"Full output: {path}\n\n"
        f"--- head ---\n{head}\n\n"
        f"--- tail ---\n{tail}"
    )
```

### a.2 接进 `execute_one_tool_call()`

在 `agent.py` 里，`result = tools.run(call, ctx)` 后面加：

```python
from .tool_results import persist_if_large

result.content = persist_if_large(ctx.cwd, result.tool_call_id, result.content)
```

这样模型仍然拿到合法 `tool_result`，但上下文只有预览和路径。如果它需要全文，可以再 `read_file` 那个路径。

跑验证：

```bash
$ uv run python - <<'PY'
from pathlib import Path
from agent_code.tool_results import persist_if_large

text = "x" * 9000
out = persist_if_large(Path.cwd(), "call_big", text)
print("Full output:" in out)
print((Path.cwd() / ".agent" / "tool-results" / "call_big.txt").exists())
PY
True
True
```

`/compact --apply` 后可以再做孤儿文件清理：扫描 session JSONL 里还引用哪些 `tool_call_id`，删除 `.agent/tool-results/` 下没被引用的文件。第一版可以先保留文件，课后挑战再做精确清理。

## 收尾 b：最小 recovery

最后补一个很小的恢复层。它不做生产级 API SDK，只处理三类常见问题：

- 429/529：退避后重试。
- prompt too long：触发 compact 后重试一次。
- max_tokens 不够：提示模型续写或提高输出上限。

新建 `agent_code/recovery.py`：

```python
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def is_capacity_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "529" in text or "overloaded" in text or "rate limit" in text


def is_prompt_too_long(exc: Exception) -> bool:
    text = str(exc).lower()
    return "prompt" in text and ("too long" in text or "context" in text)


def with_recovery(call: Callable[[], T], on_prompt_too_long: Callable[[], None] | None = None) -> T:
    for attempt in range(3):
        try:
            return call()
        except Exception as exc:
            if is_capacity_error(exc) and attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
                continue
            if is_prompt_too_long(exc) and on_prompt_too_long is not None:
                on_prompt_too_long()
                on_prompt_too_long = None
                continue
            raise
    return call()
```

在 `agent.py` 里把 provider call 包起来：

```python
from .recovery import with_recovery

response = with_recovery(
    lambda: provider.complete(messages, tools=visible_tools.list(), system=system_prompt),
    on_prompt_too_long=lambda: setattr(state, "compact_requested", True),
)
```

这只是最小闭环。OAuth、provider fallback、prompt cache break、无人值守长 retry 都不进主线。

## 收尾：今天改了哪些文件

今天新增八个文件：

```txt
agent_code/cost_prices.py
agent_code/cost.py
agent_code/context.py
agent_code/transcript.py
agent_code/token_budget.py
agent_code/compactor.py
agent_code/tool_results.py
agent_code/recovery.py
```

今天改了六个已有文件：

```txt
agent_code/model.py       ModelResponse 增 usage
agent_code/runtime.py     挂 cost/context/compact 状态
agent_code/agent.py       记录 usage、micro/auto compact、overflow、recovery
agent_code/tools.py       新增 compact 工具
agent_code/slash.py       /cost、/context、/compact
agent_code/cli.py         初始化 CostTracker
```

## 手动 trace 一遍

### 路径一：一次带工具调用的成本归因

```txt
1. 用户问“今天几号”。
2. 第一轮模型看到 system_date，发 tool_use。
3. harness 执行 system_date。
4. 第二轮模型读取 tool_result，返回 final。
5. CostTracker 把第二轮 usage 平摊给上一轮工具 system_date。
6. /cost 显示 by_tool: system_date。
```

### 路径二：micro compact

```txt
1. 老消息里有 tool_result 大文本。
2. micro_compact 找到对应 tool_use_id。
3. 用 tool_use_id 映射回工具名。
4. 把 content 替换成 [Previous: used grep]。
5. tool_result block 和 tool_use_id 仍然保留。
```

### 路径三：大工具结果落盘

```txt
1. bash 返回 9000 字。
2. persist_if_large 写 .agent/tool-results/<tool_call_id>.txt。
3. tool_result.content 变成 head/tail preview + 文件路径。
4. 模型需要全文时，再调用 read_file 读取该路径。
```

## 今天有了什么

- **成本可见**：`/cost` 能看到 total、by model、by tool 的粗估。
- **三层上下文**：`/context` 把上下文拆成 Pinned / Working / Compressed。
- **micro compact**：旧工具结果变占位，不破坏 tool_use/tool_result 配对。
- **auto/manual compact**：阈值触发或用户触发，压缩前写 transcript 备份。
- **工具结果预算**：大结果落盘，上下文里只留预览。
- **recovery**：服务忙和 prompt 太长有最小补救路径。

## 常见问题

### `/cost` 的 by-tool 准吗？

它是趋势工具，不是账单。一次模型调用的成本按上一轮工具平摊，所以只能说明“哪些工具结果大概率让上下文变贵”。

### 为什么 micro compact 不删消息？

因为真实工具协议要求 `tool_use` 和 `tool_result` 配对。删掉旧 `tool_result` 可能让下一轮请求直接报错。

### compact 后是不是历史没了？

主上下文里只剩摘要，但 compact 前已经备份到：

```txt
.agent/transcripts/transcript_<timestamp>.jsonl
```

### 大工具结果为什么不直接截断？

直接截断会丢信息。落盘后，上下文短了，全文还可以通过 `read_file` 找回来。

## 课后挑战

1. 把价格表移到 `.agent/settings.json`。
2. 给 `/context` 加 inline/deferred 工具数量，为 Day 14 铺路。
3. `/compact --apply` 后清理未被 session 引用的 `.agent/tool-results/*.txt`。
4. 给 `web_fetch` 和 `bash` 分别设置不同的结果上限。
5. 用真实 tokenizer 替换 `chars/4` 估算。

## 思考题

1. **为什么 by-tool cost 要归到“上一轮工具”，而不是当前这一轮？** 提示：工具结果什么时候进入模型上下文？
2. **Pinned / Working / Compressed 三层里，哪一层最危险，不能随便压？**
3. **micro compact 为什么必须保留 `tool_use_id`？**
4. **工具结果落盘后，模型还能如何拿到全文？这对上下文预算有什么好处？**

## 下一天

今天 harness 开始管理上下文和成本。下一天我们让多个有身份的 Agent 协作：建 team、发消息、调度 teammate，再把后台任务统一成可查、可停、可回流的 task runtime。
