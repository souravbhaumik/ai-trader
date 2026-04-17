"""Shared utilities for Celery tasks.

Provides a thread-safe helper to write task execution status to Redis so the
admin panel can display live pipeline state without polling Celery's result
backend directly.

Redis key format:  pipeline:status:{task_name}
Value (JSON):
  {
    "status":   "running" | "done" | "error" | "idle",
    "message":  "Human-readable summary",
    "started_at": "ISO datetime | null",
    "finished_at": "ISO datetime | null",
    "summary":  { ...task-specific counts/metadata }
  }

Keys expire after 7 days so stale data doesn't accumulate.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Literal, Optional

import structlog

logger = structlog.get_logger(__name__)

_TTL_SECONDS = 7 * 24 * 3600   # 7 days
_KEY_PREFIX  = "pipeline:status:"

TaskStatus = Literal["running", "done", "error", "idle"]


def write_task_status(
    task_name: str,
    status: TaskStatus,
    message: str,
    *,
    started_at:  Optional[str] = None,
    finished_at: Optional[str] = None,
    summary:     Optional[Dict[str, Any]] = None,
) -> None:
    """Write *task_name* execution status to Redis.

    Silently swallows all errors — a status-write failure must never abort
    the actual task.
    """
    try:
        import redis as _redis
        from app.core.config import settings

        r = _redis.from_url(settings.redis_url, decode_responses=True)
        payload: Dict[str, Any] = {
            "status":      status,
            "message":     message,
            "started_at":  started_at,
            "finished_at": finished_at,
            "summary":     summary or {},
            "ts":          datetime.now().isoformat(),
        }
        r.set(f"{_KEY_PREFIX}{task_name}", json.dumps(payload), ex=_TTL_SECONDS)
    except Exception as exc:
        logger.warning("task_utils.status_write_failed", task=task_name, error=str(exc))


def now_iso() -> str:
    return datetime.now().isoformat()


def read_all_task_statuses(task_names: list[str]) -> list[Dict[str, Any]]:
    """Read status entries for the given task names from Redis.

    Returns a list in the same order as *task_names*, substituting an 'idle'
    entry for any key that doesn't exist yet.
    """
    try:
        import redis as _redis
        from app.core.config import settings

        r   = _redis.from_url(settings.redis_url, decode_responses=True)
        out = []
        for name in task_names:
            raw = r.get(f"{_KEY_PREFIX}{name}")
            if raw:
                try:
                    data = json.loads(raw)
                    data["task_name"] = name
                    out.append(data)
                    continue
                except json.JSONDecodeError:
                    pass
            out.append({
                "task_name":   name,
                "status":      "idle",
                "message":     "Never run.",
                "started_at":  None,
                "finished_at": None,
                "summary":     {},
                "ts":          None,
            })
        return out
    except Exception as exc:
        logger.warning("task_utils.read_all_failed", error=str(exc))
        return [
            {"task_name": n, "status": "idle", "message": "Redis unavailable.",
             "started_at": None, "finished_at": None, "summary": {}, "ts": None}
            for n in task_names
        ]
