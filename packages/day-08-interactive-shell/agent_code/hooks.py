from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


HOOKS_FILE = "hooks.json"


def load_hooks(cwd: Path) -> dict[str, list[dict[str, Any]]]:
    """加载 hooks.json。文件不存在返回空 dict。"""
    file_path = cwd / HOOKS_FILE
    if not file_path.exists():
        return {}
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("hooks", data)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[hook warning] failed to load {file_path}: {exc}")
        return {}


def _matches(tool_name: str, matcher: str) -> bool:
    if matcher == "*":
        return True
    if "|" in matcher:
        return tool_name in matcher.split("|")
    return matcher == tool_name


def _run_hook_command(command: str, input_data: dict[str, Any], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            input=json.dumps(input_data, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "hook timed out"
    except Exception as exc:
        return False, f"hook execution error: {exc}"


def run_hooks(
    event: str,
    tool_name: str,
    tool_input: dict[str, Any],
    cwd: Path,
    tool_result: str = "",
) -> list[dict[str, Any]]:
    """执行匹配 event/tool_name 的 hooks，返回每条 hook 的执行结果。"""
    config = load_hooks(cwd)
    entries = config.get(event, [])
    results: list[dict[str, Any]] = []
    for entry in entries:
        matcher = entry.get("matcher", "*")
        if not _matches(tool_name, matcher):
            continue

        commands: list[str] = []
        if "run" in entry:
            commands = [entry["run"]]
        elif "hooks" in entry:
            for h in entry["hooks"]:
                if isinstance(h, dict) and h.get("type") == "command":
                    cmd = h.get("command", "")
                    if cmd:
                        commands.append(cmd)

        for cmd in commands:
            input_data = {
                "event": event,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_result": tool_result,
                "cwd": str(cwd),
            }
            success, output = _run_hook_command(cmd, input_data, cwd)
            results.append(
                {
                    "event": event,
                    "tool": tool_name,
                    "command": cmd,
                    "success": success,
                    "output": output,
                }
            )
    return results


def run_hooks_raw(event: str, payload: dict[str, Any], cwd: Path) -> list[dict[str, Any]]:
    """跑没有工具上下文的 hook（如 Stop）。payload 整个作为 stdin JSON 传给 hook。"""
    config = load_hooks(cwd)
    results: list[dict[str, Any]] = []
    for entry in config.get(event, []):
        matcher = entry.get("matcher", "*")
        if matcher not in ("*", ""):          # Stop 没有 tool_name，只认 * / 空 matcher
            continue
        commands: list[str] = []
        if "run" in entry:
            commands = [entry["run"]]
        else:
            for h in entry.get("hooks", []):
                if isinstance(h, dict) and h.get("type") == "command" and h.get("command"):
                    commands.append(h["command"])
        for cmd in commands:
            success, output = _run_hook_command(cmd, payload, cwd)
            results.append({"event": event, "command": cmd, "success": success, "output": output})
    return results
