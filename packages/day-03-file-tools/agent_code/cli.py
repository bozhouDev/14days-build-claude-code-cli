from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .agent import run_agent
from .tools import default_tools
from .model import create_provider

console = Console()
app = typer.Typer(add_completion=False)


def render_header(cwd: Path, provider: str, model: str, base_url: str | None) -> None:
    console.print("[bold]Agent Code[/bold]")
    console.print(f"[dim]cwd: {cwd}[/dim]")
    console.print(f"[dim]provider: {provider}  model: {model}[/dim]")
    if base_url:
        console.print(f"[dim]base_url: {base_url}[/dim]")
    console.print()


def handle_slash(line: str) -> bool:
    # slash command 是 CLI 控制命令，不交给模型。
    if line == "/help":
        console.print("可用命令：/help, /exit")
        return True
    return False


def run_once(
    prompt: str,
    cwd: Path,
    provider_name: str,
    model: str,
    base_url: str | None,
    max_steps: int,
) -> None:
    render_header(cwd, provider_name, model, base_url)
    provider = create_provider(provider_name, model, base_url)  # TODO: 引入 slash 命令注册系统
    result = run_agent(prompt, provider, default_tools(), max_steps=max_steps, cwd=cwd)
    for line in result.trace:
        console.print(line)


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

    # 注释1：REPL 分支——命令后面没跟 prompt，走下面交互循环
    render_header(resolved_cwd, provider, model, base_url)
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
        run_once(line, resolved_cwd, provider, model, base_url, max_steps)


def main() -> None:
    app()
