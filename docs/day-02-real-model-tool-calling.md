# Day 2：接入真实模型和 Tool Calling

Day 1 我们用 `MockProvider` 跑通了 Agent Loop，但那个"模型"是假的——你说什么它都只回 echo。今天把假模型换掉，接上真实模型。

跑完之后你会看到什么？模型自己不知道今天几号，但它会主动请求 `system_date` 工具，harness 执行工具拿到时间，把结果交回模型，模型再给出最终回答。这一个来回，就是真实 tool calling 的闭环。

代码约 260 行，新增约 130 行。

还是在 Day 1 的 `agent-code` 项目里继续改。仓库里的 `packages/day-*` 是参考答案快照，不是让你每天新建项目。

## 起手：今天的起点

Day 1 我们搭好了 CLI、`MockProvider`、`ModelResponse / ToolCall / ToolResult`、`echo` 工具和最小 Agent Loop。今天不动这些结构，只换"脑子"——把 `MockProvider` 换成真实模型，把内部简化消息格式换成 Anthropic 公开协议。

我们学的是 Anthropic Messages API 的 `tool_use` / `tool_result` 协议。但为了让每个人都能便宜跑通，默认模型服务用 DeepSeek 的 Anthropic-compatible endpoint。代码写的是 Anthropic 协议，只是把 `base_url` 指到 DeepSeek。你写出来的 harness 不绑定任何一家服务商。

先装 Anthropic Python SDK：

```bash
uv add anthropic
```

如果你的终端走了 SOCKS 代理（Clash、Surge、Shadowrocket 之类），还要补装 `httpx` 的 socks 支持。Anthropic SDK 底层用 `httpx` 发请求，会自动读 `ALL_PROXY` / `HTTPS_PROXY` 环境变量：

```bash
uv add "httpx[socks]"
```

然后设置 API Key 和 base URL：

```bash
export ANTHROPIC_AUTH_TOKEN="sk-..."
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
```

今天默认模型用 `deepseek-v4-flash`，便宜好跑。收尾阶段你可以通过 `--model` 换成 `deepseek-v4-pro` 或其他支持 Anthropic tool use 协议的模型。

想接官方 Claude？把 `ANTHROPIC_AUTH_TOKEN` 换成 `ANTHROPIC_API_KEY`，并把 `ANTHROPIC_BASE_URL` 显式设成官方 API base URL，或在 Day 2 收尾加好 CLI 参数后传 `--base-url https://api.anthropic.com`。注意：本文代码在 `base_url` 为空时会默认指向 DeepSeek，所以只取消 `ANTHROPIC_BASE_URL` 不会自动回到官方接口。同时把 `--model` 换成你账号可用的 Claude 模型名。harness 代码只认 Anthropic Messages API 形状，不挑服务商。

## v1：先让真实模型说一句话

第一步不传工具，只证明 `agent-code` 能接到真实模型。

打开 `agent_code/model.py`。Day 1 里 `ToolCall`、`ToolResult`、`ModelResponse` 和 `MockProvider` 都在里面，v1 不改它们，只往旁边加一个新 provider。

第一处，文件顶部多 import 三样东西：

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import Anthropic

# ... 下面原来的 ToolCall / ToolResult / ModelResponse 先保留 ...
```

第二处，在 `ModelResponse` 后面加一个 `ModelProvider` 接口。它的作用很简单：只要一个对象有 `complete(messages, tools=None)` 方法，就能被 Agent Loop 当模型用。

```python
@dataclass
class ModelResponse:
    # 一次模型响应可以是最终文本，也可以是工具调用。
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    assistant_content: list[dict[str, Any]] | None = None
    stop_reason: str = "end_turn"


class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
    ) -> ModelResponse:
        ...


# ... 这里继续放 MockProvider ...
```

`Protocol` 是 Python 的鸭子类型接口。只要一个类实现了 `complete()` 方法，就可以当 `ModelProvider` 用，不需要显式继承。用它是为了让 `agent.py` 依赖"能力"而不是依赖某一个具体模型类——下一天换 provider 时 `agent.py` 不用改。

第三处，在 `MockProvider` 旁边加 `AnthropicProvider`。这一版先不处理工具，只把 `messages` 发给模型，把返回的文本拼成 `ModelResponse.text`。

```python
class AnthropicProvider:
    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        max_tokens: int = 1024,
        base_url: str | None = None,
    ) -> None:
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("请先设置 ANTHROPIC_AUTH_TOKEN，例如：export ANTHROPIC_AUTH_TOKEN='sk-...'")

        self.model = model
        self.max_tokens = max_tokens
        # 默认使用DeepSeek的Anthropic-compatible endpoint
        self.base_url = base_url or os.environ.get(
            "ANTHROPIC_BASE_URL",
            "https://api.deepseek.com/anthropic",
        )
        self.client = Anthropic(api_key=api_key, base_url=self.base_url)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
    ) -> ModelResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        text_parts = [block.text for block in response.content if block.type == "text"]
        return ModelResponse(text="\n".join(text_parts) or None, stop_reason=response.stop_reason)
```

再改 `agent_code/cli.py`。Day 1 的 `run_once()` 里这行：

```python
result = run_agent(prompt, MockProvider(), default_tools())
```

替换成：

```python
result = run_agent(prompt, AnthropicProvider(), default_tools())
```

顶部 import 也换掉：

```python
from .model import AnthropicProvider
```

其他 CLI 代码——`render_header()`、`handle_slash()`、`main_command()`——这一版都不用动。

跑一下：

```bash
$ uv run agent-code "你好，用一句话介绍你自己"
Agent Code
cwd: /your/project

final: 你好，我是一个 AI 编程助手，可以帮助你阅读代码、解释问题并协助完成编程任务。
```

输出文字不一定完全一样，正常。关键是它来自真实模型，不是 Day 1 那个固定台词的 `MockProvider`。

这一版模型能聊天了，但它还不知道自己有哪些工具可用。下一版把工具列表递给模型。

## v2：让模型请求 system_date 工具

你可能会想：模型自己不知道现在几点吗？对，不知道。大模型没有实时时钟，它想知道时间就必须请求工具。这就是 tool calling 存在的理由。

Anthropic Messages API 里，工具不是模型自己执行的。模型只会返回一个 `tool_use` content block，意思是"我想调用这个工具，参数是这些"。真正执行工具的是我们的 harness。

这一版改三个文件：

```txt
1. tools.py：给工具补 JSON Schema，并新增 system_date。
2. model.py：把 Tool 转成 Anthropic input_schema，把 tool_use 转成 ToolCall。
3. agent.py：把 tool_result 按 Anthropic 要求放回下一轮 user message。
```

JSON Schema 就是写给模型看的函数说明书：叫什么名字、有哪些参数、参数是字符串还是数字。模型看懂了，才知道该输出什么格式的工具调用。

这里有个重要的协议差异，提前告诉你。Day 1 的 mock 阶段我们用了一个内部简化格式：

```python
{"role": "tool", "tool_call_id": "...", "content": "..."}
```

真实 Anthropic Messages API 要求 `tool_result` 放在下一轮 `user` 消息的 content blocks 里：

```python
{
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "...",
            "content": "...",
        }
    ],
}
```

这不是模仿哪个私有实现，是 Anthropic Messages API 公开的协议形状。Day 1 的简化是为了先跑通 loop，Day 2 必须按真实协议来。

### 2.1 改 tools.py：工具要有 input_schema

打开 `agent_code/tools.py`。Day 1 的 `Tool` 只有 `name / description / run`，v2 给它加上 `parameters`。这个字段稍后会被 provider 翻译成 Anthropic 的 `input_schema`。

顶部 import 从：

```python
from dataclasses import dataclass
from typing import Any, Callable
```

改成：

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
```

然后把 `Tool` dataclass 改成下面这样。其他字段保持原样，只多了 `parameters`：

```python
@dataclass
class Tool:
    name: str
    description: str
    run: ToolFunc
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )
```

接着在 `echo()` 后面新增 `system_date()`。它不需要参数，直接忽略 `args`。

```python
def echo(args: dict[str, Any]) -> str:
    return str(args.get("text", ""))


def system_date(args: dict[str, Any]) -> str:
    # system_date 是模型看不到系统时钟时，需要向 harness 请求的能力。
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
```

`ToolRegistry` 再加一个 `list()` 方法，让 provider 能取出所有工具描述。`run()` 原来的执行逻辑保留不动。

```python
class ToolRegistry:
    def __init__(self) -> None:
        # 注册表是工具名和 Python 函数之间的 harness 边界。
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def run(self, call: ToolCall) -> ToolResult:
        # ... Day 1 的执行逻辑保留不动 ...
```

最后把 `default_tools()` 里的 `echo` 注册改成带参数 schema 的版本，并额外注册 `system_date`：

```python
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
        )
    )
    registry.register(
        Tool(name="system_date", description="Return the current system date and time.", run=system_date)
    )
    return registry
```

### 2.2 改 model.py：解析模型的 tool_use

回到 `agent_code/model.py`。在 `AnthropicProvider` 前面加三个 helper。

第一个 helper 把我们的 `Tool` 翻译成 Anthropic 的工具 schema。注意 Anthropic 字段叫 `input_schema`，不是 OpenAI-compatible API 里的 `parameters`。

```python
def _to_anthropic_tools(tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]
```

第二个 helper 把模型返回的工具输入收窄成 `dict[str, Any]`。正常情况下 `tool_use.input` 就是 dict，这里只是给类型检查一个明确边界。

```python
def _parse_tool_input(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
```

第三个 helper 把 SDK 返回的 content block 转成普通 dict。某些 Anthropic-compatible endpoint 或开启 thinking 的模型会返回 `thinking` / `signature` 这类额外 block；下一轮请求要把上一轮 assistant content blocks 原样放回 `messages`，否则兼容网关可能会因为上下文不完整而报 400。

```python
def _content_block_to_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if hasattr(block, "dict"):
        return block.dict(exclude_none=True)
    data = {"type": block.type}
    for name in ("text", "id", "name", "input", "thinking", "signature"):
        if hasattr(block, name):
            data[name] = getattr(block, name)
    return data
```

然后找到 `class AnthropicProvider` 里的 `complete()` 方法，把整个方法替换成下面这版。不要只改中间几行——v2 同时改了四件事：请求时带上 `tools`，解析普通文本，解析模型返回的 `tool_use`，并保存原始 assistant content blocks。

```python
def complete(
    self,
    messages: list[dict[str, Any]],
    tools: list[Any] | None = None,
) -> ModelResponse:
    # 先准备一次模型请求的基础参数。messages 是 Agent Loop 累积出来的上下文。
    kwargs: dict[str, Any] = {
        "model": self.model,
        "max_tokens": self.max_tokens,
        "messages": messages,
    }

    # 如果 registry 里有工具，就把我们的 Tool 翻译成 Anthropic 的 tools 格式。
    # 这一步只是"告诉模型有哪些工具"，还没有执行任何工具。
    if tools:
        kwargs["tools"] = _to_anthropic_tools(tools)

    response = self.client.messages.create(**kwargs)

    # Claude/DeepSeek 可能同时返回 text block 和 tool_use block。
    # text_parts 收集普通回答；tool_calls 收集"模型想调用工具"的请求。
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    assistant_content: list[dict[str, Any]] = []

    for block in response.content:
        # 原样保存 assistant content，后面 agent.py 会把它放回 messages。
        # 这能保留 thinking / signature 等额外 block，避免下一轮请求丢上下文。
        assistant_content.append(_content_block_to_dict(block))

        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            # provider 只负责把外部协议翻译成我们自己的 ToolCall。
            # 真正执行工具的是 agent.py 里的 Agent Loop。
            tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=_parse_tool_input(block.input),
                )
            )

    return ModelResponse(
        text="\n".join(text_parts) or None,
        tool_calls=tool_calls or None,
        assistant_content=assistant_content or None,
        stop_reason=response.stop_reason or "end_turn",
    )
```

`stop_reason` 可能是 `tool_use` 或 `end_turn`。今天 `agent.py` 主要用 `if not response.tool_calls` 判断是否结束：没有工具调用就是 final，有就继续执行。保留 `stop_reason` 是为了调试时看清模型为什么停，后面做 streaming 也用得上。

### 2.3 改 agent.py：回填 tool_result

最后改 `agent_code/agent.py`。先把顶部 import 改掉：`run_agent()` 不再只接受 `MockProvider`，而是接受任何 `ModelProvider`。

```python
from dataclasses import dataclass
from typing import Any

from .model import ModelProvider, ModelResponse
from .tools import ToolRegistry
```

`AgentResult` 多存一份 `messages`，后面测试可以检查我们有没有把 assistant 的 `tool_use` 和 user 的 `tool_result` 都保存下来：

```python
@dataclass
class AgentResult:
    final: str
    trace: list[str]
    messages: list[dict[str, Any]]
```

然后新增 `_assistant_message()`。它把内部 `ModelResponse` 还原成 Anthropic Messages API 的 assistant content blocks。

```python
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
```

这里先检查 `response.assistant_content`，是为了保留模型原始返回的 content blocks。为什么？因为一些兼容端点会返回 `thinking` / `signature` 这类非 text/tool_use block，下一轮请求必须原样带回去；如果我们自己只重建 `text/tool_use`，就会把这些 block 丢掉，第二次请求可能直接 400。

再新增 `_tool_result_message()`。这就是 Day 1 和 Day 2 最大的协议差异：真实 Anthropic API 要求把工具结果作为下一轮 `user` message 发回去。

```python
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
```

最后把 `run_agent()` 里从 `response = provider.complete(...)` 到 `return AgentResult(...)` 的单轮逻辑，替换成下面这版。它还不是多步 loop，只处理"一次 tool_use → 一次 tool_result → 一次 final"，方便先把协议跑通。

```python
def run_agent(prompt: str, provider: ModelProvider, tools: ToolRegistry) -> AgentResult:
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    trace: list[str] = []

    response = provider.complete(messages, tools=tools.list())
    messages.append(_assistant_message(response))

    for call in response.tool_calls or []:
        trace.append(f"tool_call: {call.name} {call.arguments}")
        result = tools.run(call)
        trace.append(f"observation: {result.content}")
        messages.append(_tool_result_message(result.tool_call_id, result.content, result.is_error))
        response = provider.complete(messages, tools=tools.list())

    final = response.text or ""
    trace.append(f"final: {final}")
    return AgentResult(final=final, trace=trace, messages=messages)
```

跑一下：

```bash
$ uv run agent-code "今天几号？请用 system_date 工具回答"
Agent Code
cwd: /your/project

tool_call: system_date {}
observation: 2026-05-20 14:32:00 CST
final: 今天是 2026 年 5 月 20 日。
```

措辞不一样没关系。只要看到 `tool_call: system_date`、`observation`、`final` 三段，就说明真实 tool calling 闭环通了。

## v3：多步 Agent Loop

v2 已经能处理"模型请求工具 → 执行 → 再问模型"的情况，但它只走一轮。如果模型第一轮调用 `system_date`，第二轮又想调用 `echo`，v2 接不住。

现在把 `agent_code/agent.py` 里的单轮逻辑替换成多步 Agent Loop：

```txt
model -> tool_use -> tool -> tool_result -> model -> ...
```

什么时候停？看两件事：

```txt
1. 这一轮模型没有 tool_calls，说明可以 final 了。
2. step 达到 max_steps，harness 强制停止，避免模型无限调用工具。
```

先改函数签名，给 `run_agent()` 加一个 `max_steps` 参数：

```python
def run_agent(
    prompt: str,
    provider: ModelProvider,
    tools: ToolRegistry,
    max_steps: int = 8,
) -> AgentResult:
    ...
```

然后把 v2 里从 `response = provider.complete(...)` 到 `return AgentResult(...)` 的那一段，整体替换成下面这段循环。`_assistant_message()` 和 `_tool_result_message()` 不用改。

```python
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
        result = tools.run(call)
        trace.append(f"observation: {result.content}")
        messages.append(_tool_result_message(result.tool_call_id, result.content, result.is_error))

final = f"reached max_steps={max_steps}"
trace.append(f"final: {final}")
return AgentResult(final=final, trace=trace, messages=messages)
```

注意顺序：每次拿到模型响应后，先把 assistant message 放进 `messages`。如果这一轮有工具调用，再把每个工具结果作为下一轮 user message 追加进去。下一轮模型请求看到的就是完整上下文。

跑一个故意要求两次工具调用的任务：

```bash
$ uv run agent-code "不要直接回答。请严格按顺序调用两个工具：第一步用 system_date 获取今天日期；第二步用 echo 复述 system_date 的返回值；最后再回答。"
Agent Code
cwd: /your/project

tool_call: system_date {}
observation: 2026-05-20 14:32:00 CST
tool_call: echo {'text': '2026-05-20 14:32:00 CST'}
observation: 2026-05-20 14:32:00 CST
final: 今天是 2026 年 5 月 20 日，echo 工具复述了：2026-05-20 14:32:00 CST。
```

如果模型只调用了一次工具，把 prompt 写得更强硬一点。今天的重点不是让模型每次都按同一句话行动，而是 harness 已经能接住多步工具调用。

到这里，Agent Loop 的完整形态就出来了：它就是在反复问模型"下一步要不要用工具"。模型返回普通文本 → 当最终回答；模型返回 `tool_use` → harness 执行工具 → 把结果交回模型 → 再问一次。只要模型继续要工具，循环就继续；直到模型不再要，或者达到 `max_steps`。

## 收尾：provider 选项和 mock 测试入口

最后把 CLI 边界补齐：

```txt
--provider anthropic | mock
--model deepseek-v4-flash
--base-url https://api.deepseek.com/anthropic
--max-steps 8
```

`--model` 和 `--base-url` 都做成 CLI 选项。今天主线默认用 DeepSeek 的 Anthropic-compatible endpoint，但 provider 代码不写死某一家服务。

`MockProvider` 不再试图模拟 `system_date` 或多步推理。它只保留最小 echo 流程，让测试不依赖网络。真实工具调用能力交给 `AnthropicProvider` 验证。

分两步改。先在 `agent_code/model.py` 末尾加一个 provider 工厂，让 CLI 不直接知道每个 provider 的构造细节：

```python
def create_provider(name: str, model: str, base_url: str | None = None) -> ModelProvider:
    if name == "anthropic":
        return AnthropicProvider(model=model, base_url=base_url)
    if name == "mock":
        return MockProvider()
    raise ValueError(f"unknown provider: {name}")
```

再改 `agent_code/cli.py`。顶部 import 从具体类换成工厂函数：

```python
from .model import create_provider
```

`render_header()` 多打印 provider、model 和 base URL，方便你确认当前跑的是 mock 还是真实模型：

```python
def render_header(cwd: Path, provider: str, model: str, base_url: str | None) -> None:
    console.print("[bold]Agent Code[/bold]")
    console.print(f"[dim]cwd: {cwd}[/dim]")
    console.print(f"[dim]provider: {provider}  model: {model}[/dim]")
    if base_url:
        console.print(f"[dim]base_url: {base_url}[/dim]")
    console.print()
```

`run_once()` 也多接四个参数：`provider_name`、`model`、`base_url`、`max_steps`。原来写死 `AnthropicProvider()` 的地方，换成 `create_provider(provider_name, model, base_url)`。

```python
def run_once(
    prompt: str,
    cwd: Path,
    provider_name: str,
    model: str,
    base_url: str | None,
    max_steps: int,
) -> None:
    render_header(cwd, provider_name, model, base_url)
    provider = create_provider(provider_name, model, base_url)
    result = run_agent(prompt, provider, default_tools(), max_steps=max_steps)
    for line in result.trace:
        console.print(line)
```

最后给 `main_command()` 增加四个 CLI 选项，并把调用 `run_once()` 的地方补齐参数。REPL 模式里的 `render_header()` 也要传同样的 provider/model/base URL。

```python
@app.callback(invoke_without_command=True)
def main_command(
    prompt: str = typer.Argument("", help="Prompt to send to the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", "-C"),
    provider: str = typer.Option("anthropic", "--provider"),
    model: str = typer.Option("deepseek-v4-flash", "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    max_steps: int = typer.Option(8, "--max-steps"),
) -> None:
    resolved_cwd = cwd.resolve()
    text = prompt.strip()

    if text:
        run_once(text, resolved_cwd, provider, model, base_url, max_steps)
        return

    render_header(resolved_cwd, provider, model, base_url)
    # ... 下面的 REPL 循环保留，只把最后的 run_once(...) 参数补齐 ...
```

跑两个验收。先跑真实模型：

```bash
$ uv run agent-code "今天几号？请用 system_date 工具回答"
Agent Code
cwd: /your/project
provider: anthropic  model: deepseek-v4-flash

tool_call: system_date {}
observation: 2026-05-20 14:32:00 CST
final: 今天是 2026 年 5 月 20 日。
```

再跑离线 mock，确认不依赖网络：

```bash
$ uv run agent-code --provider mock "用 echo 工具说 hi"
Agent Code
cwd: /your/project
provider: mock  model: deepseek-v4-flash

tool_call: echo {'text': 'hi'}
observation: hi
final: echo 工具返回：hi
```

## 手动 trace 一遍

输入 `今天几号？请用 system_date 工具回答`，发生了什么：

```txt
1. CLI 解析 provider=anthropic、model=deepseek-v4-flash、base_url 和 max_steps=8。
2. run_agent 创建 messages = [{"role": "user", "content": "..."}]。
3. AnthropicProvider 把 ToolRegistry 里的工具翻译成 Anthropic input_schema。
4. 第一次请求模型，模型返回 stop_reason=tool_use。
5. provider 把 tool_use content block 转成内部 ToolCall(name="system_date", arguments={})。
6. agent 把 assistant tool_use message append 到 messages。
7. ToolRegistry 执行 system_date，得到当前系统时间。
8. agent 把 tool_result 包成下一轮 user message，并 append 到 messages。
9. 第二次请求模型，模型看到工具结果，返回最终文本。
10. CLI 打印 tool_call、observation、final。
```

记住一个关键点：Anthropic 工具调用协议不只是把工具结果扔回去，还要保留"模型刚才请求了哪个工具"这一轮，并且 `tool_result` 必须回到下一轮 `user` message 里。漏掉任何一步都会报错。

## 今天有了什么

- **ModelProvider**：CLI 和 Agent Loop 不直接依赖某一个 provider 类，换模型只需要换工厂参数。
- **AnthropicProvider**：用 Anthropic Messages API 接入真实模型，默认指向 DeepSeek 的 Anthropic-compatible endpoint，代码不绑定服务商。
- **工具描述传递**：`Tool` 不只有 Python 函数，还带 JSON Schema，模型能看懂工具怎么调用。
- **messages 形状修复**：从 Day 1 的内部简化格式切换到 Anthropic 公开的 `tool_use` / `tool_result` 协议。
- **多步 Agent Loop**：用 `max_steps` 限制循环，模型可以连续调用工具但不会无限跑。

## 常见问题

### 报错 `ANTHROPIC_AUTH_TOKEN`

这是最容易踩的坑——每次新开终端窗口都要重新 export。

确认当前终端设置过：

```bash
export ANTHROPIC_AUTH_TOKEN="sk-..."
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
```

然后用同一个终端运行 `uv run agent-code ...`。

### 报错 `Using SOCKS proxy, but the 'socksio' package is not installed`

说明你的终端设了 SOCKS 代理，比如 `ALL_PROXY=socks5://...`。Anthropic SDK 底层的 `httpx` 会自动用这个代理，但默认没装 SOCKS 支持。

补装一次就好：

```bash
uv add "httpx[socks]"
```

如果不想让这次请求走代理，也可以临时取消：

```bash
unset ALL_PROXY HTTPS_PROXY HTTP_PROXY
```

### 为什么类名还叫 `AnthropicProvider`

因为这里的 provider 适配的是 Anthropic Messages API 的消息形状：`tool_use`、`tool_result`、`input_schema`。DeepSeek 提供的是 Anthropic-compatible endpoint，我们只是把 SDK 的 `base_url` 指过去。

今天默认服务商是 DeepSeek，但代码学的是 Anthropic 协议和 Agent Loop。名字反映了学的是什么，不是连的是谁。

### 模型不调用工具

先把 prompt 写明确：

```bash
uv run agent-code "不要直接回答，请调用 system_date 工具获取今天日期，再回答。"
```

工具调用不是字符串匹配，是模型根据工具描述自己决定要不要调用。初学阶段用明确 prompt 验证 harness 更稳。后面模型理解能力上来了，自然不需要这么啰嗦的 prompt。

### `tool_result` 为什么是 user message

这是 Anthropic Messages API 的公开协议形状。模型返回 assistant `tool_use`，harness 执行工具，然后把结果作为下一轮 user message 里的 `tool_result` content block 发回去。

Day 1 的 `{"role": "tool"}` 只是 mock 阶段的内部简化，Day 2 接真实模型时必须切换到 Anthropic 的公开消息形状。这个差异一开始可能觉得绕，但跑通一次就明白了。

## 课后挑战

1. 用 `python-dotenv` 自动加载 `.env`，不用每次手动 `export ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_BASE_URL`。
2. 增加一个 `--api-key-env` 选项，允许用户指定从哪个环境变量读取 API Key。
3. 给 `system_date` 加一个 `timezone` 参数，练习带参数的 JSON Schema。
4. 把 `complete()` 改成 streaming，让终端实时显示文本 delta。

## 思考题


1. **Anthropic Messages API 的 `tool_use` / `tool_result` 协议长什么样？** 为什么 `tool_result` 必须塞在下一轮 `user` message 的 content blocks 里，而不是单独一个 `tool` role？（提示：和 Day 1 mock 里的 `{"role": "tool"}` 做对比。）

2. **`AnthropicProvider.complete()` 为什么要原样保存 `assistant_content`，自己根据 `text` 和 `tool_calls` 拼一份 `{"type": "text"}` 和 `{"type": "tool_use"}` 不行吗？** （提示：thinking / signature 这类额外 content block 一旦丢失，下一轮请求可能 400。）

3. **`max_steps` 在 Agent Loop 里干什么用？** 把它去掉，模型最坏会怎么折腾你？

4. **`ModelProvider` 我们用 `Protocol` 而不是抽象基类（ABC）。** 这个选择让 `agent.py` 对具体 provider 的依赖变成了什么形状？换一家服务商时少改了什么？

## 下一天

今天 Agent 第一次接入真实模型，也第一次按 Anthropic 工具调用协议组织 messages。下一天我们让工具从 `echo` 和 `system_date` 扩展到项目文件：`read_file`、`list_files`、`glob`、`grep` 和 `project_tree`。那时候 `--cwd` 会真正变成文件系统边界——Agent 开始能"看"你的代码了。
