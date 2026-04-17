"""Broker adapter factory.

Resolves the correct BrokerAdapter for a given user based on their
`preferred_broker` setting and their stored (decrypted) credentials.
"""
from __future__ import annotations

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.brokers.base import BrokerAdapter
from app.brokers.yfinance_adapter import YFinanceAdapter

logger = structlog.get_logger(__name__)


async def get_adapter_for_user(
    user_id: str,
    preferred_broker: Optional[str],
    db: AsyncSession,
) -> BrokerAdapter:
    """Return the correct BrokerAdapter for the user.

    Priority:
      1. Use preferred_broker from user_settings.
      2. If credentials not configured for that broker, fall back to yfinance
         with a warning flag (caller can surface this in the API response).
      3. Default to yfinance if preferred_broker is None / 'yfinance'.
    """
    if not preferred_broker or preferred_broker == "yfinance":
        return YFinanceAdapter()

    creds = await _load_credentials(user_id, preferred_broker, db)

    if preferred_broker == "angel_one":
        from app.brokers.angel_one import AngelOneAdapter
        from app.core.security import decrypt_field

        adapter = AngelOneAdapter(
            api_key=decrypt_field(creds.api_key) if creds and creds.api_key else None,
            client_id=decrypt_field(creds.client_id) if creds and creds.client_id else None,
            password=decrypt_field(creds.api_secret) if creds and creds.api_secret else None,
            totp_secret=decrypt_field(creds.totp_secret) if creds and creds.totp_secret else None,
        )
        if not adapter.is_credentials_configured():
            logger.info("angel_one_not_configured_fallback_yfinance", user_id=str(user_id))
        return adapter

    if preferred_broker == "upstox":
        from app.brokers.upstox import UpstoxAdapter
        from app.core.security import decrypt_field

        adapter = UpstoxAdapter(
            api_key=decrypt_field(creds.api_key) if creds and creds.api_key else None,
            api_secret=decrypt_field(creds.api_secret) if creds and creds.api_secret else None,
            access_token=decrypt_field(creds.totp_secret) if creds and creds.totp_secret else None,
        )
        if not adapter.is_credentials_configured():
            logger.info("upstox_not_configured_fallback_yfinance", user_id=str(user_id))
        return adapter

    # Unknown broker — fallback to yfinance
    logger.warning("unknown_broker_fallback", broker=preferred_broker)
    return YFinanceAdapter()


async def _load_credentials(user_id: str, broker_name: str, db: AsyncSession):
    """Load broker credentials row from DB (or return None)."""
    from sqlmodel import select
    from app.models.broker_credential import BrokerCredential

    try:
        result = await db.execute(
            select(BrokerCredential).where(
                BrokerCredential.user_id == user_id,
                BrokerCredential.broker_name == broker_name,
            )
        )
        return result.scalar_one_or_none()
    except Exception as e:
        logger.error("load_credentials_failed", error=str(e))
        return None
