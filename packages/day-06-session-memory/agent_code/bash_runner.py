from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .fs_safety import truncate_output

# bash 执行的环境变量最小集合——不让宿主机的敏感变量漏进子进程
_MINIMAL_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", ""),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", "/bin/bash"),
}


def run_sync(command: str, cwd: Path, timeout: int = 30) -> str:
    """同步执行 shell 命令。cwd 锁定在项目目录，超时后杀进程。"""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            env=_MINIMAL_ENV,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"

    output = proc.stdout.decode("utf-8", errors="replace")
    if proc.stderr:
        stderr_text = proc.stderr.decode("utf-8", errors="replace")
        if stderr_text.strip():
            output += "\n[stderr]\n" + stderr_text

    # 统一截断，防止长输出撑爆上下文
    truncated = truncate_output(output.strip(), max_chars=12000)

    if proc.returncode != 0:
        return f"exit code {proc.returncode}\n{truncated}"
    return truncated if truncated else "(no output)"