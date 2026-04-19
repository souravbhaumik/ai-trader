"""EOD live-order reconciliation task.

Runs at 4:00 PM IST Mon–Fri (16 minutes before market close so any open orders
can still be cancelled if needed).

For each live_order that is still PENDING or OPEN at EOD:
  1. Ask the broker for the current order status via ``get_order_status()``.
  2. If the broker status differs from our record, update the row.
  3. Emit a WARNING-level log for every mismatch so ops can investigate.

The task is synchronous (Celery runs it in a forked process) and uses
``asyncio.run()`` to call the async broker adapter.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_OPEN_STATUSES = ("PENDING", "OPEN")


@celery_app.task(name="app.tasks.eod_reconciliation.reconcile_live_orders", bind=True)
def reconcile_live_orders(self) -> dict:
    """Query all open live_orders and reconcile status with broker."""
    logger.info("eod_reconciliation.started")

    reconciled = 0
    mismatches = 0

    with get_sync_session() as session:
        result = session.execute(
            text("""
                SELECT lo.id, lo.broker_order_id, lo.status, lo.user_id,
                       us.preferred_broker
                FROM   live_orders lo
                JOIN   user_settings us ON us.user_id = lo.user_id
                WHERE  lo.status = ANY(:statuses)
                  AND  lo.broker_order_id IS NOT NULL
                  AND  lo.broker_order_id != ''
            """),
            {"statuses": list(_OPEN_STATUSES)},
        )
        rows = result.fetchall()

    logger.info("eod_reconciliation.found_open_orders", count=len(rows))

    for row in rows:
        order_id        = str(row.id)
        broker_order_id = row.broker_order_id
        local_status    = row.status
        user_id         = str(row.user_id)
        preferred_broker = row.preferred_broker or "yfinance"

        try:
            broker_result = asyncio.run(
                _fetch_order_status(user_id, preferred_broker, broker_order_id)
            )
        except Exception as exc:
            logger.error(
                "eod_reconciliation.broker_query_failed",
                order_id=order_id,
                broker_order_id=broker_order_id,
                error=str(exc),
            )
            continue

        if broker_result is None:
            logger.warning(
                "eod_reconciliation.order_not_found_at_broker",
                order_id=order_id,
                broker_order_id=broker_order_id,
            )
            continue

        broker_status = broker_result.status.upper()
        reconciled += 1

        if broker_status != local_status.upper():
            mismatches += 1
            logger.warning(
                "eod_reconciliation.status_mismatch",
                order_id=order_id,
                broker_order_id=broker_order_id,
                local_status=local_status,
                broker_status=broker_status,
            )
            now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
            with get_sync_session() as session:
                session.execute(
                    text("""
                        UPDATE live_orders
                        SET    status         = :status,
                               broker_status  = :bstat,
                               filled_qty     = :fq,
                               avg_fill_price = :afp,
                               updated_at     = :now
                        WHERE  id = :id
                    """),
                    {
                        "status": broker_status,
                        "bstat":  broker_status,
                        "fq":     broker_result.filled_qty if hasattr(broker_result, "filled_qty") else 0,
                        "afp":    broker_result.avg_price if hasattr(broker_result, "avg_price") else 0,
                        "now":    now_ts,
                        "id":     order_id,
                    },
                )
                session.commit()

    logger.info(
        "eod_reconciliation.completed",
        total_open=len(rows),
        reconciled=reconciled,
        mismatches=mismatches,
    )
    return {"total_open": len(rows), "reconciled": reconciled, "mismatches": mismatches}


async def _fetch_order_status(user_id: str, preferred_broker: str, broker_order_id: str):
    """Async helper: build adapter and query order status."""
    from app.core.database import get_session as get_async_session  # noqa: PLC0415

    async for db in get_async_session():
        from app.brokers.factory import get_adapter_for_user  # noqa: PLC0415
        adapter = await get_adapter_for_user(user_id, preferred_broker, db)
        try:
            await adapter.connect()
            result = await adapter.get_order_status(broker_order_id)
        finally:
            try:
                await adapter.disconnect()
            except Exception:
                pass
        return result

    return None
