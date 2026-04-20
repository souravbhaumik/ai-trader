"""Shared utilities for Celery tasks.

Task *status* (running / done / error / idle) is persisted in the
``pipeline_task_status`` PostgreSQL table — one row per task, upserted on
every transition.  This survives Redis restarts and never goes stale after a
worker crash: the API layer detects any row stuck in ``running`` for more
than 1 hour and surfaces it as ``error``.

Task *logs* remain in Redis (ephemeral, capped list) — they are only needed
for live viewing and do not need to be durable.

Log list key format:  pipeline:logs:{task_name}
  A Redis list of JSON strings, each:
  { "ts": "ISO", "level": "info"|"error"|"warn", "msg": "..." }
  Capped at _LOG_CAP entries. TTL = 7 days.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, Literal, Optional

import structlog

logger = structlog.get_logger(__name__)

_LOG_PREFIX  = "pipeline:logs:"
_LOG_CAP     = 500              # max log lines kept per task
_LOG_TTL     = 7 * 24 * 3600   # 7 days

TaskStatus = Literal["running", "done", "error", "idle", "unknown"]

# ── Sync SQLAlchemy engine (used by Celery worker processes) ──────────────────
_sync_engine      = None
_sync_engine_lock = threading.Lock()


def _get_sync_engine():
    """Return a module-level SQLAlchemy sync engine (created once per process)."""
    global _sync_engine
    if _sync_engine is None:
        with _sync_engine_lock:
            if _sync_engine is None:
                from sqlalchemy import create_engine
                from app.core.config import settings
                _sync_engine = create_engine(
                    settings.sync_database_url,
                    pool_pre_ping=True,
                    pool_size=2,
                    max_overflow=2,
                )
    return _sync_engine


def _get_redis():
    import redis as _redis
    from app.core.config import settings
    return _redis.from_url(settings.redis_url, decode_responses=True)


def write_task_status(
    task_name: str,
    status: TaskStatus,
    message: str,
    *,
    started_at:  Optional[str] = None,
    finished_at: Optional[str] = None,
    summary:     Optional[Dict[str, Any]] = None,
) -> None:
    """Upsert *task_name* execution status into the pipeline_task_status DB table.

    Also appends the message as a log entry to Redis.
    Silently swallows all errors — a status-write failure must never abort
    the actual task.
    """
    try:
        from sqlalchemy import text
        engine = _get_sync_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_task_status
                        (task_name, status, message, started_at, finished_at, summary, updated_at)
                    VALUES
                        (:task_name, :status, :message, :started_at, :finished_at,
                         CAST(:summary AS jsonb), NOW())
                    ON CONFLICT (task_name) DO UPDATE SET
                        status      = EXCLUDED.status,
                        message     = EXCLUDED.message,
                        started_at  = COALESCE(EXCLUDED.started_at, pipeline_task_status.started_at),
                        finished_at = EXCLUDED.finished_at,
                        summary     = EXCLUDED.summary,
                        updated_at  = NOW()
                """),
                {
                    "task_name":  task_name,
                    "status":     status,
                    "message":    message,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "summary":    json.dumps(summary or {}),
                },
            )
    except Exception as exc:
        logger.warning("task_utils.status_write_failed", task=task_name, err=str(exc))

    # Always append to the Redis log list as well (best-effort)
    try:
        r = _get_redis()
        _push_log(r, task_name, message, level="error" if status == "error" else "info")
    except Exception as exc:
        logger.warning("task_utils.log_append_failed", task=task_name, err=str(exc))


def append_task_log(task_name: str, msg: str, level: str = "info") -> None:
    """Append a single log line to pipeline:logs:{task_name}.

    Silently swallows errors — never block the task.
    """
    try:
        r = _get_redis()
        _push_log(r, task_name, msg, level=level)
    except Exception as exc:
        logger.warning("task_utils.log_append_failed", task=task_name, err=str(exc))


def clear_task_logs(task_name: str) -> None:
    """Delete the log list for *task_name* so a fresh run starts clean."""
    try:
        r = _get_redis()
        r.delete(f"{_LOG_PREFIX}{task_name}")
    except Exception as exc:
        logger.warning("task_utils.log_clear_failed", task=task_name, err=str(exc))


def read_task_logs(task_name: str, limit: int = 200) -> list[Dict[str, Any]]:
    """Return the last *limit* log entries for *task_name* (oldest-first)."""
    try:
        r = _get_redis()
        key = f"{_LOG_PREFIX}{task_name}"
        # LRANGE returns oldest→newest (RPUSH appends to tail)
        raw_list = r.lrange(key, -limit, -1)
        out = []
        for raw in raw_list:
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                out.append({"ts": "", "level": "info", "msg": raw})
        return out
    except Exception as exc:
        logger.warning("task_utils.log_read_failed", task=task_name, err=str(exc))
        return []


def _push_log(r: Any, task_name: str, msg: str, level: str = "info") -> None:
    """Internal: RPUSH one log entry and trim to cap."""
    key = f"{_LOG_PREFIX}{task_name}"
    entry = json.dumps({"ts": datetime.now().isoformat(), "level": level, "msg": msg})
    r.rpush(key, entry)
    r.ltrim(key, -_LOG_CAP, -1)
    r.expire(key, _LOG_TTL)


def now_iso() -> str:
    return datetime.now().isoformat()


def reset_interrupted_tasks() -> None:
    """On application startup, flip any 'running' rows to 'unknown'.

    A task left in 'running' at startup means the worker was killed before it
    could write a final 'done'/'error' status.  We cannot know the real
    outcome so 'unknown' is the honest state.
    Silently swallows errors so a DB hiccup never blocks startup.
    """
    try:
        from sqlalchemy import text
        engine = _get_sync_engine()
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE pipeline_task_status
                    SET    status     = 'unknown',
                           message    = 'Status unknown — application restarted while task was running.',
                           updated_at = NOW()
                    WHERE  status = 'running'
                      AND  finished_at IS NULL
                    RETURNING task_name
                """)
            )
            affected = [row[0] for row in result.fetchall()]
        if affected:
            logger.info("task_utils.reset_interrupted", tasks=affected)
    except Exception as exc:
        logger.warning("task_utils.reset_interrupted_failed", err=str(exc))


def read_all_task_statuses(task_names: list[str]) -> list[Dict[str, Any]]:
    """Read status entries for the given task names from the DB (sync).

    Returns a list in the same order as *task_names*, substituting an 'idle'
    entry for any task not yet in the table.
    """
    try:
        from sqlalchemy import text
        engine = _get_sync_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT
                        task_name,
                        status,
                        message,
                        started_at,
                        finished_at,
                        summary,
                        updated_at
                    FROM pipeline_task_status
                    WHERE task_name = ANY(:names)
                """),
                {"names": task_names},
            ).fetchall()

        by_name = {}
        for row in rows:
            by_name[row[0]] = {
                "task_name":   row[0],
                "status":      row[1],
                "message":     row[2] or "",
                "started_at":  row[3].isoformat() if row[3] else None,
                "finished_at": row[4].isoformat() if row[4] else None,
                "summary":     row[5] or {},
                "ts":          row[6].isoformat() if row[6] else None,
            }

        _idle = lambda n: {
            "task_name": n, "status": "idle", "message": "Never run.",
            "started_at": None, "finished_at": None, "summary": {}, "ts": None,
        }
        return [by_name.get(n, _idle(n)) for n in task_names]
    except Exception as exc:
        logger.warning("task_utils.read_all_failed", err=str(exc))
        return [
            {"task_name": n, "status": "idle", "message": "DB unavailable.",
             "started_at": None, "finished_at": None, "summary": {}, "ts": None}
            for n in task_names
        ]
