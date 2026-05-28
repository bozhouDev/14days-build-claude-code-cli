from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import Anthropic


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
    assistant_content: list[dict[str, Any]] | None = None
    stop_reason: str = "end_turn"

class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        system: str | None = None,
    ) -> ModelResponse:
        ...


def _content_block_to_dict(block: Any) -> dict[str, Any]:
    # DeepSeek 的 Anthropic-compatible endpoint 可能返回 thinking block。
    # 后续请求必须把这些 assistant content blocks 原样传回去。
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if hasattr(block, "dict"):
        return block.dict(exclude_none=True)
    data = {"type": block.type}
    for name in ("text", "id", "name", "input", "thinking", "signature"):
        if hasattr(block, name):
            data[name] = getattr(block, name)
    return data


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
        system: str | None = None,
    ) -> ModelResponse:
        # 先准备一次模型请求的基础参数。messages 是 Agent Loop 累积出来的上下文。
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }

        if system:
            kwargs["system"] = system

        # 如果 registry 里有工具，就把我们的 Tool 翻译成 Anthropic 的 tools 格式。
        # 这一步只是“告诉模型有哪些工具”，还没有执行任何工具。
        if tools:
            kwargs["tools"] = _to_anthropic_tools(tools)

        response = self.client.messages.create(**kwargs)

        # Claude/DeepSeek 可能同时返回 text block 和 tool_use block。
        # text_parts 收集普通回答；tool_calls 收集“模型想调用工具”的请求。
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        assistant_content: list[dict[str, Any]] = []

        for block in response.content:
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
class MockProvider:
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        system: str | None = None,
    ) -> ModelResponse:
        last = messages[-1]

        if last["role"] == "user":
            content = last["content"]
            if isinstance(content, list) and content and content[0].get("type") == "tool_result":
                return ModelResponse(text=f"echo 工具返回：{content[0]['content']}")

            # 第一轮不直接回答，而是请求 harness 执行工具。
            text = str(content).replace("用 echo 工具说", "").strip() or str(content)
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

# 辅助函数：把工具列表转换成 Anthropic 格式
def _to_anthropic_tools(tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]

# 辅助函数：把工具调用参数转换成 Anthropic 格式
def _parse_tool_input(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def create_provider(name: str, model: str, base_url: str | None = None) -> ModelProvider:
    if name == "anthropic":
        return AnthropicProvider(model=model, base_url=base_url)
    if name == "mock":
        return MockProvider()
    raise ValueError(f"unknown provider: {name}")