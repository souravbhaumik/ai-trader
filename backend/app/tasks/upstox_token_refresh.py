"""Upstox access token refresh task — runs daily at 7:30 AM IST.

Upstox access tokens are valid for one trading day (expire at midnight IST).
Since Upstox does not support a standard refresh_token grant, the user must
re-authorize once per day via the browser OAuth flow.

This task checks if the stored token will expire before end-of-day and:
  - If still valid: logs it and exits.
  - If expired/missing: sends a notification (Discord + email) prompting
    the user to re-authorize via GET /api/v1/broker-credentials/upstox/authorize.

This is the best we can do within Upstox's API constraints without storing
the user's login credentials (which we must not do).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import append_task_log, clear_task_logs, now_iso, write_task_status

logger = logging.getLogger(__name__)
_TASK = "upstox_token_refresh"


@celery_app.task(name="app.tasks.upstox_token_refresh.check_upstox_tokens")
def check_upstox_tokens():
    """Check all Upstox credentials and notify users if token is expiring."""
    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "Checking Upstox token validity.", started_at=started)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    # Warn if token expires within 2 hours from now
    warn_threshold = now_utc + timedelta(hours=2)

    with get_sync_session() as session:
        rows = session.execute(
            text("""
                SELECT bc.user_id, bc.api_key, bc.access_token,
                       bc.access_token_expires_at,
                       u.email, u.full_name
                FROM   broker_credentials bc
                JOIN   users u ON u.id = bc.user_id
                WHERE  bc.broker_name = 'upstox'
                  AND  bc.is_configured = TRUE
                  AND  bc.api_key IS NOT NULL
            """)
        ).fetchall()

    expired_users  = []
    expiring_users = []
    valid_users    = []

    for row in rows:
        exp = row.access_token_expires_at
        if not row.access_token or not exp:
            expired_users.append(row)
        elif exp <= now_utc:
            expired_users.append(row)
        elif exp <= warn_threshold:
            expiring_users.append(row)
        else:
            valid_users.append(row)

    append_task_log(_TASK, (
        f"Upstox tokens: {len(valid_users)} valid, "
        f"{len(expiring_users)} expiring soon, {len(expired_users)} expired."
    ))

    # Notify users with expired/expiring tokens
    for row in expired_users + expiring_users:
        status_word = "expired" if row in expired_users else "expiring soon"
        logger.warning(
            "upstox_token_%s", status_word,
            extra={"user_id": str(row.user_id), "email": row.email},
        )
        _notify_reauth_needed(
            email=row.email,
            full_name=row.full_name or row.email,
            status=status_word,
        )

    write_task_status(
        _TASK, "done",
        f"Token check complete. {len(expired_users)} users need re-authorization.",
        started_at=started, finished_at=now_iso(),
        summary={
            "valid": len(valid_users),
            "expiring": len(expiring_users),
            "expired": len(expired_users),
        },
    )
    return {
        "valid": len(valid_users),
        "expiring": len(expiring_users),
        "expired": len(expired_users),
    }


def _notify_reauth_needed(*, email: str, full_name: str, status: str) -> None:
    """Send Discord + email notification to re-authorize Upstox."""
    from app.core.config import settings

    # Discord notification
    try:
        from app.services.discord_service import notify_signal_sync as _ds
        import urllib.request, json

        url = settings.discord_webhook_url.strip()
        if url:
            payload = {
                "embeds": [{
                    "title": "⚠️ Upstox Token Re-Authorization Required",
                    "color": 0xF39C12,
                    "description": (
                        f"Your Upstox access token has **{status}**.\n\n"
                        f"Please visit: **Settings → Broker → Connect Upstox** "
                        f"to re-authorize and restore live market data."
                    ),
                    "footer": {"text": f"AI Trader · {full_name}"},
                }]
            }
            data = json.dumps(payload).encode()
            req  = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json", "User-Agent": "ai-trader/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("upstox_notify_discord_failed: %s", exc)

    # Email notification
    try:
        from app.services.email_service import send_email_sync
        send_email_sync(
            to=email,
            subject="Action Required: Re-authorize Upstox in AI Trader",
            body=(
                f"Hi {full_name},\n\n"
                f"Your Upstox access token has {status}.\n\n"
                f"Please log in to AI Trader and go to:\n"
                f"Settings → Broker → Connect Upstox\n\n"
                f"This keeps live market data flowing for intraday signal generation.\n\n"
                f"— AI Trader"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("upstox_notify_email_failed: %s", exc)
