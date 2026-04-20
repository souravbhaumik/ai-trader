"""IP Rotator — proxy pool for outbound HTTP requests.

Supports static SOCKS5/HTTP proxy pool with round-robin selection,
dead-proxy eviction, and background health checks.

Usage:
    from app.lib.ip_rotator import get_rotator

    rotator = get_rotator()
    session = rotator.get_session()
    resp = session.get("https://example.com")

Configuration (env vars):
    IP_ROTATOR_BACKEND: "proxy_list" | "none" (default: "none")
    IP_ROTATOR_PROXY_LIST: newline-separated proxy URIs
    IP_ROTATOR_STRATEGY: "round_robin" | "random" (default: "round_robin")
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

import structlog

logger = structlog.get_logger(__name__)

_DEAD_THRESHOLD = 3  # consecutive failures to mark dead
_HEALTH_CHECK_INTERVAL = 600  # 10 minutes


@dataclass
class ProxyEntry:
    uri: str
    fail_count: int = 0
    last_used: Optional[float] = None
    is_dead: bool = False


class IPRotator:
    """Round-robin proxy pool with dead-proxy eviction."""

    def __init__(
        self,
        proxies: List[str],
        strategy: str = "round_robin",
    ):
        self._proxies: List[ProxyEntry] = [ProxyEntry(uri=p.strip()) for p in proxies if p.strip()]
        self._strategy = strategy
        self._index = 0
        self._lock = threading.Lock()
        self._health_thread: Optional[threading.Thread] = None

    @classmethod
    def from_settings(cls) -> "IPRotator":
        """Create an IPRotator from environment settings."""
        from app.core.config import settings
        proxy_list = getattr(settings, "ip_rotator_proxy_list", "")
        strategy = getattr(settings, "ip_rotator_strategy", "round_robin")

        proxies = [p.strip() for p in proxy_list.split("\n") if p.strip()]
        return cls(proxies=proxies, strategy=strategy)

    def get_session(self):
        """Return a requests.Session configured with the next proxy."""
        import requests

        session = requests.Session()
        proxy = self._next_proxy()
        if proxy:
            session.proxies = {
                "http": proxy.uri,
                "https": proxy.uri,
            }
            proxy.last_used = time.monotonic()
        return session

    def get_httpx_proxy(self) -> Optional[str]:
        """Return the next proxy URI for httpx, or None if no proxies available."""
        proxy = self._next_proxy()
        if proxy:
            proxy.last_used = time.monotonic()
            return proxy.uri
        return None

    def rotate(self) -> None:
        """Explicitly advance to the next proxy."""
        with self._lock:
            self._index += 1

    def mark_failed(self, proxy_uri: str) -> None:
        """Record a failure for a proxy. After _DEAD_THRESHOLD failures, mark as dead."""
        with self._lock:
            for p in self._proxies:
                if p.uri == proxy_uri:
                    p.fail_count += 1
                    if p.fail_count >= _DEAD_THRESHOLD:
                        p.is_dead = True
                        logger.warning("ip_rotator.proxy_dead", uri=p.uri, failures=p.fail_count)
                    break

    def mark_success(self, proxy_uri: str) -> None:
        """Reset failure count on success."""
        with self._lock:
            for p in self._proxies:
                if p.uri == proxy_uri:
                    p.fail_count = 0
                    p.is_dead = False
                    break

    def _next_proxy(self) -> Optional[ProxyEntry]:
        """Get the next live proxy via round-robin or random selection."""
        with self._lock:
            live = [p for p in self._proxies if not p.is_dead]
            if not live:
                return None
            if self._strategy == "random":
                return random.choice(live)
            # Round-robin
            idx = self._index % len(live)
            self._index += 1
            return live[idx]

    @property
    def proxy_count(self) -> int:
        return len(self._proxies)

    @property
    def live_count(self) -> int:
        return sum(1 for p in self._proxies if not p.is_dead)

    def start_health_checks(self) -> None:
        """Start a background thread that periodically checks dead proxies."""
        if self._health_thread and self._health_thread.is_alive():
            return

        def _checker():
            while True:
                time.sleep(_HEALTH_CHECK_INTERVAL)
                self._run_health_check()

        self._health_thread = threading.Thread(target=_checker, daemon=True)
        self._health_thread.start()
        logger.info("ip_rotator.health_check_started")

    def _run_health_check(self) -> None:
        """Probe dead proxies and revive them if they respond."""
        import requests

        with self._lock:
            dead = [p for p in self._proxies if p.is_dead]

        for p in dead:
            try:
                resp = requests.get(
                    "https://httpbin.org/ip",
                    proxies={"https": p.uri},
                    timeout=10,
                )
                if resp.status_code == 200:
                    self.mark_success(p.uri)
                    logger.info("ip_rotator.proxy_revived", uri=p.uri)
            except Exception:
                pass  # Still dead


class NoopRotator:
    """No-op rotator when proxy rotation is disabled."""

    def get_session(self):
        import requests
        return requests.Session()

    def get_httpx_proxy(self) -> Optional[str]:
        return None

    def rotate(self) -> None:
        pass

    def mark_failed(self, proxy_uri: str) -> None:
        pass

    def mark_success(self, proxy_uri: str) -> None:
        pass

    def start_health_checks(self) -> None:
        pass

    @property
    def proxy_count(self) -> int:
        return 0

    @property
    def live_count(self) -> int:
        return 0


# Module-level singleton
_rotator = None
_rotator_lock = threading.Lock()


def get_rotator() -> IPRotator | NoopRotator:
    """Get or create the singleton IP rotator based on settings."""
    global _rotator
    if _rotator is None:
        with _rotator_lock:
            if _rotator is None:
                from app.core.config import settings
                backend = getattr(settings, "ip_rotator_backend", "none")
                if backend == "proxy_list":
                    _rotator = IPRotator.from_settings()
                    if _rotator.proxy_count > 0:
                        _rotator.start_health_checks()
                        logger.info("ip_rotator.initialized", proxies=_rotator.proxy_count)
                    else:
                        logger.info("ip_rotator.no_proxies_configured")
                        _rotator = NoopRotator()
                else:
                    _rotator = NoopRotator()
    return _rotator
