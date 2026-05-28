from __future__ import annotations

from pathlib import Path

# memdir 根目录——放在 .agent/ 下，和 sessions/ 同级
MEMORY_DIR = ".agent/memory"
INDEX_FILE = "MEMORY.md"
# 索引文件保护：200 行 + 25KB 截断
# 索引一旦塞太多条，模型读 system prompt 时就被这一段挤掉别的上下文
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024


def get_memdir(cwd: Path) -> Path:
    """返回 .agent/memory 目录路径。"""
    return cwd / MEMORY_DIR


def ensure_memdir(cwd: Path) -> Path:
    """确保 .agent/memory 和四个类型子目录存在。返回 memdir 路径。"""
    memdir = get_memdir(cwd)
    memdir.mkdir(parents=True, exist_ok=True)
    for sub in ("user", "feedback", "project", "reference"):
        (memdir / sub).mkdir(exist_ok=True)
    return memdir


def index_path(cwd: Path) -> Path:
    """返回 MEMORY.md 索引文件路径。"""
    return get_memdir(cwd) / INDEX_FILE


def topic_path(cwd: Path, mem_type: str, slug: str) -> Path:
    """返回 .agent/memory/<type>/<slug>.md 路径。"""
    return get_memdir(cwd) / mem_type / f"{slug}.md"