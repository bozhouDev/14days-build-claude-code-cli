from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PermissionRequest:
    """一次工具调用的权限请求。工具只描述意图，是否执行交给 harness 决定。"""
    tool_name: str
    args: dict
    mode: str
    cwd: Path


@dataclass
class PermissionDecision:
    """权限引擎的决策结果。behavior 是 allow / ask / deny 之一。"""
    behavior: str  # "allow" | "ask" | "deny"
    message: str | None = None  # deny 或 ask 时的说明文字


# 教学版危险命令列表。正则只是兜底，不是完整 shell sandbox。
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf", "rm -rf is destructive"),
    (r"rm\s+-fr", "rm -fr is destructive"),
    (r"\bsudo\b", "sudo grants root access"),
    (r"chmod\s+-R", "chmod -R is recursive permission change"),
    (r"(curl|wget).*\|.*(sh|bash|zsh)\b", "downloaded script execution may execute untrusted code"),
    (r"git\s+push\s+.*--force", "git push --force overwrites remote history"),
    (r"git\s+push\s+-f", "git push -f overwrites remote history"),
    (r"\bgit\s+push\b", "git push publishes local changes to a remote"),
    (r"git\s+reset\s+--hard", "git reset --hard discards uncommitted changes"),
]

_READONLY_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep", "project_tree",
    "git_status", "git_diff",
    "system_date", "echo",
    "memory_recall",  # Day 6：memdir 召回是纯读，进 readonly 没问题
})
# Day 6：写入范围被锁在 .agent/memory/ 的"低风险写"工具。
# default / acceptEdits 直接放行；plan 模式仍然 deny——plan 的硬约束就是只读。
_LOW_RISK_WRITES = frozenset({"memory_write"})

def _is_dangerous(command: str) -> str | None:
    """检查命令是否匹配危险模式。返回 None 表示安全，返回字符串表示危险原因。"""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


# 只读工具白名单——这些工具默认 allow，不走 ask 弹窗
_READONLY_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep", "project_tree",
    "git_status", "git_diff",
    "system_date", "echo",
})

# 交互和网络都不是写入，但仍需要用户知道 Agent 正在停下来问人或访问外部资源。
_ASK_TOOLS = frozenset({"ask_user_question", "web_fetch", "web_search"})


def decide_permission(request: PermissionRequest) -> PermissionDecision:
    """权限引擎入口：根据工具名、参数和当前模式决定 allow / ask / deny。"""
    tool_name = request.tool_name
    args = request.args
    mode = request.mode

    if tool_name in _ASK_TOOLS:
        return PermissionDecision("ask")

    # plan 模式：只允许只读工具；ask_user_question / web_* 仍会走 ask。
    # 写类工具一律 deny。
    if mode == "plan":
        if tool_name in _READONLY_TOOLS:
            return PermissionDecision("allow")
        if tool_name in _LOW_RISK_WRITES:
            return PermissionDecision("allow")
        return PermissionDecision(
            "deny",
            f"plan mode: {tool_name} is not allowed. Only read-only tools can run in plan mode.",
        )

    # 只读工具在所有模式下默认允许
    if tool_name in _READONLY_TOOLS:
        return PermissionDecision("allow")

    # bash 工具的危险命令检测——不管什么模式，危险命令先拦截
    if tool_name == "bash":
        command = args.get("command", "")
        danger_reason = _is_dangerous(command)
        if danger_reason:
            return PermissionDecision("deny", f"Dangerous command blocked: {danger_reason}")

    # acceptEdits 模式：文件编辑跳过确认 UI，但安全校验仍在 agent.py 做
    if mode == "acceptEdits":
        if tool_name in ("file_write", "file_edit"):
            return PermissionDecision("allow")

    # 默认模式：写文件和 bash 需要确认
    return PermissionDecision("ask")