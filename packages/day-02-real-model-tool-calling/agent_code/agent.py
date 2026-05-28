from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import ModelProvider, ModelResponse
from .tools import ToolRegistry


@dataclass
class AgentResult:
    final: str
    trace: list[str]
    messages: list[dict[str, Any]]

# 辅助函数：把模型响应转换成 Anthropic 格式
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

# 辅助函数：把工具结果转换成 Anthropic 格式
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

def run_agent(prompt: str, provider: ModelProvider, tools: ToolRegistry,max_steps: int = 8) -> AgentResult:
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
