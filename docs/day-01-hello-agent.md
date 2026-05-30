# Day 1：Hello Agent

- 今天我们一起写一个大概 150 行的 Python CLI，叫 `agent-code`。
- 跑完之后，你会亲眼看到模型不是直接回答你，而是**先请求工具 → 执行 → 拿到结果 → 再回答**。这就是 Agent 和 Chatbot 的分水岭。
- 这是一个教学项目。我们参考 Claude Code 的架构思路，用 Python 从零实现一个最小可跑的版本。二进制命令故意叫 `agent-code`，不和官方 CLI 混淆。



我会从最笨的回声 CLI 开始，分三次小迭代把它变成一个会调用工具的 Agent。每一版你都能 `uv run` 跑出来，亲眼看到这一版多了什么。

## 起手：建一个空项目

```bash
mkdir agent-code && cd agent-code
uv init --package
uv add typer rich
uv add --dev pytest
rm -rf src
mkdir -p agent_code tests
touch agent_code/__init__.py
```

这里容易踩一个坑：新版 `uv init --package` 默认会生成 `src/agent_code/`。这套教程和仓库里的参考快照统一使用项目根目录下的 `agent_code/`，所以我们先删掉 `src/`，后面所有 `agent_code/xxx.py` 都指的是 `agent-code/agent_code/xxx.py`。

把 `pyproject.toml` 的关键部分改成下面这样（其它字段保留 `uv init` 生成的就行）：

```toml
[project]
name = "agent-code"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "rich>=13.7.0",
  "typer>=0.12.0",
]

[project.scripts]
agent-code = "agent_code.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["agent_code"]
```

`[project.scripts]` 这一行很关键：它让 `uv run agent-code` 知道入口是 `agent_code/cli.py` 里的 `main()`。`[tool.hatch.build.targets.wheel]` 则告诉打包工具：我们要安装的是项目根目录下的 `agent_code/`，不是刚才删掉的 `src/agent_code/`。

仓库里的完成版我会放在 `packages/day-01-hello-agent/`。那是参考答案快照，**不是**让你每天新建一个目录。后面 7 天你都在同一个 `agent-code` 项目里继续改。

## v1：能回声的 CLI

先写一个最笨的版本：CLI 接收一段文字，原样回声。

在项目根目录的 `agent_code/` 下新建 `cli.py`，完整路径是 `agent-code/agent_code/cli.py`：

```python
from __future__ import annotations

import typer
from rich.console import Console

console = Console()
app = typer.Typer(add_completion=False)


@app.callback(invoke_without_command=True)
def main_command(prompt: str = typer.Argument("hi")) -> None:
    console.print(f"回声: {prompt}")


def main() -> None:
    app()
```

跑一下：

```bash
$ uv run agent-code "hi"
回声: hi
```

看到 `回声: hi` 就对了，v1 完成。

这是个 `cat` 命令的复杂版本。它没有"模型"，没有"工具"，更不会"做事"。但它已经帮我们把骨架立起来了：`uv run agent-code` 能跑、`pyproject.toml` 入口接对了、`typer` 接住了参数。

好了，现在我们有了一个能跑但啥也不会的 CLI。下一步让它脑子里有个"模型"。

## v2：把"模型"分出去
要让程序变成 Agent，第一步得承认一件事：**回答用户的不应该是 CLI，而是"模型"**。

哪怕今天我们用一个假模型，也要先把这条边界画清楚。下一天换成真实模型时，我们会改 provider wiring 和真实 API 的消息形状，但 `CLI → run_agent → provider.complete()` 这条主边界不用推倒重来。这个边界画好了，后面几天你会一直受益——这就是 harness 的价值。

新建 `agent_code/model.py`：

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelResponse:
    # 一次模型响应。v2 只有最终文本；v3 我们会加上工具调用。
    text: str


class MockProvider:
    def complete(self, prompt: str) -> ModelResponse:
        # 一个假模型，固定回一句话，够用来打通 CLI <-> Provider 这条边界。
        return ModelResponse(text=f"我是 MockProvider，你说了：{prompt}")
```

`cli.py` 改动两处。先在顶部加一行 import：

```python
from .model import MockProvider
```

然后把 `main_command` 里 v1 的那一行：

```python
console.print(f"回声: {prompt}")
```

替换成：

```python
provider = MockProvider()
response = provider.complete(prompt)
console.print(f"final: {response.text}")
```

跑一下：

```bash
$ uv run agent-code "hi"
final: 我是 MockProvider，你说了：hi
```

注意输出变成了 `final:` 开头，不再是 `回声:`。这说明 CLI 已经把回答的活外包给了 provider。

现在 CLI 不再亲自回答了 —— 它把活外包给了 `MockProvider`。但这个"模型"还是只能用嘴说话：你让它"用 echo 工具说 hi"，它也只会复述这句话。要让它真正动手做事，它能输出的东西就不能只是 `text`。

## v3：让模型请求工具

到这里才算真正进入 Agent 的世界。前面都是热身。

要让模型动手，它得能输出："我想调用工具 X，参数是 Y。" 然后程序去执行，把结果交回去，模型再继续。

这个 **"模型想做什么 → 程序执行 → 把结果交回模型"** 的循环，就是 Agent Loop。

我们分三步走，每一步你都能跑起来看到变化：扩协议、写工具表、串循环。

### 3.1 扩展 model.py，让模型能开"工具调用单"

v2 的 `MockProvider` 太简单了，现在它要长两样本事：

- `ModelResponse` 除了 `text`，还要能装一个或多个**工具调用**。
- `complete()` 接收的不再是单句 `prompt`，而是 `messages: list` —— 因为模型完成工具调用之后还要看到完整对话，才能把工具结果变成最终回答。

这个文件改动最大，但别慌——本质上就是把"一句话回答"升级成"能下单、能读结果"。把 `agent_code/model.py` 整体重写成这样（v2 的 7 行扩成 40 行）：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    # 模型请求 harness 执行这个工具。
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    # harness 把工具观察结果交回模型。
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ModelResponse:
    # 一次模型响应可以是最终文本，也可以是工具调用。
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    stop_reason: str = "end_turn"


class MockProvider:
    def complete(self, messages: list[dict[str, str]]) -> ModelResponse:
        last = messages[-1]

        if last["role"] == "user":
            # 第一轮不直接回答，而是请求 harness 执行工具。
            text = last["content"].replace("用 echo 工具说", "").strip() or last["content"]
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call_echo_1",
                        name="echo",
                        arguments={"text": text},
                    )
                ],
                stop_reason="tool_use",
            )

        if last["role"] == "tool":
            # 第二轮把工具观察结果变成最终回答。
            return ModelResponse(text=f"echo 工具返回：{last['content']}")

        return ModelResponse(text="我现在只能演示 echo 工具。")
```

`MockProvider` 我故意写得很笨：用户说什么都请求 `echo`，工具结果回来就拼一句话。重点不是它聪明，而是 Agent Loop 的形状能跑起来。

`stop_reason` 今天只会出现 `tool_use` 和 `end_turn`。先把这个字段留着，下一天接真实 Claude 时就不用重写。

### 3.2 工具注册表

好，模型现在会说"我要用 `echo`"了。但光说不练不行——程序得有个地方把工具名映射到真正的 Python 函数。新建 `agent_code/tools.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .model import ToolCall, ToolResult


ToolFunc = Callable[[dict[str, Any]], str]


@dataclass
class Tool:
    name: str
    description: str
    run: ToolFunc


def echo(args: dict[str, Any]) -> str:
    return str(args.get("text", ""))


class ToolRegistry:
    def __init__(self) -> None:
        # 注册表是工具名和 Python 函数之间的 harness 边界。
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def run(self, call: ToolCall) -> ToolResult:
        # 未知工具也返回 observation，不让 Agent Loop 崩掉。
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                content=f"unknown tool: {call.name}",
                is_error=True,
            )
        return ToolResult(tool_call_id=call.id, content=tool.run(call.arguments))


def default_tools() -> ToolRegistry:
    # Day 1 只有一个工具，后面会在这里加文件和 bash 工具。
    registry = ToolRegistry()
    registry.register(Tool(name="echo", description="Return the input text.", run=echo))
    return registry
```

后面要加 `read_file`、`bash` 等工具，都是在 `default_tools()` 这里多 `register` 一行。

### 3.3 把 model 和 tools 串成 Agent Loop

零件都到齐了，现在把它们串起来。这才是今天的重头戏。

这个循环今天只跑一轮，但它的形状就是所有 Agent 的骨架。后面几天只会在这个骨架上长肉。

新建 `agent_code/agent.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

from .model import MockProvider
from .tools import ToolRegistry


@dataclass
class AgentResult:
    final: str
    trace: list[str]


def run_agent(prompt: str, provider: MockProvider, tools: ToolRegistry) -> AgentResult:
    # messages 是每一轮都要交回 provider 的上下文。
    messages = [{"role": "user", "content": prompt}]
    trace: list[str] = []

    response = provider.complete(messages)

    for call in response.tool_calls or []:
        trace.append(f"tool_call: {call.name} {call.arguments}")

        result = tools.run(call)
        trace.append(f"observation: {result.content}")
        # 工具结果会成为下一轮模型调用的 observation。
        messages.append(
            {
                "role": "tool",
                "tool_call_id": result.tool_call_id,
                "content": result.content,
            }
        )

        response = provider.complete(messages)

    final = response.text or ""
    trace.append(f"final: {final}")
    return AgentResult(final=final, trace=trace)
```

今天循环只跑一轮（一次 `tool_call` → `observation` → 一次最终回答）。这里的 `{"role": "tool"}` 是 Day 1 为了跑通 mock loop 做的内部简化；下一天接真实 Claude 时，会换成 Anthropic Messages API 公开要求的 `tool_use` / `tool_result` 消息形状。

最后把 `cli.py` 接到 `run_agent`。改两处。顶部 import 调整为：

```python
from .agent import run_agent
from .model import MockProvider
from .tools import default_tools
```

`main_command` 里 v2 的三行：

```python
provider = MockProvider()
response = provider.complete(prompt)
console.print(f"final: {response.text}")
```

替换成：

```python
result = run_agent(prompt, MockProvider(), default_tools())
for line in result.trace:
    console.print(line)
```

跑一下：

```bash
$ uv run agent-code "用 echo 工具说 hi"
tool_call: echo {'text': 'hi'}
observation: hi
final: echo 工具返回：hi
```

**这 3 行就是 Agent Loop 的最小形态**：

```txt
model -> tool_call -> tool -> observation -> model -> final
```

看到这三行了吗？`tool_call` → `observation` → `final`。这就是 Agent Loop 的脉搏。以后不管模型多聪明、工具多复杂，都是这个节奏。

回头看一眼——v1 的 CLI 只能回声，v2 的"模型"只会动嘴，到 v3 模型才第一次真正动了手。

### 小结：到底什么是 tool，什么是 function call

跑通了再回头把这两个名词说清楚，接下来你天天都会用到。

**Tool（工具）就是一个你交给模型差遣的 Python 函数。** 模型本体只会吐字——它没法读文件、跑命令、查数据库。所以我们在 harness 这边备好一个个函数，给每个配上名字和说明（我们的 `Tool` 就是 `name` + `description` + `run` 三件套），再塞进 `ToolRegistry`。这一注册，就等于告诉模型："这些活你可以差我去干。"今天只有一个 `echo`，Day 3 之后这里会冒出 `read_file`、`bash`。

**Function call（函数调用）是模型"下单"的那个动作，不是"执行"。** 关键就在这：模型永远不会自己运行那个函数。它能做的只是输出一张结构化的单子——"我要调 `echo`，参数 `{"text": "hi"}`"，也就是我们的 `ToolCall(name=..., arguments=...)`。真正动手的是 harness：`ToolRegistry.run()` 按名字找到 Python 函数、跑出结果，再把这条 observation 交回模型。模型下单、harness 执行、结果回流——这正是前面那条链为什么是 `tool_call → observation → final`。

最后点破一个名词坑：**function call、tool call、tool use 基本是同一件事**，只是各家叫法不同。OpenAI 早期叫 function calling，Anthropic 叫 tool use，我们代码里统一用 `ToolCall` / `tool_calls`。下一天接真实 Claude，你会在 API 里看到 `tool_use` / `tool_result`，心里清楚它就是今天这套东西换了层壳，就不慌了。

## 收尾：REPL、slash 命令和 --cwd

v3 有个小遗憾：只能一次性跑。`agent-code "..."` 跑一次就退出。公开的 Claude Code 还可以直接敲 `claude` 进 REPL，里面用 `/help`、`/exit` 这类 slash 命令。我们也加上这层。同时把 `--cwd` 工作目录参数补上，Day 3 的文件工具会用到。

把 `agent_code/cli.py` 改成下面这样（这是今天的终版）：

```python
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .agent import run_agent
from .model import MockProvider
from .tools import default_tools

console = Console()
app = typer.Typer(add_completion=False)


def render_header(cwd: Path) -> None:
    # cwd 是后续文件工具和 bash 工具都要遵守的工作边界。
    console.print("[bold]Agent Code[/bold]")
    console.print(f"[dim]cwd: {cwd}[/dim]\n")


def handle_slash(line: str) -> bool:
    # slash command 是 CLI 控制命令，不交给模型。
    if line == "/help":
        console.print("可用命令：/help, /exit")
        return True
    return False


def run_once(prompt: str, cwd: Path) -> None:
    render_header(cwd)
    result = run_agent(prompt, MockProvider(), default_tools())
    for line in result.trace:
        console.print(line)


@app.callback(invoke_without_command=True)
def main_command(
    prompt: str = typer.Argument("", help="Prompt to send to the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", "-C"),
) -> None:
    # 启动时只解析一次 cwd，让整次运行共享同一个工作目录。
    resolved_cwd = cwd.resolve()
    text = prompt.strip()

    if text:
        # 有 prompt 参数时进入一次性模式：运行一次就退出。
        run_once(text, resolved_cwd)
        return

    # 注释1：REPL 分支——命令后面没跟 prompt，走下面交互循环
    render_header(resolved_cwd)
    console.print("输入 /help 查看命令，输入 /exit 退出。")
    while True:
        line = typer.prompt(">").strip()
        if not line:
            continue
        if line == "/exit":
            console.print("Bye.")
            return
        if line.startswith("/") and handle_slash(line):
            continue
        run_once(line, resolved_cwd)


def main() -> None:
    app()
```

这里有个边界我希望你记住：**slash 命令是 CLI 自己处理的，不会被当成 prompt 发给模型。** 后面 Day 7 我们再把它做成可扩展的注册系统。

跑两个验收。一次性模式：

```bash
$ uv run agent-code "用 echo 工具说 hi"
Agent Code
cwd: /your/project

tool_call: echo {'text': 'hi'}
observation: hi
final: echo 工具返回：hi
```

REPL 模式：

```bash
$ uv run agent-code
Agent Code
cwd: /your/project

输入 /help 查看命令，输入 /exit 退出。
> /help
可用命令：/help, /exit
> 用 echo 工具说 hello
...
> /exit
Bye.
```

试着在 REPL 里多输入几行，每次都是完整的一次 Agent Loop。`/exit` 退出。

## 手动 trace 一遍

输入 `用 echo 工具说 hi`，发生了什么：

```txt
1. CLI 解析 prompt 和 cwd。
2. run_agent 把 prompt 包成 messages = [user message]。
3. MockProvider 看到 user message，返回 ModelResponse(tool_calls=[echo])。
4. ToolRegistry 找到 echo 工具，执行后得到 "hi"。
5. ToolResult(content="hi") 被 append 到 messages。
6. MockProvider 看到最新的 tool message，返回 ModelResponse(text="echo 工具返回：hi")。
7. CLI 把 trace 三行打印出来。
```

读完这 7 步，试着在脑子里"演奏"一遍——以后 debug 时你就是在脑子里跑这个流程。

## 今天有了什么

- **CLI runtime**：`uv run agent-code` 既支持一次性 prompt，也支持 REPL，认识 `--cwd`，会拦截 `/help` / `/exit`。
- **ModelResponse 协议**：v3 把它定下来。下一天换真实模型时，内部仍然用这组 `text/tool_calls/stop_reason` 字段承接模型响应，只是 provider wiring 和外部 API 消息形状会升级。
- **Tool Calling**：模型可以输出"我想用工具 X"，而不只是文本。
- **Observation**：工具结果回到 messages 里，推动下一轮回答。
- **Agent Loop**：`model -> tool -> observation -> model` 是代码 Agent 的最小核心。

## 常见问题

### `agent-code` 命令找不到

确认 `pyproject.toml` 里有：

```toml
[project.scripts]
agent-code = "agent_code.cli:main"
```

然后用 `uv run agent-code`，不要直接运行 `agent-code`。

### 输出里的字典引号和示例不同

Python 打印 dict 可能使用单引号。只要看到 `tool_call`、`observation`、`final` 这三段就对了。

### `ModuleNotFoundError: No module named 'agent_code.cli'`

这个错最常见的原因是项目里同时有两个包目录：`src/agent_code/` 和 `agent_code/`。`uv init --package` 默认生成了前者，但我们把 `cli.py` 写到了后者，打包时就会找不到 `agent_code.cli`。

按这三步统一一下：

```bash
rm -rf src
```

确认 `pyproject.toml` 里有：

```toml
[project.scripts]
agent-code = "agent_code.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["agent_code"]
```

然后确认文件在这里：

```txt
agent-code/
  agent_code/
    __init__.py
    cli.py
```

最后仍然用 `uv run agent-code "hi"` 跑，不要直接运行 `python agent_code/cli.py`。

## 课后挑战

1. 加一个 `uppercase` 工具，把输入变大写。
2. 给 `ToolRegistry.run()` 写一个更友好的"未知工具"错误（例如附上可用工具列表）。
3. 让 `/help` 打印当前注册的所有工具。

## 思考题

几个开放性问题，先自己憋一句话答案，再继续往下看。面试官真问起 Agent 这块，能不能讲清楚就看这关。

1. **用一句话说说 Agent Loop 是什么？** 它和普通 chatbot 的根本差别在哪？

2. **`ToolRegistry` 这个对象在 harness 里担任什么角色？** 没有它，直接在 Agent Loop 里写一串 `if call.name == "echo": ...` 派发到具体函数，会出什么问题？

3. **v2 我们专门把"模型"分到 `MockProvider` 里，CLI 当天根本用不到这条边界——这一步是不是多此一举？** 
4. **v3 的输出 `tool_call → observation → final` 就是 Agent Loop 的最小形态。** 如果让你给同事用三句话讲清楚这条链上 `model` / `harness` / `tool` 各自干了什么，你会怎么讲？

## 下一天

今天我们用 `MockProvider` 跑通了最小 Agent Loop。下一天终于要把这个假模型换掉了——接上真实模型之后，先让它通过 `system_date` 查当前时间，再把今天的单步 `for` 扩成 `while step < max_steps`。读文件会放到 Day 3；因为 `ModelResponse` 已经定下来了，下一天不用推翻整个 harness，只是在 provider、真实消息协议和循环控制上继续加一层。
