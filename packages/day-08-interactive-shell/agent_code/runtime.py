"""agent_code/runtime.py -- 跨主线程 / worker 线程共享的运行态。"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field


@dataclass
class TodoItem:
    content: str        # 要做什么（祈使句，如 "实现 reverse 函数"）
    status: str         # pending | in_progress | completed
    active_form: str    # 正在做什么（进行时，如 "正在实现 reverse 函数"）


@dataclass
class RuntimeState:
    # 主线程（输入/键位/状态栏）写这些；worker 线程（Agent Loop）读这些。
    permission_mode: str = "default"      # default | acceptEdits | plan，shift+tab 改它
    model: str = "deepseek-v4-pro"        # /model 改它，下一轮 turn 生效
    provider: str = "anthropic"
    abort_event: threading.Event = field(default_factory=threading.Event)  # ESC 置位，loop 步间检查
    input_queue: "queue.Queue[str]" = field(default_factory=queue.Queue)   # type-ahead，turn 末 drain
    todo_store: list[TodoItem] = field(default_factory=list)   # v5 新增：共享待办板

    def cycle_permission_mode(self) -> str:
        """shift+tab 循环：default → acceptEdits → plan → default。只主线程调，无需锁。"""
        order = ["default", "acceptEdits", "plan"]
        idx = order.index(self.permission_mode) if self.permission_mode in order else 0
        self.permission_mode = order[(idx + 1) % len(order)]
        return self.permission_mode
