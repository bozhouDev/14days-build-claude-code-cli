from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sanitize_cwd(cwd: Path) -> str:
    """把绝对路径转成合法目录名：/ 替换为 _。"""
    path_str = str(cwd.resolve())
    # Windows 盘符的 : 和反斜杠也换掉
    sanitized = path_str.replace("/", "_").replace(":", "_").replace("\\", "_")
    # 去掉前导下划线
    return sanitized.lstrip("_")


def _sessions_dir(cwd: Path) -> Path:
    """返回 .agent/sessions/<sanitized_cwd>/，自动创建。"""
    dir_path = cwd / ".agent" / "sessions" / _sanitize_cwd(cwd)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


class Session:
    """一次会话。管理 session id、JSONL 落盘、读取历史消息。"""

    def __init__(self, cwd: Path, session_id: str, file_path: Path, resumed: bool = False) -> None:
        self.cwd = cwd
        self.session_id = session_id
        self.file_path = file_path
        # resumed=True 表示这是 --resume / -c 拉起来的，CLI 用它决定打印 "(resumed)"
        self.resumed = resumed

    @classmethod
    def create(cls, cwd: Path) -> "Session":
        """新建会话：生成 12 位 hex session_id，创建空 JSONL 文件。"""
        sid = uuid.uuid4().hex[:12]
        file_path = _sessions_dir(cwd) / f"{sid}.jsonl"
        file_path.touch()  # 创建空文件，标记 session 存在
        return cls(cwd=cwd, session_id=sid, file_path=file_path, resumed=False)

    @classmethod
    def load_latest(cls, cwd: Path) -> "Session | None":
        """加载最近一次会话（按 mtime）。没有则返回 None。"""
        sessions_dir = _sessions_dir(cwd)
        jsonl_files = list(sessions_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None
        latest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
        sid = latest.stem  # 文件名去 .jsonl 就是 session_id
        return cls(cwd=cwd, session_id=sid, file_path=latest, resumed=True)

    @classmethod
    def load_id(cls, cwd: Path, session_id: str) -> "Session | None":
        """按 session_id 加载指定会话。找不到则返回 None。"""
        file_path = _sessions_dir(cwd) / f"{session_id}.jsonl"
        if not file_path.exists():
            return None
        return cls(cwd=cwd, session_id=session_id, file_path=file_path, resumed=True)

    @property
    def history(self) -> list[dict[str, Any]]:
        """解析 JSONL 文件，返回 messages 列表（去掉 timestamp）。"""
        messages: list[dict[str, Any]] = []
        if not self.file_path.exists():
            return messages
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue  # 跳过损坏行，不阻塞整个 session 恢复
            # 只保留 role 和 content——模型不需要 timestamp
            messages.append({"role": data["role"], "content": data["content"]})
        return messages

    def append_messages(self, msgs: list[dict[str, Any]]) -> None:
        """向 JSONL 文件追加消息。每条自动加 UTC timestamp。"""
        now = datetime.now(timezone.utc).isoformat()
        with open(self.file_path, "a", encoding="utf-8") as f:
            for msg in msgs:
                record = {"role": msg["role"], "content": msg["content"], "timestamp": now}
                # separators 强制紧凑输出（默认带空格），让一行一条 JSON 看起来更稳
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")