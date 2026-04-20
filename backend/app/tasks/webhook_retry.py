"""Celery task for retrying broker order-update webhook writes.

Used when a postback arrives before the originating FastAPI thread has committed
the live_orders INSERT (race condition with fast market order fills).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.webhook_retry.retry_order_update",
    max_retries=3,
    default_retry_delay=3,   # seconds between retries
)
def retry_order_update(
    self,
    *,
    broker_order_id: str,
    new_status: str,
    filled_qty: int,
    avg_price: float,
) -> None:
    """Write a broker order-status update to live_orders.

    Retried up to 3 times with 3-second gaps.  If the row still does not exist
    after all retries (truly orphaned postback), log a warning and give up.
    """
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    with get_sync_session() as session:
        row = session.execute(
            text("SELECT id FROM live_orders WHERE broker_order_id = :boid LIMIT 1"),
            {"boid": broker_order_id},
        ).first()

        if not row:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=3)
            logger.warning(
                "webhook_retry.order_not_found_after_retries",
                broker_order_id=broker_order_id,
                retries=self.request.retries,
            )
            return

        session.execute(
            text("""
                UPDATE live_orders
                SET    broker_status  = :bstat,
                       status         = :status,
                       filled_qty     = :fq,
                       avg_fill_price = :afp,
                       updated_at     = :now
                WHERE  broker_order_id = :boid
            """),
            {
                "bstat":  new_status,
                "status": new_status,
                "fq":     filled_qty,
                "afp":    avg_price,
                "now":    now_ts,
                "boid":   broker_order_id,
            },
        )
        session.commit()
        logger.info(
            "webhook_retry.order_updated",
            broker_order_id=broker_order_id,
            new_status=new_status,
            retry_attempt=self.request.retries,
        )
