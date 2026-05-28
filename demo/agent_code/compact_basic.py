from __future__ import annotations

from typing import Any


def compact(messages: list[dict[str, Any]], keep: int = 8) -> list[dict[str, Any]]:
    """确定性压缩消息历史。不调 LLM。

    返回三段拼接：
    1. pinned: 前 2 条（任务定义，不能丢）
    2. compressed: 一条概括中间消息的摘要 user message
    3. working: 最后 keep 条（最近的交互，保持完整）

    如果消息总数 <= keep + 2，不做压缩，直接返回原列表。"""
    pin_count = 2
    if len(messages) <= keep + pin_count:
        return messages  # 消息还不够多，不需要压缩

    pinned = messages[:pin_count]
    working = messages[-keep:]
    middle = messages[pin_count:-keep]
    compressed = _build_compressed_block(middle)
    return pinned + [compressed] + working


def _build_compressed_block(msgs: list[dict[str, Any]]) -> dict[str, Any]:
    """扫描被压缩的消息，提取结构化统计。
    如果 msgs 里包含上一轮 compact 留下的 <compacted-history> 块，保留为
    <previous-summary> 子块——重复 compact 时早期信息才不会丢。"""
    total = len(msgs)
    tool_names: set[str] = set()
    tool_count = 0
    files_read: set[str] = set()
    files_edited: set[str] = set()
    commands: list[str] = []
    previous_summary: str | None = None

    for msg in msgs:
        content = msg.get("content")
        # 识别上一轮 compact 自己写进来的 <compacted-history> 块，单独保留
        if isinstance(content, str) and content.startswith("<compacted-history>"):
            previous_summary = content
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                # assistant 消息里的工具调用请求
                tool_count += 1
                name = block.get("name", "")
                if name:
                    tool_names.add(name)
                args = block.get("input", {}) or {}
                # 分类统计文件操作和命令
                if name in ("read_file",) and args.get("path"):
                    files_read.add(str(args["path"]))
                elif name in ("file_write", "file_edit") and args.get("file_path"):
                    files_edited.add(str(args["file_path"]))
                elif name == "bash" and args.get("command"):
                    cmd = str(args["command"])
                    commands.append(cmd[:80] + "..." if len(cmd) > 80 else cmd)
            elif btype == "tool_result":
                # user 消息里的工具结果——不额外统计，tools-used 已经覆盖
                pass

    lines = ["<compacted-history>"]
    # 把上一轮摘要原文嵌进来——读者能直观看到"这是第 N 轮压缩，前面的事还在"
    if previous_summary:
        lines.append("  <previous-summary>")
        for ln in previous_summary.splitlines():
            lines.append("    " + ln)
        lines.append("  </previous-summary>")
    lines.extend([
        f"  <message-count>{total}</message-count>",
        f"  <tool-calls>{tool_count}</tool-calls>",
        f"  <tools-used>{', '.join(sorted(tool_names)) if tool_names else '(none)'}</tools-used>",
        f"  <files-read>{', '.join(sorted(files_read)) if files_read else '(none)'}</files-read>",
        f"  <files-edited>{', '.join(sorted(files_edited)) if files_edited else '(none)'}</files-edited>",
        f"  <commands-run>{', '.join(commands[:20]) if commands else '(none)'}</commands-run>",
        f"  <conclusions>(not yet supported — see Day 11)</conclusions>",
        "</compacted-history>",
    ])
    return {"role": "user", "content": "\n".join(lines)}
