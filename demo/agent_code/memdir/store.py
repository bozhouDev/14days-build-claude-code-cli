from __future__ import annotations

from pathlib import Path

from .paths import ensure_memdir, get_memdir, index_path, topic_path, INDEX_MAX_LINES, INDEX_MAX_BYTES
from .types import MemoryEntry, MEMORY_TYPES, make_slug


def load_index(cwd: Path) -> str | None:
    """读取 MEMORY.md 索引文件。超过行数或字节上限时截断。
    文件不存在返回 None。"""
    ipath = index_path(cwd)
    if not ipath.exists():
        return None
    text = ipath.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return None
    # 截断保护：先按行数，再按字节数
    lines = text.splitlines()
    if len(lines) > INDEX_MAX_LINES:
        header = lines[:2] if lines[0].startswith("#") else []
        body = lines[2:] if header else lines
        # 保留头部 + 最新 200 行（尾部），丢掉中间
        lines = header + body[-(INDEX_MAX_LINES - len(header)):]
        text = "\n".join(lines)
    text_bytes = text.encode("utf-8")
    if len(text_bytes) > INDEX_MAX_BYTES:
        # 在字节边界截断：从后往前找最后一个完整 UTF-8 字符的换行
        truncated = text_bytes[:INDEX_MAX_BYTES].decode("utf-8", errors="replace")
        last_nl = truncated.rfind("\n")
        text = truncated[:last_nl] if last_nl > 0 else truncated
    return text


def write_memory(cwd: Path, mem_type: str, title: str, body: str) -> MemoryEntry:
    """写入一条长期记忆。同时做两件事：
    1. 写 .agent/memory/<type>/<slug>.md（带 frontmatter）
    2. 在 MEMORY.md 索引末尾追加一行引用

    两步放在同一个函数里走，正常路径会一起完成；如果中途索引更新失败，
    topic 文件已经写入，仍能被 recall 时的 scan 找到，不会丢记忆。"""
    if mem_type not in MEMORY_TYPES:
        raise ValueError(f"unknown memory type: {mem_type}, expected one of {MEMORY_TYPES}")

    ensure_memdir(cwd)
    slug = make_slug(title)

    # 防文件名冲突：如果 slug 已存在，加数字后缀
    tpath = topic_path(cwd, mem_type, slug)
    counter = 1
    while tpath.exists():
        tpath = topic_path(cwd, mem_type, f"{slug}-{counter}")
        counter += 1

    # 写 topic 文件——frontmatter 用 type + title 两个字段：
    #   type 是必填，决定文件归属哪个子目录、recall 时是否过滤
    #   title 是给人看的标题，也用来生成索引行的链接文本
    # 索引里的 hook 直接从 body 前 60 字派生，所以 topic 文件本身只需要这两个字段
    frontmatter = f"---\ntype: {mem_type}\ntitle: {title}\n---\n\n"
    tpath.write_text(frontmatter + body, encoding="utf-8")

    # 生成一句 hook：取 body 的前 60 个字符，截到最后一个完整词
    hook = body.strip()[:60]
    if len(body.strip()) > 60:
        hook = hook[:hook.rfind(" ")] + "..."

    # 追加索引行
    ipath = index_path(cwd)
    index_line = f"- [{title}]({mem_type}/{tpath.name}) — {hook}\n"
    if not ipath.exists():
        ipath.write_text("# Memory Index\n\n" + index_line, encoding="utf-8")
    else:
        with open(ipath, "a", encoding="utf-8") as f:
            f.write(index_line)

    return MemoryEntry(
        mem_type=mem_type,
        title=title,
        slug=tpath.stem,
        body=body,
        file_path=str(tpath.relative_to(cwd)),
    )


def recall_memory(cwd: Path, query: str, top_k: int = 5) -> list[MemoryEntry]:
    """关键字召回：扫描四个子目录下所有 .md 文件，把 query 按空格拆成
    keyword 列表，每个 keyword 在 title + body 里命中就加一分（不区分大小写）。
    按总分降序、同分按 mtime 倒序，返回 top_k 条匹配。"""
    # recall_memory 是纯只读——目录不存在时直接返回空，不要 mkdir，
    # 否则 plan 模式（只读硬约束）下调用 recall 会偷偷创建目录
    memdir = get_memdir(cwd)
    if not memdir.is_dir():
        return []
    keywords = query.lower().split()
    if not keywords:
        return []

    # (score, mtime, entry) 三元组：score 高的优先，同分按 mtime 新优先
    scored: list[tuple[float, float, MemoryEntry]] = []
    for mtype in MEMORY_TYPES:
        type_dir = memdir / mtype
        if not type_dir.is_dir():
            continue
        for md_file in type_dir.glob("*.md"):
            text = md_file.read_text(encoding="utf-8", errors="replace")
            text_lower = text.lower()
            # 简单 keyword match——每个 keyword 出现就加 1 分
            score = sum(1 for kw in keywords if kw in text_lower)
            if score == 0:
                continue
            # 解析 frontmatter 里的 title（取 --- 之间的 title: 行）
            title = md_file.stem.replace("-", " ").title()
            body = text
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].splitlines():
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip()
                    body = parts[2].strip()
            scored.append((
                score / len(keywords),  # 归一化到 0-1
                md_file.stat().st_mtime,
                MemoryEntry(
                    mem_type=mtype,
                    title=title,
                    slug=md_file.stem,
                    body=body,
                    file_path=str(md_file.relative_to(cwd)),
                )
            ))

    # 元组比较：先比 score 再比 mtime，两者都降序——分数高的优先，同分时新写的优先
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [entry for _, _, entry in scored[:top_k]]
