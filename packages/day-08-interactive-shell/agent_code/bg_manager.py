from __future__ import annotations

import os
import subprocess
import threading
import uuid
from pathlib import Path


_MINIMAL_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", ""),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", "/bin/bash"),
}


def start_background(command: str, cwd: Path) -> dict:
    """在后台启动 shell 命令，stdout/stderr 流式写入 .bg/<id>.out/.err。
    立即返回结构化信息，不等待命令结束。"""
    bg_id = f"bg-{uuid.uuid4().hex[:8]}"
    bg_dir = cwd / ".bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    out_path = bg_dir / f"{bg_id}.out"
    err_path = bg_dir / f"{bg_id}.err"

    out_f = open(str(out_path), "w")
    err_f = open(str(err_path), "w")

    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        env=_MINIMAL_ENV,
        stdout=out_f,
        stderr=err_f,
    )

    def _wait_and_close() -> None:
        """等子进程结束，关闭文件描述符。在 daemon 线程里跑。"""
        proc.wait()
        out_f.close()
        err_f.close()

    t = threading.Thread(target=_wait_and_close, daemon=True)
    t.start()

    return {
        "background_id": bg_id,
        "output_file": str(out_path.relative_to(cwd)),
        "stderr_file": str(err_path.relative_to(cwd)),
        "pid": proc.pid,
        "message": (
            f"Started in background with ID: {bg_id}. "
            f"Output: .bg/{bg_id}.out, Stderr: .bg/{bg_id}.err. "
            f"Use bash(\"cat .bg/{bg_id}.out\") to check output, "
            f"bash(\"kill {proc.pid}\") to stop."
        ),
    }