from __future__ import annotations

import threading

import pathspec
from dataclasses import dataclass, field
from pathlib import Path


# 文本文件后缀白名单：直接放行，不用 peek 文件头。
TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".cfg", ".ini", ".env", ".sh", ".bash", ".zsh", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".sql", ".lock", ".gitignore",
}

# 单文件大小上限：超过就拒绝读取整文件。和官方 256 KiB 对齐。
MAX_READ_BYTES = 256 * 1024

# 单次工具 observation 上限。模型上下文有限，过长直接截尾。
DEFAULT_MAX_CHARS = 8000

# 默认跳过的目录名。任意祖先目录命中名单，整条路径都被剔除。
DEFAULT_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "dist", "build",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


@dataclass
class SkipPolicy:
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS
    gitignore: pathspec.PathSpec | None = None

    @classmethod
    def default(cls, gitignore: pathspec.PathSpec | None = None) -> "SkipPolicy":
        return cls(gitignore=gitignore)


@dataclass
class ReadFileState:
    # path -> (mtime_ns, char_count)。Day 4 的 read-before-edit 要靠它判断
    # "模型读过这个文件之后，文件在磁盘上是不是又被改过"。今天先只做记录。
    entries: dict[Path, tuple[int, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)   # Day 8 v4：并行读保护 entries

    def record(self, path: Path, content: str) -> None:
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return
        with self._lock:                      # 原来这行赋值直接裸写，现在包进锁
            self.entries[path] = (mtime_ns, len(content))


def resolve_in_cwd(cwd: Path, user_path: str) -> Path:
    # 把模型给的相对路径解析成绝对路径，并强制锁回 cwd 子树。
    # 越界直接抛错，由调用方包成 observation。
    candidate = (cwd / user_path).resolve()
    cwd_resolved = cwd.resolve()
    try:
        candidate.relative_to(cwd_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes cwd: {user_path}") from exc
    return candidate


def ensure_text_file(path: Path) -> None:
    # 白名单后缀直接放行；其余文件 peek 首 1 KB，看到 NUL 就当二进制拒绝。
    if path.suffix.lower() in TEXT_SUFFIXES:
        return
    with path.open("rb") as f:
        if b"\x00" in f.read(1024):
            raise ValueError(f"binary file: {path.name}")


def ensure_within_size(path: Path, max_bytes: int = MAX_READ_BYTES) -> None:
    # 整文件读取的硬上限。Day 3 不做 offset/limit，超限直接拒绝，
    # 课后挑战里再补按行读。
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"file too large: {size} bytes > {max_bytes}; "
            f"read a smaller file or use grep instead"
        )


def should_skip(rel_path: Path, policy: SkipPolicy) -> bool:
    if any(part in policy.skip_dirs for part in rel_path.parts):
        return True
    if policy.gitignore is not None and policy.gitignore.match_file(str(rel_path)):
        return True
    return False


def truncate_output(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated {len(text) - max_chars} chars]"


def load_gitignore(cwd: Path) -> pathspec.PathSpec | None:
    # 只读 cwd 根的 .gitignore；嵌套 gitignore 留作课后挑战。
    gitignore = cwd / ".gitignore"
    if not gitignore.exists():
        return None
    lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)

def ensure_read_before_edit(state: ReadFileState, path: Path) -> str | None:
    """检查文件是否在本次会话中被读过。没读过返回 error 字符串。"""
    if path not in state.entries:
        return (
            f"error: file has not been read yet. "
            f"Read {path.name} first before editing."
        )
    return None


def check_mtime_conflict(state: ReadFileState, path: Path) -> str | None:
    """检查文件在 read 之后是否被外部修改过。mtime 变了返回 error。
    教学版不做 content-equals 兜底——mtime 变就判冲突，再读一次即可。"""
    entry = state.entries.get(path)
    if entry is None:
        return None  # ensure_read_before_edit 会先拦住没读的情况
    read_mtime_ns, _ = entry
    try:
        current_mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    if current_mtime_ns > read_mtime_ns:
        return (
            f"error: file was modified after read. "
            f"Read {path.name} again before editing."
        )
    return None


def apply_single_replace(
    content: str, old: str, new: str, replace_all: bool
) -> tuple[str | None, str | None]:
    """在 content 中查找 old 并替换为 new。
    返回 (new_content, error)：成功时 error 为 None，失败时 new_content 为 None。"""
    if old == "":
        # str.count("") 会返回 len+1，str.replace("", x) 会在每个字符之间插入 x。
        # 这两个行为对模型完全没用，直接拒绝。
        return None, "error: old_string must not be empty."
    if old == new:
        return None, "error: old_string and new_string are exactly the same."

    count = content.count(old)
    if count == 0:
        return None, "error: string to replace not found in file."
    if count > 1 and not replace_all:
        return None, (
            f"error: found {count} matches for old_string. "
            f"Use replace_all=True to replace all, or make old_string more specific."
        )

    if replace_all:
        return content.replace(old, new), None
    else:
        return content.replace(old, new, 1), None