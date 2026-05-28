from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue


class CronJob:
    """一个定时任务。id 是 12 位 hex，slash 是到点要重放的命令/prompt。"""

    def __init__(
        self,
        job_id: str,
        slash: str,
        every_seconds: int,
        label: str = "",
        last_run_at: str | None = None,
        created_at: str | None = None,
    ) -> None:
        self.id = job_id
        self.slash = slash
        self.every_seconds = every_seconds
        self.label = label
        self.last_run_at = last_run_at
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()


def _cron_path(cwd: Path) -> Path:
    agent_dir = cwd / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    return agent_dir / "cron.json"


def _load_jobs(cwd: Path) -> list[CronJob]:
    fpath = _cron_path(cwd)
    if not fpath.exists():
        return []
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    jobs: list[CronJob] = []
    for item in data.get("jobs", []):
        try:
            jobs.append(
                CronJob(
                    job_id=item["id"],
                    slash=item["slash"],
                    every_seconds=item["every_seconds"],
                    label=item.get("label", ""),
                    last_run_at=item.get("last_run_at"),
                    created_at=item.get("created_at"),
                )
            )
        except (KeyError, TypeError):
            continue
    return jobs


def _save_jobs(cwd: Path, jobs: list[CronJob]) -> None:
    fpath = _cron_path(cwd)
    data = {
        "jobs": [
            {
                "id": j.id,
                "slash": j.slash,
                "every_seconds": j.every_seconds,
                "label": j.label,
                "last_run_at": j.last_run_at,
                "created_at": j.created_at,
            }
            for j in jobs
        ]
    }
    fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class CronScheduler:
    """REPL 内的 cron 调度器：管理 job、后台 tick 和 pending queue。"""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._jobs: list[CronJob] = _load_jobs(cwd)
        self._pending: Queue[str] = Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def add_job(self, slash: str, every_seconds: int, label: str = "") -> CronJob:
        jid = uuid.uuid4().hex[:12]
        job = CronJob(job_id=jid, slash=slash, every_seconds=every_seconds, label=label)
        with self._lock:
            self._jobs.append(job)
            _save_jobs(self.cwd, self._jobs)
        return job

    def list_jobs(self) -> list[CronJob]:
        with self._lock:
            return list(self._jobs)

    def cancel_job(self, jid: str) -> bool:
        with self._lock:
            for i, job in enumerate(self._jobs):
                if job.id == jid:
                    self._jobs.pop(i)
                    _save_jobs(self.cwd, self._jobs)
                    return True
        return False

    def drain_pending(self) -> list[str]:
        items: list[str] = []
        while not self._pending.empty():
            try:
                items.append(self._pending.get_nowait())
            except Exception:
                break
        return items

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(1.0)
            if self._stop_event.is_set():
                break
            now_ts = datetime.now(timezone.utc).timestamp()
            dirty = False
            with self._lock:
                for job in self._jobs:
                    baseline = job.last_run_at or job.created_at
                    last_ts = 0.0
                    if baseline:
                        try:
                            last_dt = datetime.fromisoformat(baseline)
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=timezone.utc)
                            last_ts = last_dt.timestamp()
                        except ValueError:
                            pass
                    if now_ts - last_ts >= job.every_seconds:
                        self._pending.put(job.slash)
                        job.last_run_at = datetime.now(timezone.utc).isoformat()
                        dirty = True
                if dirty:
                    _save_jobs(self.cwd, self._jobs)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
