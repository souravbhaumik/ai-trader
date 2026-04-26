"""Broker adapter factory.

Resolves the correct BrokerAdapter for a given user based on their
`preferred_broker` setting and their stored (decrypted) credentials.

Session caching: Angel One sessions (JWT) are cached in Redis with a 23-hour
TTL to avoid re-authenticating via TOTP on every API request.

No yfinance fallback for live data paths — raises ValueError if no broker is
configured so callers can show a clear error message to the user.
"""
from __future__ import annotations

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.brokers.base import BrokerAdapter

logger = structlog.get_logger(__name__)


async def get_adapter_for_user(
    user_id: str,
    preferred_broker: Optional[str],
    db: AsyncSession,
) -> BrokerAdapter:
    """Return the correct BrokerAdapter for the user.

    This adapter is used for ORDER EXECUTION (place_order, cancel_order,
    get_positions, get_holdings) and as a live price FALLBACK only.

    Live prices are sourced from NSEIndiaAdapter in price_service.py and
    do not depend on the user having a broker configured.

    Priority for order execution:
      1. User's personal Angel One credentials
      2. User's personal Upstox credentials

    If no broker is configured, returns YFinanceAdapter (whose live price
    methods return empty, so NSEIndiaAdapter stays primary transparently).
    """
    if not preferred_broker:
        from app.brokers.yfinance_adapter import YFinanceAdapter  # noqa: PLC0415
        logger.info("no_broker_configured_nse_handles_live_prices", user_id=user_id)
        return YFinanceAdapter()

    creds = await _load_credentials(user_id, preferred_broker, db)

    if preferred_broker == "angel_one":
        from app.brokers.angel_one import AngelOneAdapter  # noqa: PLC0415
        from app.core.security import decrypt_field  # noqa: PLC0415

        adapter = AngelOneAdapter(
            api_key=decrypt_field(creds.api_key) if creds and creds.api_key else None,
            client_id=decrypt_field(creds.client_id) if creds and creds.client_id else None,
            password=decrypt_field(creds.api_secret) if creds and creds.api_secret else None,
            totp_secret=decrypt_field(creds.totp_secret) if creds and creds.totp_secret else None,
        )
        if not adapter.is_credentials_configured():
            raise ValueError(
                "Angel One credentials incomplete. Add them in Settings → Broker."
            )

        # Try to restore cached session before doing full TOTP auth
        cached = None
        try:
            from app.core.redis_client import get_cached_broker_session
            cached = await get_cached_broker_session(str(user_id), "angel_one")
        except Exception:
            cached = None
        if cached and cached.get("jwt_token"):
            try:
                from SmartApi import SmartConnect  # type: ignore
                # Strip "Bearer " prefix if present — the SDK's setAccessToken
                # stores the raw token and prepends "Bearer " itself in headers.
                raw_token = cached["jwt_token"]
                if raw_token.startswith("Bearer "):
                    raw_token = raw_token[len("Bearer "):]
                adapter._smart_api = SmartConnect(api_key=adapter._api_key)
                adapter._smart_api.setSessionExpiryHook(lambda: None)
                adapter._auth_token = raw_token
                adapter._smart_api.setAccessToken(raw_token)
                if cached.get("feed_token"):
                    adapter._smart_api.setFeedToken(cached["feed_token"])
                logger.info("angel_one_session_restored", user_id=str(user_id))
                return adapter
            except Exception as exc:
                logger.warning("angel_one_cache_restore_failed", err=str(exc))

        # Full TOTP authentication
        await adapter.connect()
        if not adapter._smart_api:  # noqa: SLF001
            raise ValueError(
                "Angel One authentication failed. Check your credentials in Settings → Broker."
            )

        # Cache the new session — store the raw token (no "Bearer " prefix)
        # so that session restore can call setAccessToken() without double-prefixing.
        try:
            from app.core.redis_client import cache_broker_session
            raw_token = getattr(adapter._smart_api, "access_token", None) or adapter._auth_token or ""
            if raw_token.startswith("Bearer "):
                raw_token = raw_token[len("Bearer "):]
            await cache_broker_session(
                str(user_id),
                "angel_one",
                raw_token,
                getattr(adapter._smart_api, "feed_token", None),
            )
        except Exception as exc:
            logger.warning("broker_session_cache_failed", err=str(exc))

        return adapter

    if preferred_broker == "upstox":
        from app.brokers.upstox import UpstoxAdapter  # noqa: PLC0415
        from app.core.security import decrypt_field  # noqa: PLC0415
        from app.core.config import settings as app_settings

        # Access token is stored in access_token column (refreshed daily)
        access_token = decrypt_field(creds.access_token) if creds and creds.access_token else None
        adapter = UpstoxAdapter(
            api_key=decrypt_field(creds.api_key) if creds and creds.api_key else None,
            api_secret=decrypt_field(creds.api_secret) if creds and creds.api_secret else None,
            access_token=access_token,
            redirect_uri=app_settings.upstox_redirect_uri or None,
        )
        if not adapter.is_credentials_configured():
            raise ValueError(
                "Upstox not authorized. Visit Settings → Broker → Connect Upstox to authorize."
            )
        return adapter

    raise ValueError(f"Unknown broker '{preferred_broker}'. Supported: angel_one, upstox.")


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
        logger.error("load_credentials_failed", err=str(e))
        return None

