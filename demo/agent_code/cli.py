from __future__ import annotations

import threading
from pathlib import Path
from queue import Empty, Queue

from click.exceptions import Abort
import typer
from rich.console import Console

from .agent import run_agent
from .slash import SlashContext, dispatch_slash  # Day 7：slash 注册表
from .tools import default_tools
from .model import create_provider
from .session import Session  # Day 6：会话持久化
from .agent import build_system_prompt  # Day 6 v2：cold start 拼项目规则
from .scheduler import CronScheduler  # Day 7：cron 调度器
from .cron_tools import set_scheduler  # Day 7：让 cron 工具访问同一个 scheduler

console = Console()
app = typer.Typer(add_completion=False)


def render_header(cwd: Path, provider: str, model: str, base_url: str | None) -> None:
    console.print("[bold]Agent Code[/bold]")
    console.print(f"[dim]cwd: {cwd}[/dim]")
    console.print(f"[dim]provider: {provider}  model: {model}[/dim]")
    if base_url:
        console.print(f"[dim]base_url: {base_url}[/dim]")
    console.print()


def run_once(
    prompt: str,
    cwd: Path,
    provider_name: str,
    model: str,
    base_url: str | None,
    max_steps: int,
    permission_mode: str,
    session: Session | None = None,  # Day 6：可选的 session
    system_prompt: str | None = None,  # Day 6 v2：由 main_command 拼好传入
) -> None:
    render_header(cwd, provider_name, model, base_url)
    if session:
        suffix = " (resumed)" if session.resumed else ""
        console.print(f"[dim]session: {session.session_id}{suffix}[/dim]")
    provider = create_provider(provider_name, model, base_url)  # TODO: 引入 slash 命令注册系统
    run_agent(
        prompt, provider, default_tools(),
        max_steps=max_steps, cwd=cwd,
        permission_mode=permission_mode,
        session=session,
        system_prompt=system_prompt,
    )


@app.callback(invoke_without_command=True)
def main_command(
    prompt: str = typer.Argument("", help="Prompt to send to the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", "-C"),
    provider: str = typer.Option("anthropic", "--provider"),
    model: str = typer.Option("deepseek-v4-flash", "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    max_steps: int = typer.Option(8, "--max-steps"),
    permission_mode: str = typer.Option("default", "--permission-mode", help="Permission mode: default, acceptEdits, plan"),
    # Day 6：会话持久化入口
    resume: str | None = typer.Option(None, "--resume", help="按 session id 恢复指定会话"),
    continue_: bool = typer.Option(False, "--continue", "-c", help="恢复 cwd 最近一次会话"),
) -> None:
    resolved_cwd = cwd.resolve()
    text = prompt.strip()

    # Day 6：按 flag 分支决定 session 来源；具体打印交给 run_once，
    # 避免新建路径打一次、恢复路径打两次
    session: Session | None = None
    if continue_:
        session = Session.load_latest(resolved_cwd)
        if session is None:
            console.print("[red]没有找到历史会话，无法 --continue。[/red]")
            raise typer.Exit(code=1)
    elif resume:
        session = Session.load_id(resolved_cwd, resume)
        if session is None:
            console.print(f"[red]找不到 session: {resume}[/red]")
            raise typer.Exit(code=1)

    # Day 6 v2：cold start 时把 AGENT.md 读一次，整轮 CLI 共享同一份 system prompt
    system_prompt = build_system_prompt(resolved_cwd)

    def run_user_input(line: str) -> None:
        """统一处理用户输入：先走 slash dispatch，未命中再进入 Agent Loop。"""
        nonlocal session
        slash_result = dispatch_slash(
            line,
            SlashContext(
                cwd=resolved_cwd,
                permission_mode=permission_mode,
                model=model,
                provider=provider,
                session_id=session.session_id if session else None,
            ),
        )
        if slash_result.handled:
            if slash_result.message:
                console.print(slash_result.message)
            if slash_result.should_query:
                if session is None:
                    session = Session.create(resolved_cwd)
                run_once(
                    slash_result.prompt, resolved_cwd, provider, model, base_url, max_steps,
                    permission_mode, session=session, system_prompt=system_prompt,
                )
            return

        if session is None:
            session = Session.create(resolved_cwd)
        run_once(
            line, resolved_cwd, provider, model, base_url, max_steps,
            permission_mode, session=session, system_prompt=system_prompt,
        )

    if text:
        run_user_input(text.strip())
        return

    # 注释1：REPL 分支——命令后面没跟 prompt，走下面交互循环
    render_header(resolved_cwd, provider, model, base_url)
    # Day 6：REPL 整个周期共享一个 session（run_once 每轮自己打印 session 头）
    if session is None:
        session = Session.create(resolved_cwd)

    # Day 7：REPL 模式启动 cron scheduler；一次性模式不启动
    scheduler = CronScheduler(resolved_cwd)
    set_scheduler(scheduler)
    scheduler.start()

    console.print("输入 /help 查看命令，输入 /exit 退出。")
    input_queue: Queue[str | None] = Queue()
    stop_repl = threading.Event()

    def _read_input() -> None:
        while not stop_repl.is_set():
            try:
                line = typer.prompt(">").strip()
            except (KeyboardInterrupt, EOFError, Abort):
                input_queue.put(None)
                return
            input_queue.put(line)

    input_thread = threading.Thread(target=_read_input, daemon=True)
    input_thread.start()

    try:
        while True:
            # 即使用户没有敲下一行，主线程也会定期检查 cron pending queue。
            for pp in scheduler.drain_pending():
                console.print(f"[dim]cron: running scheduled job → {pp}[/dim]")
                run_user_input(pp)

            try:
                line = input_queue.get(timeout=0.5)
            except Empty:
                continue

            if line is None:
                break
            if not line:
                continue
            if line == "/exit":
                console.print("Bye.")
                break
            run_user_input(line)
    finally:
        stop_repl.set()
        scheduler.stop()


def main() -> None:
    app()
