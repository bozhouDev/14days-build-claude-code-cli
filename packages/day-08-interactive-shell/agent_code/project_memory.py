from __future__ import annotations

from pathlib import Path

# AGENT.md 大小上限：超过就截断保护，避免一份超长规则文件挤掉别的上下文
_MAX_AGENT_MD_BYTES = 50 * 1024


def load_agent_md(cwd: Path) -> str | None:
    """读取 cwd 下的 AGENT.md，包装成 <project-rules> 块。
    文件不存在返回 None——不是错误，只是没配置。"""
    agent_md = cwd / "AGENT.md"
    if not agent_md.exists():
        return None
    content = agent_md.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return None
    # 超过 50KB 就截到字节边界 + 一行提示，避免规则文件意外巨大爆 system prompt
    if len(content.encode("utf-8")) > _MAX_AGENT_MD_BYTES:
        truncated = content.encode("utf-8")[:_MAX_AGENT_MD_BYTES].decode("utf-8", errors="replace")
        content = truncated + "\n\n[... AGENT.md truncated at 50 KB ...]"
    # 用 XML 标签和核心 system prompt 隔开，让模型识别这是项目规则
    return f"<project-rules>\n{content}\n</project-rules>"