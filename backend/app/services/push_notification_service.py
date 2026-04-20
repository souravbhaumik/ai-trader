"""Expo push notification sender service."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)
_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_signal_alert(
    session: AsyncSession,
    *,
    user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> int:
    """Send push alert to all active Expo tokens for the user.

    Returns number of tokens attempted.
    """
    rows = await session.execute(
        text(
            """
            SELECT token
            FROM expo_push_tokens
            WHERE user_id = :uid
              AND is_active = TRUE
            """
        ),
        {"uid": user_id},
    )
    tokens = [r[0] for r in rows.fetchall()]
    if not tokens:
        return 0

    payload = [
        {
            "to": tok,
            "title": title,
            "body": body,
            "sound": "default",
            "data": data or {},
        }
        for tok in tokens
    ]

    invalid_tokens: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_EXPO_PUSH_URL, json=payload)
            resp.raise_for_status()
            out = resp.json().get("data", [])
            for i, item in enumerate(out):
                if item.get("status") == "error":
                    details = item.get("details") or {}
                    if details.get("error") == "DeviceNotRegistered":
                        invalid_tokens.add(tokens[i])
    except Exception as exc:
        logger.warning("push.send_failed", user_id=user_id, err=str(exc))

    if invalid_tokens:
        now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.execute(
            text(
                """
                UPDATE expo_push_tokens
                SET is_active = FALSE,
                    tbl_last_dt = :now
                WHERE token = ANY(:tokens)
                """
            ),
            {"tokens": list(invalid_tokens), "now": now_ts},
        )
        await session.commit()

    return len(tokens)
