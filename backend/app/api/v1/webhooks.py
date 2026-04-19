"""Broker postback webhook — Angel One order-update notifications.

Angel One POSTs order status changes to this endpoint.
The callback URL must be registered in the Angel One developer portal.

Race-condition safety
---------------------
Market orders execute so fast that the postback can arrive *before* the FastAPI
thread that placed the order has committed the ``live_orders`` row.  If we look
up by ``broker_order_id`` and find nothing we push a Celery task with a 3-second
countdown to retry the DB write — by which time the original row will be committed.

Security
--------
Angel One does not currently send an HMAC signature for postbacks, but the
endpoint validates that:
  1. The JSON body has the required fields.
  2. The ``broker_order_id`` is a non-empty string (basic sanity check).
  3. The status value is one of the known broker statuses.

When Angel One adds HMAC signing, add the signature check at the top of the handler.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_KNOWN_STATUSES = {
    "open", "complete", "cancelled", "rejected", "pending",
    "OPEN", "COMPLETE", "CANCELLED", "REJECTED", "PENDING",
}


class OrderUpdatePayload(BaseModel):
    """Angel One postback order-update shape."""
    orderid: str                          # broker order ID
    status: str
    filledshares: Optional[str] = None    # may be a string "0"
    averageprice: Optional[str] = None
    text: Optional[str] = None            # rejection reason

    @field_validator("orderid")
    @classmethod
    def order_id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("orderid must not be empty")
        return v.strip()


@router.post("/order-update", status_code=status.HTTP_200_OK)
async def broker_order_update(request: Request):
    """Receive broker order-status postback and update live_orders table.

    If the live_order row is not found (race condition — postback arrived before
    the originating FastAPI thread committed the INSERT), we schedule a Celery
    retry task with a 3-second countdown so the retry runs after the commit.
    """
    try:
        raw: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body")

    # Parse and validate
    try:
        payload = OrderUpdatePayload(**raw)
    except Exception as exc:
        logger.warning("webhook.invalid_payload", error=str(exc), raw=raw)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid payload: {exc}")

    broker_order_id = payload.orderid
    new_status      = payload.status.upper()
    filled_qty      = int(payload.filledshares or 0)
    avg_price       = float(payload.averageprice or 0.0)

    logger.info(
        "webhook.order_update_received",
        broker_order_id=broker_order_id,
        status=new_status,
    )

    # Try to update synchronously first
    updated = await _update_order(broker_order_id, new_status, filled_qty, avg_price)

    if not updated:
        # Race condition: row not found yet — schedule a delayed retry via Celery
        logger.warning(
            "webhook.order_not_found_scheduling_retry",
            broker_order_id=broker_order_id,
        )
        try:
            from app.tasks.webhook_retry import retry_order_update  # noqa: PLC0415
            retry_order_update.apply_async(
                kwargs={
                    "broker_order_id": broker_order_id,
                    "new_status":      new_status,
                    "filled_qty":      filled_qty,
                    "avg_price":       avg_price,
                },
                countdown=3,   # wait 3 s for the originating transaction to commit
            )
        except Exception as exc:
            logger.error("webhook.retry_schedule_failed", error=str(exc))

    # Always return 200 — broker will not retry on non-2xx (varies by provider)
    return {"received": True}


async def _update_order(
    broker_order_id: str,
    new_status: str,
    filled_qty: int,
    avg_price: float,
) -> bool:
    """Update live_orders row.  Returns True if the row was found and updated."""
    from sqlalchemy import text  # noqa: PLC0415
    from app.core.database import get_session  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    # get_session is an async context manager that yields an AsyncSession
    async for db in get_session():
        result = await db.execute(
            text("SELECT id FROM live_orders WHERE broker_order_id = :boid LIMIT 1"),
            {"boid": broker_order_id},
        )
        row = result.first()
        if not row:
            return False

        await db.execute(
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
        await db.commit()
        logger.info(
            "webhook.order_updated",
            broker_order_id=broker_order_id,
            new_status=new_status,
        )
        return True

    return False  # noqa: unreachable — for type checker
