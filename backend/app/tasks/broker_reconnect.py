"""Daily broker reconnect task.

Re-authenticates Angel One credentials with TOTP and caches JWT session in Redis
for cross-worker reuse.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import text

from app.brokers.angel_one import AngelOneAdapter
from app.core.database import get_sync_session
from app.core.security import decrypt_field
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import clear_task_logs, append_task_log, write_task_status, now_iso

logger = structlog.get_logger(__name__)


async def _reconnect_one(user_id: str, api_key: str, client_id: str, api_secret: str, totp_secret: str) -> tuple[bool, str]:
    adapter = AngelOneAdapter(
        api_key=decrypt_field(api_key),
        client_id=decrypt_field(client_id),
        password=decrypt_field(api_secret),
        totp_secret=decrypt_field(totp_secret),
    )
    try:
        await adapter.connect()
        if not adapter._smart_api or not adapter._auth_token:  # noqa: SLF001
            return False, "auth_failed"

        feed_token = getattr(adapter._smart_api, "feed_token", None)
        from app.core.redis_client import cache_broker_session  # noqa: PLC0415

        await cache_broker_session(
            user_id=user_id,
            broker_name="angel_one",
            jwt_token=adapter._auth_token,
            feed_token=feed_token,
        )
        return True, "ok"
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            await adapter.disconnect()
        except Exception:
            pass


@celery_app.task(name="app.tasks.broker_reconnect.refresh_broker_sessions", bind=True)
def refresh_broker_sessions(self) -> dict:
    task_name = "broker_reconnect"
    started_at = now_iso()
    clear_task_logs(task_name)
    write_task_status(task_name, "running", "Broker reconnect started", started_at=started_at)

    success = 0
    failed = 0

    with get_sync_session() as db:
        rows = db.execute(
            text(
                """
                SELECT user_id, api_key, client_id, api_secret, totp_secret
                FROM broker_credentials
                WHERE broker_name = 'angel_one'
                  AND is_configured = TRUE
                  AND api_key IS NOT NULL
                  AND client_id IS NOT NULL
                  AND api_secret IS NOT NULL
                  AND totp_secret IS NOT NULL
                """
            )
        ).fetchall()

    for row in rows:
        user_id = str(row[0])
        ok, info = asyncio.run(_reconnect_one(user_id, row[1], row[2], row[3], row[4]))
        if ok:
            success += 1
            append_task_log(task_name, f"Reconnected user {user_id}")
            logger.info("broker_reconnect.success", user_id=user_id)
        else:
            failed += 1
            append_task_log(task_name, f"Reconnect failed for user {user_id}: {info}", level="error")
            logger.warning("broker_reconnect.failed", user_id=user_id, err=info)

    finished_at = now_iso()
    summary = {
        "total": len(rows),
        "success": success,
        "failed": failed,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    write_task_status(
        task_name,
        "done" if failed == 0 else "error",
        f"Reconnect complete ({success} success, {failed} failed)",
        started_at=started_at,
        finished_at=finished_at,
        summary=summary,
    )
    return summary
