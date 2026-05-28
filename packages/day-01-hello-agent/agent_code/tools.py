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