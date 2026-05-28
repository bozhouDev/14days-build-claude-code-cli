from __future__ import annotations

import difflib

import typer


def render_diff(old: str, new: str, path: str) -> str:
    """用 difflib 生成 unified diff，给增删行加 rich markup 着色。"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    colored: list[str] = []
    for line in diff_lines:
        line = line.rstrip()
        if line.startswith("---") or line.startswith("+++"):
            colored.append(f"[bold]{line}[/bold]")
        elif line.startswith("-"):
            colored.append(f"[red]{line}[/red]")
        elif line.startswith("+"):
            colored.append(f"[green]{line}[/green]")
        elif line.startswith("@@"):
            colored.append(f"[cyan]{line}[/cyan]")
        else:
            colored.append(line)
    return "\n".join(colored)


def confirm_edit(path: str) -> bool:
    """让用户确认是否应用这次编辑，默认不应用。"""
    return typer.confirm(f"Apply this edit to {path}?", default=False)


def confirm_command(command: str) -> bool:
    """让用户确认是否执行这条 bash 命令，默认不执行。"""
    return typer.confirm(f"Run this command?", default=False)


def confirm_tool_use(tool_name: str, detail: str) -> bool:
    """让用户确认非 bash 的 ask 类工具，例如访问外部网络。"""
    return typer.confirm(f"Allow {tool_name}: {detail}?", default=False)


def prompt_single_choice(question: str, labels: list[str]) -> str | None:
    """展示一个 numbered menu 让用户单选，返回被选中的 label。"""
    from rich.console import Console

    console = Console()
    console.print(f"\n[bold yellow]? {question}[/bold yellow]")
    for i, label in enumerate(labels, 1):
        console.print(f"  {i}. {label}")
    console.print(f"  0. [dim]Skip / Other[/dim]")

    try:
        choice = typer.prompt("Choice", default="0")
        idx = int(choice)
        if 1 <= idx <= len(labels):
            return labels[idx - 1]
        return None
    except (ValueError, TypeError):
        return None