from __future__ import annotations

from typing import Any

from .tools import ToolContext


_scheduler: Any = None


def set_scheduler(scheduler: Any) -> None:
    """cli.py 在创建 CronScheduler 后调用，让工具函数访问同一个实例。"""
    global _scheduler
    _scheduler = scheduler


def _get_scheduler(ctx: ToolContext) -> Any:
    if _scheduler is not None:
        return _scheduler
    from .scheduler import CronScheduler

    return CronScheduler(ctx.cwd)


def cron_create(args: dict[str, Any], ctx: ToolContext) -> str:
    scheduler = _get_scheduler(ctx)
    slash = args.get("slash", "")
    every_seconds = int(args.get("every_seconds", 0))
    label = args.get("label", "")
    if not slash:
        return "error: missing required argument 'slash'"
    if every_seconds <= 0:
        return "error: every_seconds must be positive"
    job = scheduler.add_job(slash, every_seconds, label)
    return f"Cron job created: {job.id} — every {every_seconds}s: {slash}"


def cron_list(args: dict[str, Any], ctx: ToolContext) -> str:
    scheduler = _get_scheduler(ctx)
    jobs = scheduler.list_jobs()
    if not jobs:
        return "(no cron jobs)"
    lines = []
    for job in jobs:
        last = job.last_run_at or "never"
        label = f" — {job.label}" if job.label else ""
        lines.append(f"  [{job.id}] every {job.every_seconds}s: {job.slash}{label}  (last: {last})")
    return "\n".join(lines)


def cron_cancel(args: dict[str, Any], ctx: ToolContext) -> str:
    scheduler = _get_scheduler(ctx)
    jid = args.get("id", "")
    if not jid:
        return "error: missing required argument 'id'"
    if scheduler.cancel_job(jid):
        return f"Cron job cancelled: {jid}"
    return f"error: job not found: {jid}"
