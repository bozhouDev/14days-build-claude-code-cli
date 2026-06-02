from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# 四种记忆类型：user 是用户本身的事实，feedback 是用户反馈的做法，
# project 是项目里此刻在做什么，reference 是外部系统指针。
MEMORY_TYPES = ("user", "feedback", "project", "reference")


@dataclass
class MemoryEntry:
    """一条记忆的完整数据。"""
    mem_type: str       # user / feedback / project / reference
    title: str          # 人类可读标题
    slug: str           # 文件名安全标识
    body: str           # 正文（markdown）
    file_path: str      # 相对于 cwd 的路径，如 .agent/memory/user/my-role.md


def make_slug(title: str, max_len: int = 64) -> str:
    """把 title 转成文件名安全的 slug：只留 ASCII 字母数字 + 短横。
    title 是纯中文/纯日文等非 ASCII 内容时退到 hash slug，保证跨平台稳定。"""
    slug = title.lower().strip()
    # 只保留 a-z / 0-9 / 空格 / 短横；中文、表情等都会被丢掉
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug.strip("-")[:max_len]
    if not slug:
        # title 完全没 ASCII（例如纯中文）时，用 sha1 前 8 位兜底
        slug = "mem-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return slug