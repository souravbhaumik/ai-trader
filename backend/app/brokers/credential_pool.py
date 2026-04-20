"""Shared broker credential pool for market data fan-out.

Phase 8 design:
- Loads pool-eligible Angel One credentials from DB.
- Uses round-robin selection across healthy credentials.
- Marks failing credentials degraded via Redis TTL so all workers share health.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import List, Optional

import redis
import structlog
from sqlalchemy import text

from app.brokers.angel_one import AngelOneAdapter
from app.core.config import settings
from app.core.database import get_sync_session
from app.core.security import decrypt_field

logger = structlog.get_logger(__name__)

_DEGRADED_TTL_SECONDS = 60


@dataclass
class PoolCredential:
    user_id: str
    api_key: str
    client_id: str
    password: str
    totp_secret: str


class CredentialPool:
    """In-memory round-robin pool with Redis-backed degraded flags."""

    def __init__(self) -> None:
        self._credentials: List[PoolCredential] = []
        self._idx = 0
        self._lock = threading.Lock()
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)

    def load_from_db(self) -> int:
        """Load eligible Angel One credentials into local pool."""
        with get_sync_session() as db:
            rows = db.execute(
                text(
                    """
                    SELECT user_id, api_key, client_id, api_secret, totp_secret
                    FROM broker_credentials
                    WHERE broker_name = 'angel_one'
                      AND is_configured = TRUE
                      AND pool_eligible = TRUE
                      AND api_key IS NOT NULL
                      AND client_id IS NOT NULL
                      AND api_secret IS NOT NULL
                      AND totp_secret IS NOT NULL
                    ORDER BY updated_at DESC
                    """
                )
            ).fetchall()

        creds: List[PoolCredential] = []
        for row in rows:
            try:
                creds.append(
                    PoolCredential(
                        user_id=str(row[0]),
                        api_key=decrypt_field(row[1]),
                        client_id=decrypt_field(row[2]),
                        password=decrypt_field(row[3]),
                        totp_secret=decrypt_field(row[4]),
                    )
                )
            except Exception as exc:
                logger.warning("credential_pool.decrypt_failed", user_id=str(row[0]), err=str(exc))

        with self._lock:
            self._credentials = creds
            self._idx = 0
        return len(creds)

    def _is_degraded(self, client_id: str) -> bool:
        return bool(self._redis.exists(f"pool:degraded:{client_id}"))

    def mark_degraded(self, client_id: str, ttl_seconds: int = _DEGRADED_TTL_SECONDS) -> None:
        self._redis.setex(f"pool:degraded:{client_id}", ttl_seconds, "1")

    def get_next(self) -> Optional[PoolCredential]:
        with self._lock:
            if not self._credentials:
                return None
            size = len(self._credentials)
            for _ in range(size):
                cred = self._credentials[self._idx % size]
                self._idx = (self._idx + 1) % size
                if not self._is_degraded(cred.client_id):
                    return cred
        return None

    async def get_connected_adapter(self) -> Optional[AngelOneAdapter]:
        """Return a connected AngelOne adapter from the pool, or None."""
        # Reload lazily if pool is empty.
        if not self._credentials:
            self.load_from_db()

        attempts = max(1, len(self._credentials))
        for _ in range(attempts):
            cred = self.get_next()
            if not cred:
                return None

            adapter = AngelOneAdapter(
                api_key=cred.api_key,
                client_id=cred.client_id,
                password=cred.password,
                totp_secret=cred.totp_secret,
            )
            try:
                await adapter.connect()
                if adapter._smart_api:  # noqa: SLF001
                    return adapter
                self.mark_degraded(cred.client_id)
            except Exception as exc:
                self.mark_degraded(cred.client_id)
                logger.warning("credential_pool.connect_failed", user_id=cred.user_id, err=str(exc))

        return None

    async def health_check(self) -> dict:
        """Probe degraded credentials and clear recovered entries.

        This is a best-effort helper intended for periodic background checks.
        """
        recovered = 0
        still_degraded = 0

        # Ensure we have the latest credential list.
        if not self._credentials:
            self.load_from_db()

        for cred in list(self._credentials):
            if not self._is_degraded(cred.client_id):
                continue
            adapter = AngelOneAdapter(
                api_key=cred.api_key,
                client_id=cred.client_id,
                password=cred.password,
                totp_secret=cred.totp_secret,
            )
            try:
                await adapter.connect()
                if adapter._smart_api:  # noqa: SLF001
                    self._redis.delete(f"pool:degraded:{cred.client_id}")
                    recovered += 1
                else:
                    still_degraded += 1
            except Exception:
                still_degraded += 1
            finally:
                try:
                    await adapter.disconnect()
                except Exception:
                    pass

        return {"recovered": recovered, "still_degraded": still_degraded}


_pool_singleton: CredentialPool | None = None
_pool_lock = threading.Lock()


def get_credential_pool() -> CredentialPool:
    global _pool_singleton
    if _pool_singleton is None:
        with _pool_lock:
            if _pool_singleton is None:
                _pool_singleton = CredentialPool()
    return _pool_singleton


def get_quotes_batch_via_pool(symbols: list[str]) -> list[dict]:
    """Sync wrapper for WS broadcaster thread pool."""

    async def _run() -> list[dict]:
        pool = get_credential_pool()
        adapter = await pool.get_connected_adapter()
        if not adapter:
            return []
        try:
            quotes = await adapter.get_quotes_batch(symbols)
            return [q.__dict__ for q in quotes]
        finally:
            try:
                await adapter.disconnect()
            except Exception:
                pass

    return asyncio.run(_run())
