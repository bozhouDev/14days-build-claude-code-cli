"""agent_code/interactive.py — prompt_toolkit 交互 shell。

主线程 = PromptSession（输入、键位、状态栏 + slash 分派）；
worker 线程 = run_agent（阻塞 provider.complete + 工具执行）。
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from . import prompt_ui
from .runtime import RuntimeState
from .slash import SlashContext, dispatch_slash


def run_interactive_shell(
    state: RuntimeState,
    run_turn: Callable[[str], None],            # worker 调它跑一轮 Agent Loop
    make_slash_context: Callable[[], SlashContext],
) -> None:
    """启动交互 REPL。主线程读输入 + 分派 slash，worker 线程跑 Agent Loop。"""
    job_queue: "queue.Queue[str]" = queue.Queue()
    busy = threading.Event()                 # v3 新增：worker 跑 turn 时置位

    def worker_loop() -> None:
        while True:
            text = job_queue.get()
            if text == "__EXIT__":
                break
            state.abort_event.clear()
            busy.set()
            try:
                run_turn(text)
            except Exception as exc:          # provider/工具异常别让 worker 静默死掉
                print(f"[error] {exc}")
            finally:
                busy.clear()
            # turn 末 drain：把运行期间排队的输入接着跑
            while not state.input_queue.empty():
                job_queue.put(state.input_queue.get())

    worker = threading.Thread(target=worker_loop, daemon=True)
    worker.start()

    session: PromptSession[str] = PromptSession(
        key_bindings=build_key_bindings(state),
        bottom_toolbar=lambda: bottom_toolbar(state),
    )

    async def _run() -> None:
        # get_running_loop()：拿到 prompt_async 真正在跑的那条事件循环。
        # 线程拆开后，worker 要问用户（确认编辑、批准计划）不能直接抢 stdin——
        # terminal_asker 用 run_coroutine_threadsafe 把提问调度到这条循环上，
        # run_in_terminal 暂停输入框、问完再恢复，worker 阻塞在 .result() 等结果。
        # set_terminal_asker 在 1.5 的 prompt_ui 里定义。
        loop = asyncio.get_running_loop()

        def terminal_asker(func: Callable[[], Any]) -> Any:
            return asyncio.run_coroutine_threadsafe(run_in_terminal(func), loop).result()

        prompt_ui.set_terminal_asker(terminal_asker)

        # patch_stdout：worker 线程里 run_agent 的 console.print 会被安全地排到输入框上方
        with patch_stdout():
            while True:
                try:
                    text = (await session.prompt_async("> ")).strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if not text:
                    continue
                if text == "/exit":
                    break
                # slash 是 harness 控制面，主线程直接处理，不丢给模型
                if text.startswith("/"):
                    result = dispatch_slash(text, make_slash_context())
                    if result.handled:
                        if result.message:
                            print(result.message)
                        if result.should_query:
                            job_queue.put(result.prompt)
                        continue
                # 原来这里是: job_queue.put(text)
                if busy.is_set():
                    state.input_queue.put(text)        # 忙时入 type-ahead 队列
                    print("[queued] turn 结束后自动处理")
                else:
                    job_queue.put(text)

    asyncio.run(_run())             # 在这条事件循环上跑 prompt_async 读输入
    job_queue.put("__EXIT__")


def build_key_bindings(state: RuntimeState) -> KeyBindings:
    """v1 先只绑 ESC。v2 加 shift+tab。"""
    kb = KeyBindings()

    @kb.add("escape")
    def _(event: Any) -> None:
        state.abort_event.set()                 # 只置标志，真正的中断在 Agent Loop 步间处理（v3）

    @kb.add("s-tab")
    def _(event: Any) -> None:
        new_mode = state.cycle_permission_mode()
        print(f"[mode → {new_mode}]")          # 提示切到了哪个模式

    return kb


def bottom_toolbar(state: RuntimeState) -> str:
    """底部状态栏——当前模式 + 模型。"""
    mode = {"default": "default", "acceptEdits": "accept edits", "plan": "plan"}.get(
        state.permission_mode, state.permission_mode
    )
    return f" {mode} · {state.model} "
