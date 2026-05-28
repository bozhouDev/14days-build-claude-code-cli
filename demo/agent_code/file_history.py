from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def backup(cwd: Path, path: Path, old_content: str) -> Path | None:
    """写盘前把文件旧内容备份到 .agent/history/<rel>/<ts>。
    备份不是工具，模型看不到它——它是 harness 的全局安全网。
    失败不阻塞编辑，返回 None。"""
    try:
        rel = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return None  # 路径在 cwd 外，不备份

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    backup_dir = cwd / ".agent" / "history" / rel
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / ts

    try:
        backup_path.write_text(old_content, encoding="utf-8")
    except OSError:
        return None
    return backup_path