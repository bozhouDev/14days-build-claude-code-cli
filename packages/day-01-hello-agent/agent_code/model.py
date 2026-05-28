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
