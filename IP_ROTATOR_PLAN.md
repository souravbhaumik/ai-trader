# IP Rotator + Google Finance Real-Time Scraper — Implementation Plan

## Problem

Broker APIs (Angel One, Upstox) have rate limits that restrict how many live price queries we can make. Google Finance shows **real-time prices** on its web pages (not 20-min delayed), but aggressive scraping leads to IP blocking.

## Solution

Build a **multi-layered IP rotation system** that uses techniques available to us without relying on external services or third-party proxy lists.

---

## The Core Challenge: Where Do IPs Come From?

We cannot "generate" IPs out of thin air. IP addresses are assigned by ISPs and cloud providers. However, there are several techniques to present different IPs to Google without external dependencies:

| Technique | How it works | Dependency | IPs Available |
|-----------|-------------|------------|---------------|
| **IPv6 Rotation** | Generate random IPv6 addresses from the server's /64 subnet. Each is valid on the network. | Server must have IPv6 enabled | 18 quintillion |
| **IPv4 Rotation** | If server has multiple IPv4s allocated, bind each request to a different one. | Multiple IPv4s on server | 2-50+ (depends on allocation) |
| **Tor Network** | Route through Tor's distributed exit nodes (~1000). Each circuit = different IP. | Tor must be installed on server | ~1000 |
| **Header Rotation Only** | Rotate User-Agent, Accept-Language, browser fingerprint headers. | None | 0 (no IP change) |

---

## Phase 1: IPv6 Rotation (Best — If Server Has IPv6)

### How IPv6 Rotation Works

If the server has IPv6 enabled (most cloud servers do), we can generate billions of unique IPs from a single /64 subnet.

```
IPv6 Address Structure:
2001:db8:85a3:1234:xxxx:xxxx:xxxx:xxxx
│        subnet /64      │  random 64 bits  │

We keep the first 64 bits fixed (our subnet).
We randomize the last 64 bits for each request.
Each randomized address is valid on our network.
```

### Implementation

```python
import random
import ipaddress
import socket
import subprocess
import structlog

logger = structlog.get_logger(__name__)

class IPv6Rotator:
    """
    Generates random IPv6 addresses from the server's subnet.
    
    How it works:
      1. Detect the server's IPv6 subnet (e.g., 2001:db8:85a3:1234::/64)
      2. For each request, generate a random suffix (64 bits)
      3. Bind the HTTP client to this random IPv6 address
      4. Google sees a different IP for each request
    
    Requirements:
      - Server must have IPv6 enabled
      - Server must have a /64 subnet or larger allocated
      - The network must accept outbound connections from any IP in the subnet
    
    Most cloud providers (AWS, GCP, DigitalOcean, Linode, Vultr) support this.
    """
    
    def __init__(self):
        self.subnet = self._detect_ipv6_subnet()
        self._available = self.subnet is not None
        if self._available:
            logger.info("ipv6_rotator_initialized", subnet=str(self.subnet))
        else:
            logger.warning("ipv6_rotator_unavailable")
    
    def _detect_ipv6_subnet(self) -> ipaddress.IPv6Network | None:
        """Detect the server's IPv6 subnet from network interfaces."""
        try:
            result = subprocess.run(
                ["ip", "-6", "addr", "show", "scope", "global"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "inet6" in line:
                    parts = line.strip().split()
                    for part in parts:
                        if "/" in part and not part.startswith("fe80"):
                            return ipaddress.IPv6Network(part, strict=False)
        except Exception:
            pass
        return None
    
    def get_random_ip(self) -> str:
        """Generate a random IPv6 address from our subnet."""
        if not self._available:
            return None
        suffix = random.getrandbits(64)
        host_bits = suffix.to_bytes(8, byteorder='big')
        ip_int = int(self.subnet.network_address) + int.from_bytes(host_bits, byteorder='big')
        return str(ipaddress.IPv6Address(ip_int))
    
    @property
    def is_available(self) -> bool:
        return self._available
```

### How curl_cffi Uses It

`httpx` is **replaced** with `curl_cffi` throughout. `curl_cffi` wraps `libcurl` and fully impersonates Chrome's TLS `ClientHello`, HTTP/2 SETTINGS frame, and ALPN negotiation — the exact fingerprint Google checks. `httpx` fails this check silently.

```python
from curl_cffi.requests import AsyncSession

# curl_cffi binds to a specific IP via libcurl's CURLOPT_INTERFACE.
# 'impersonate' sets the full Chrome TLS + HTTP/2 fingerprint.
ip = ipv6_rotator.get_random_ip()

async with AsyncSession(
    impersonate="chrome124",   # Chrome 124 TLS + HTTP/2 fingerprint
    interface=ip,              # bind outbound connection to this IPv6/IPv4
    timeout=_TIMEOUT,
) as session:
    r = await session.get(url, headers=headers)
```

**Why this matters:**
- `impersonate="chrome124"` sends the exact same cipher suite order, TLS extensions, and HTTP/2 frames as a real Chrome 124 browser
- Google's bot detection checks this fingerprint **before** looking at IP or headers
- Without it, every `httpx` request is identifiable as a bot regardless of IP rotation

**Supported impersonation targets** (rotate these for variety):
```python
_PERSONAS = [
    "chrome120", "chrome124",
    "firefox126",
    "safari17_0",
    "edge99",
]
```

---

## Phase 2: IPv4 Rotation (If Server Has Multiple IPv4s)

### How IPv4 Rotation Works

If the server has multiple public IPv4 addresses allocated (common on cloud servers with multiple elastic IPs or a /29 subnet), we can bind each request to a different IPv4.

```
How to check:
  ip addr show | grep "inet " | grep -v "127.0.0.1"
  
Example output:
  inet 203.0.113.10/24 brd 203.0.113.255 scope global eth0
  inet 203.0.113.11/32 scope global eth0:0    ← additional IP
  inet 203.0.113.12/32 scope global eth0:1    ← additional IP
```

### Implementation

```python
class IPv4Rotator:
    """
    Rotates through multiple IPv4 addresses allocated to the server.
    
    How it works:
      1. Detect all public IPv4 addresses on the server
      2. Maintain a list of available IPs
      3. Round-robin through them for each request
      4. If an IP gets blocked, remove it from rotation
    
    Requirements:
      - Server must have multiple public IPv4s allocated
      - Additional IPv4s cost ~$3-4/month each on most cloud providers
    
    Typical setups:
      - AWS: Multiple Elastic IPs attached to the same ENI
      - DigitalOcean: Multiple floating IPs
      - OVH: /29 subnet (6 usable IPs) included with VPS
      - Hetzner: Additional IPs for €1/month each
    """
    
    def __init__(self):
        self.ips = self._detect_ipv4_addresses()
        self._index = 0
        self._available = len(self.ips) > 1  # need at least 2 to rotate
        if self._available:
            logger.info("ipv4_rotator_initialized", count=len(self.ips), ips=self.ips)
        else:
            logger.info("ipv4_rotator_single_ip", count=len(self.ips))
    
    def _detect_ipv4_addresses(self) -> list[str]:
        """Detect all public IPv4 addresses on the server."""
        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show", "scope", "global"],
                capture_output=True, text=True, timeout=5
            )
            ips = []
            for line in result.stdout.splitlines():
                if "inet" in line:
                    parts = line.strip().split()
                    for part in parts:
                        if "/" in part:
                            ips.append(part.split("/")[0])
            return ips
        except Exception:
            return []
    
    def get_next_ip(self) -> str | None:
        """Get the next IPv4 in round-robin order."""
        if not self.ips:
            return None
        ip = self.ips[self._index % len(self.ips)]
        self._index += 1
        return ip
    
    @property
    def is_available(self) -> bool:
        return self._available
```

---

## Phase 3: Tor Network Rotation (Fallback — No IPv6, Single IPv4)

### How Tor Rotation Works

Tor routes traffic through a distributed network of relays. Each Tor circuit ends at a different exit node with a different IP. By requesting a new circuit for each request, we get a different IP.

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Our      │────▶│ Tor      │────▶│ Tor      │────▶│ Exit     │────▶ Google
│ Server   │     │ Guard    │     │ Relay    │     │ Node     │     Finance
└──────────┘     └──────────┘     └──────────┘     └──────────┘
                                                        │
                                                   IP: 185.220.xxx.xxx
                                                   (different each circuit)
```

### Implementation

```python
class TorRotator:
    """
    Routes requests through the Tor network.
    Each request gets a different exit node IP.
    
    Requirements:
      - Tor must be installed: apt install tor
      - Tor must be running: systemctl start tor
      - SOCKS5 proxy on localhost:9050
      - Control port on localhost:9051 (for circuit rotation)
    
    How it works:
      1. Connect to Tor SOCKS5 proxy
      2. Send request through Tor
      3. After each request, signal Tor to create a new circuit
      4. New circuit = new exit node = new IP
    
    Pros:
      - Free
      - ~1000 different exit nodes available
      - No setup beyond installing Tor
    
    Cons:
      - Slow (1-3s per request)
      - Some sites block Tor exit nodes
      - Google may show CAPTCHA for Tor traffic
    """
    
    SOCKS5_PROXY = "socks5://127.0.0.1:9050"
    CONTROL_PORT = 9051
    
    def __init__(self):
        self._available = self._check_tor_available()
        if self._available:
            logger.info("tor_rotator_initialized")
        else:
            logger.warning("tor_rotator_unavailable")
    
    def _check_tor_available(self) -> bool:
        """Check if Tor is installed and running."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "tor"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False
    
    async def get_new_circuit(self):
        """Signal Tor to create a new circuit (new exit IP)."""
        try:
            with stem.control.Controller.from_port(port=self.CONTROL_PORT) as controller:
                controller.authenticate()
                controller.signal(stem.Signal.NEWNYM)
        except Exception as e:
            logger.warning("tor_new_circuit_failed", err=str(e))
    
    def get_proxy(self) -> dict | None:
        if not self._available:
            return None
        return {"all://": self.SOCKS5_PROXY}
    
    @property
    def is_available(self) -> bool:
        return self._available
```

---

## Phase 4: Header-Only Rotation (Universal Fallback)

When no IP rotation method is available, we still rotate headers aggressively to avoid fingerprinting.

```python
class HeaderRotator:
    """
    Rotates browser headers on every request.
    
    Even without IP rotation, rotating headers makes us look like
    different users to Google's CDN, reducing the chance of blocking.
    """
    
    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.2478.80 Safari/537.36 Edg/124.0.2478.80",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.83 Mobile Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        # + 40 more...
    ]
    
    _LANGUAGES = [
        "en-US,en;q=0.9",
        "en-GB,en;q=0.9",
        "en-IN,en;q=0.9,hi;q=0.8",
        "en-US,en;q=0.9,es;q=0.8",
        "en-CA,en;q=0.9,fr;q=0.8",
    ]
    
    def get_headers(self) -> dict:
        """Return a randomized set of browser headers."""
        return {
            "User-Agent": random.choice(self._USER_AGENTS),
            "Accept-Language": random.choice(self._LANGUAGES),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": random.choice(["no-cache", "max-age=0"]),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
```

---

## Phase 4.5: JavaScript Challenge Bypass (Google Persona Cookies)

### The Problem

Google Finance occasionally returns a **bot-detection page** instead of real HTML. This happens when:
- A new IP is seen for the first time (no trust history)
- Request patterns look automated (no session continuity)
- reCAPTCHA v3 scores your request as bot-like

This is **not a JS challenge in the Cloudflare sense** — Google Finance does not use Cloudflare. It uses Google's own detection, which is primarily **session/cookie based**. A real Chrome user has cookies that carry a trust history. A fresh `httpx` client has nothing.

### The Solution: Pre-Seeded Google Persona Cookies

Google's session cookies (`NID`, `SOCS`, `CONSENT`) carry a **trust score** accumulated over time by a real browser visiting Google properties. By harvesting these cookies from a real Playwright browser session and reusing them in every `curl_cffi` request, we look like an already-trusted user — not a new bot.

```
Cookie lifetime:
  NID    → ~6 months  (main trust carrier)
  SOCS   → ~13 months (consent state)
  CONSENT→ permanent  (cookie banner dismissed)
```

### Implementation: `GooglePersonaManager`

```python
import asyncio
import json
from datetime import datetime, timezone
from playwright.async_api import async_playwright
import structlog

logger = structlog.get_logger(__name__)

class GooglePersonaManager:
    """
    Harvests and maintains Google session cookies using a real
    headless Playwright browser. These cookies make curl_cffi
    requests appear as trusted, returning Google users.

    How it works:
      1. Launch headless Chrome via Playwright
      2. Visit google.com/finance — accept consent, wait for full load
      3. Extract NID, SOCS, CONSENT cookies
      4. Store in Redis with TTL = cookie expiry
      5. All curl_cffi requests include these cookies
      6. Background task refreshes them before expiry

    Result: Google treats our requests as a known, trusted browser
    session rather than a new IP with no history.
    """

    COOKIE_REFRESH_INTERVAL = 60 * 60 * 4   # 4 hours (well within 6-month NID TTL)
    REDIS_KEY = "gf:persona:cookies"

    def __init__(self, redis):
        self._redis = redis
        self._cookies: dict = {}

    async def get_cookies(self) -> dict:
        """Return valid Google cookies, refreshing if needed."""
        if self._cookies:
            return self._cookies
        # Try Redis first (survives restarts)
        cached = await self._redis.get(self.REDIS_KEY)
        if cached:
            self._cookies = json.loads(cached)
            return self._cookies
        # Cold start — harvest fresh cookies
        return await self.refresh_cookies()

    async def refresh_cookies(self) -> dict:
        """Launch headless Chrome, visit Google Finance, harvest cookies."""
        logger.info("google_persona_refreshing_cookies")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",  # hide automation flag
                ]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="Asia/Kolkata",
            )
            page = await context.new_page()

            # Visit Google Finance — accept any consent popup
            await page.goto("https://www.google.com/finance", wait_until="networkidle")
            # Click "Accept all" if consent dialog appears
            try:
                await page.click("button:has-text('Accept all')", timeout=3000)
                await page.wait_for_load_state("networkidle")
            except Exception:
                pass  # No consent dialog — already accepted

            # Extract cookies
            raw = await context.cookies()
            cookies = {
                c["name"]: c["value"]
                for c in raw
                if c["name"] in ("NID", "SOCS", "CONSENT", "1P_JAR", "AEC")
            }
            await browser.close()

        self._cookies = cookies
        await self._redis.setex(
            self.REDIS_KEY,
            self.COOKIE_REFRESH_INTERVAL,
            json.dumps(cookies)
        )
        logger.info("google_persona_cookies_harvested", keys=list(cookies.keys()))
        return cookies

    async def mark_challenge_detected(self):
        """Call this when a JS challenge / CAPTCHA page is returned.
        Forces cookie refresh on next request."""
        self._cookies = {}
        await self._redis.delete(self.REDIS_KEY)
        logger.warning("google_persona_challenge_detected_refreshing")
        asyncio.create_task(self.refresh_cookies())  # refresh in background
```

### How It Integrates With Requests

```python
# Include harvested cookies in every curl_cffi request
cookies = await _persona_manager.get_cookies()
r = await session.get(url, headers=headers, cookies=cookies)

# Detect a challenge page (Google returns 200 with challenge HTML)
if "unusual traffic" in r.text or "recaptcha" in r.text.lower():
    await _persona_manager.mark_challenge_detected()
    # Retry with fresh cookies on next cycle
    return None
```

### Persona Rotation ("Saying GF I Am a Whole Different Person")

Rather than one persona being used for all requests, we maintain **multiple independent browser personas** — each with its own cookies, representing a different user:

```python
_PERSONAS = [
    # (curl_cffi impersonate target, Playwright user-agent, timezone)
    ("chrome124", "Chrome/124 Windows",   "Asia/Kolkata"),
    ("chrome120", "Chrome/120 macOS",     "America/New_York"),
    ("firefox126","Firefox/126 Linux",    "Europe/London"),
    ("safari17_0","Safari/17 iPhone",     "Asia/Tokyo"),
]
```

Each persona:
- Has its own Google cookie set (different `NID` = different identity)
- Uses the matching `curl_cffi` impersonation target (TLS fingerprint matches User-Agent)
- Is assigned to a specific IP from the rotation pool

This means Google sees 4 completely different "users" from 4 different IPs — not one bot with rotating IPs.

---

## Phase 5: Layered Strategy

We try techniques in order, falling back to the next if unavailable:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Full Bypass Strategy                          │
│                                                                  │
│  LAYER A: TLS Fingerprint (curl_cffi)           ← Always ON     │
│  └── impersonate Chrome/Firefox/Safari TLS stack                │
│      Effect: Passes Google's #1 bot check                       │
│                                                                  │
│  LAYER B: Google Persona Cookies                ← Always ON     │
│  └── Pre-harvested NID/SOCS cookies via Playwright              │
│      Effect: Looks like returning trusted user, not new bot     │
│                                                                  │
│  LAYER C: Per-IP Request Budget (Behavioural)   ← Always ON     │
│  └── Each IP sends random(5, 10) requests, then rotates         │
│      Effect: Each IP looks like a normal human browsing         │
│                                                                  │
│  LAYER D: IP Pool (pick best available)                         │
│  ├── TRY #1: IPv6 Rotation                                      │
│  │     ├── Check: Does server have IPv6 /64 block?              │
│  │     └── If YES: 18 quintillion unique IPs                    │
│  ├── TRY #2: IPv4 Rotation                                      │
│  │     ├── Check: Does server have multiple IPv4s?              │
│  │     └── If YES: N unique IPs (round-robin)                   │
│  └── TRY #3: Header-Only (no IP change)                        │
│              Combined with Layers A+B still effective           │
│                                                                  │
│  LAYER E: Persona Rotation                      ← Always ON     │
│  └── 4 independent browser identities (Chrome/Firefox/Safari)  │
│      Each persona: own cookies + own IP + own TLS fingerprint   │
│                                                                  │
│  CIRCUIT BREAKER: 5 failures → block 2 min → auto-recover      │
└─────────────────────────────────────────────────────────────────┘
```

### Strategy Selection at Startup

```python
import random

# curl_cffi impersonation targets — each has a different TLS fingerprint
_PERSONAS = [
    "chrome120",
    "chrome124",
    "firefox126",
    "safari17_0",
]

class IPRotator:
    """
    Unified IP rotation — tries IPv6 → IPv4 → Headers.
    Each IP is assigned a random budget of 5-10 requests before rotating.
    Tor removed: too slow for real-time trading data.
    """

    def __init__(self):
        self.ipv6 = IPv6Rotator()
        self.ipv4 = IPv4Rotator()
        self.headers = HeaderRotator()

        # Determine active strategy
        if self.ipv6.is_available:
            self.strategy = "ipv6"
            logger.info("ip_rotator_strategy", strategy="ipv6")
        elif self.ipv4.is_available:
            self.strategy = "ipv4"
            logger.info("ip_rotator_strategy", strategy="ipv4", count=len(self.ipv4.ips))
        else:
            self.strategy = "headers_only"
            logger.info("ip_rotator_strategy", strategy="headers_only")

        # Per-IP budget tracking
        self._current_ip: str | None = None
        self._budget: int = 0       # how many requests this IP is allowed
        self._used: int = 0         # how many requests this IP has served
        self._persona_index: int = 0

    def get_headers(self) -> dict:
        return self.headers.get_headers()

    def get_persona(self) -> str:
        """Rotate through curl_cffi impersonation targets."""
        persona = _PERSONAS[self._persona_index % len(_PERSONAS)]
        return persona

    def get_ip_for_request(self) -> str | None:
        """
        Return the current IP. When this IP's budget is exhausted,
        rotate to a new IP and assign a fresh random budget.

        Budget = random(5, 10) requests per IP.
        This makes each IP look like a normal user browsing a handful
        of pages — not a bot hammering 50 URLs.
        """
        if self._used >= self._budget:
            # Budget exhausted — rotate IP and persona
            self._used = 0
            self._budget = random.randint(5, 10)
            self._persona_index += 1
            if self.strategy == "ipv6":
                self._current_ip = self.ipv6.get_random_ip()
            elif self.strategy == "ipv4":
                self._current_ip = self.ipv4.get_next_ip()
            else:
                self._current_ip = None
            logger.info(
                "ip_rotator_budget_rotate",
                new_ip=self._current_ip,
                budget=self._budget,
                persona=self.get_persona(),
            )
        self._used += 1
        return self._current_ip

    def get_ip_batches(self, symbols: list[str]) -> list[tuple[str | None, list[str]]]:
        """
        Split symbols into per-IP batches based on random(5,10) budget.

        Example for 50 symbols:
          IP-A gets 7  → [RELIANCE, TCS, INFY, HDFC, ICICI, SBI, BAJAJ]
          IP-B gets 5  → [WIPRO, HCL, TITAN, NESTL, MARUTI]
          IP-C gets 8  → [...]
          ...

        Each IP looks like a user browsing a few stocks, not a bulk scraper.
        """
        batches: list[tuple[str | None, list[str]]] = []
        i = 0
        while i < len(symbols):
            ip = self.get_ip_for_request()
            budget = self._budget - self._used + 1  # remaining budget for this IP
            batch = symbols[i:i + budget]
            batches.append((ip, batch))
            i += len(batch)
        return batches
```

---

## Phase 6: Upgrade Google Finance Adapter

### File: `backend/app/brokers/google_finance_adapter.py`

### Changes

#### 6.1 Remove Circuit Breaker

Replace the hard circuit breaker (5 failures → 2-min block) with the layered rotation strategy.

#### 6.2 Add Rotation to `_fetch_one()`

```python
# Module-level rotator (initialized once)
_ip_rotator = IPRotator()

async def _fetch_one(self, client, gf_key, original_symbol) -> Optional[Quote]:
    headers = _ip_rotator.get_headers()
    start = time.monotonic()
    
    for attempt in range(3):
        try:
            async with _BATCH_SEM:
                r = await client.get(
                    f"https://www.google.com/finance/quote/{gf_key}",
                    headers=headers,
                )
            r.raise_for_status()
            price = _parse_price(r.text)
            if price:
                return _make_quote(original_symbol, price, _parse_change_pct(r.text))
        except Exception as exc:
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))  # exponential backoff
                headers = _ip_rotator.get_headers()
                continue
            logger.warning("google_finance_fetch_failed", symbol=original_symbol, err=str(exc))
    
    return None
```

#### 6.3 Update `get_quotes_batch()` — Per-IP Budgeted Batches

Instead of one client fetching all symbols, we split symbols into per-IP batches.
Each batch uses its own `curl_cffi` session bound to a different IP with its own persona.

```python
from curl_cffi.requests import AsyncSession

async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
    all_results: list[Quote] = []

    # Split 50 symbols into per-IP batches of random(5, 10)
    # e.g.: IP-A→7 symbols, IP-B→5, IP-C→8, ...
    for ip, batch in _ip_rotator.get_ip_batches(symbols):
        persona = _ip_rotator.get_persona()
        cookies = await _persona_manager.get_cookies()
        headers = _ip_rotator.get_headers()

        session_kwargs: dict = {
            "impersonate": persona,   # Chrome/Firefox/Safari TLS fingerprint
            "timeout": _TIMEOUT,
        }
        if ip:
            session_kwargs["interface"] = ip  # bind this batch to this IP

        async with AsyncSession(**session_kwargs) as session:
            tasks = [
                self._fetch_one(session, _symbol_to_gf_key(sym), sym, headers, cookies)
                for sym in batch
            ]
            # Concurrency within a batch is naturally limited by budget size (5-10)
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results.extend(
                r for r in batch_results if isinstance(r, Quote)
            )

        # Small inter-batch delay — avoids all IPs hitting Google simultaneously
        await asyncio.sleep(random.uniform(0.3, 0.8))

    return all_results


async def _fetch_one(
    self,
    session: AsyncSession,
    gf_key: str,
    original_symbol: str,
    headers: dict,
    cookies: dict,
) -> Optional[Quote]:
    url = f"https://www.google.com/finance/quote/{gf_key}"
    for attempt in range(3):
        try:
            async with _BATCH_SEM:
                r = await session.get(url, headers=headers, cookies=cookies)
            r.raise_for_status()

            # Detect JS challenge / bot detection page
            if "unusual traffic" in r.text or "recaptcha" in r.text.lower():
                logger.warning("google_finance_challenge_detected", symbol=original_symbol)
                await _persona_manager.mark_challenge_detected()
                return None

            price = _parse_price(r.text)
            if price is None:
                _record_failure()
                return None

            _record_success()
            return _make_quote(original_symbol, price, _parse_change_pct(r.text))

        except Exception as exc:
            if attempt < 2:
                await asyncio.sleep(random.uniform(0.5, 1.5) * (attempt + 1))
                continue
            _record_failure()
            logger.warning("google_finance_fetch_failed", symbol=original_symbol, err=str(exc))
    return None
```

#### 6.4 Per-IP Request Budget (Behavioural Camouflage)

Do **not** raise the global semaphore to 20. That's the wrong approach.

Instead, the `get_ip_batches()` method naturally limits concurrency:
- Each IP batch is **random(5, 10)** symbols — within human browsing range
- Within a batch, requests are concurrent (fast) but bounded by budget size
- Between batches, a **0.3–0.8s random delay** prevents all IPs hitting Google simultaneously
- The `_BATCH_SEM` stays at **5** as a hard cap within each IP's batch

This results in Google seeing:
```
IP-A: 7 requests over ~3s  → looks like someone browsing their watchlist
IP-B: 5 requests over ~2s  → looks like another user doing the same
IP-C: 8 requests over ~4s  → another normal user
(0.5s gap between each IP batch)
```

Not:
```
20 concurrent requests from 20 IPs in 200ms → obvious bot pattern
```

#### 6.5 Improve HTML Parsing

Current regex patterns are fragile. Add:
- BeautifulSoup fallback parser
- JSON-LD extraction (Google embeds structured data)
- More robust price regex patterns

---

## Phase 7: All Places That Need Live Prices

Here is every location in the codebase that consumes live price data and would benefit from the Google Finance + IP Rotator integration:

### 7.1 Backend Services

| # | File | Function | What it does | Symbols per call |
|---|------|----------|-------------|-----------------|
| 1 | `backend/app/services/price_service.py` | `get_quote()` | Single symbol quote | 1 |
| 2 | `backend/app/services/price_service.py` | `get_quotes_batch()` | Batch quote fetch | N (up to 50) |
| 3 | `backend/app/services/price_service.py` | `get_indices()` | Index quotes (Nifty, Sensex, etc.) | 4-6 |
| 4 | `backend/app/services/screener_service.py` | `get_screener_page()` | Screener page with live prices | 50 (per page) |
| 5 | `backend/app/services/price_service.py` | `get_history()` | Historical OHLCV bars | N bars |

### 7.2 API Endpoints

| # | Endpoint | File | What it serves |
|---|----------|------|----------------|
| 6 | `GET /api/v1/prices/indices` | `backend/app/api/v1/prices.py` | Index quotes for dashboard |
| 7 | `GET /api/v1/prices/{symbol}/quote` | `backend/app/api/v1/prices.py` | Single symbol quote |
| 8 | `GET /api/v1/prices/{symbol}/history` | `backend/app/api/v1/prices.py` | Historical OHLCV |
| 9 | `GET /api/v1/screener` | `backend/app/api/v1/screener.py` | Screener with live prices |
| 10 | `GET /api/v1/screener/universe/search` | `backend/app/api/v1/screener.py` | Symbol search |
| 11 | `GET /api/v1/forecasts/{symbol}` | `backend/app/api/v1/forecasts.py` | TFT forecast (needs latest price) |
| 12 | `GET /api/v1/forecasts/{symbol}/anomaly` | `backend/app/api/v1/forecasts.py` | LSTM anomaly (needs latest price) |
| 13 | `WS /api/v1/ws/prices` | `backend/app/api/v1/ws.py` | WebSocket price streaming |

### 7.3 Frontend Pages

| # | Page | File | What uses live prices |
|---|------|------|----------------------|
| 14 | **Dashboard** | `frontend/src/pages/DashboardPage.tsx` | Index ticker tape, top movers, portfolio summary |
| 15 | **Screener** | `frontend/src/pages/ScreenerPage.tsx` | All 50 rows show live price, change %, volume |
| 16 | **Signal Log** | `frontend/src/pages/SignalLogPage.tsx` | Signal rows show entry price (from signal, not live) |
| 17 | **Paper Trading** | `frontend/src/pages/PaperTradingPage.tsx` | Open positions show current P&L (needs live price) |
| 18 | **Live Portfolio** | `frontend/src/pages/LivePortfolioPage.tsx` | Positions show LTP, P&L from broker API |
| 19 | **Forecast** | `frontend/src/pages/ForecastPage.tsx` | Forecast chart base price |
| 20 | **Forecast Modal** | `frontend/src/components/ForecastModal.tsx` | Price chart + anomaly score |
| 21 | **Order Modal** | `frontend/src/components/OrderModal.tsx` | Entry price pre-fill |

### 7.4 Integration Priority

**Tier 1 (High traffic, needs IP rotation most):**
- Screener (50 symbols per page, multiple users)
- Dashboard indices + ticker tape
- WebSocket price streaming

**Tier 2 (Medium traffic):**
- Single symbol quotes (price service)
- Paper trading P&L refresh
- Forecast base price

**Tier 3 (Low traffic, broker API fine):**
- Live portfolio (already uses broker API for orders)
- Historical data (uses DB, not live)

---

## Phase 8: Make Google Finance the Default Price Source

### File: `backend/app/brokers/factory.py`

**Change:** Instead of raising `ValueError` when no broker is configured, return `GoogleFinanceAdapter()`.

```python
async def get_adapter_for_user(user_id, preferred_broker, db) -> BrokerAdapter:
    if not preferred_broker:
        logger.info("no_broker_configured_using_google_finance", user_id=user_id)
        return GoogleFinanceAdapter()
    
    # ... existing Angel One / Upstox logic ...
    
    # Fallback at the end
    logger.info("broker_fallback_to_google_finance", user_id=user_id)
    return GoogleFinanceAdapter()
```

### File: `backend/app/services/screener_service.py`

**Change:** Add Google Finance fallback when broker returns no quotes.

```python
if not quote_map:
    quotes = await adapter.get_quotes_batch(symbols)
    if not quotes:
        from app.brokers.google_finance_adapter import GoogleFinanceAdapter
        gf = GoogleFinanceAdapter()
        quotes = await gf.get_quotes_batch(symbols)
    quote_map = {q.symbol: q.__dict__ for q in quotes}
```

### File: `backend/app/services/price_service.py`

**Already has Google Finance fallback** — no changes needed.

---

## Phase 9: Rate Limit & Cache Tuning

### Redis Cache TTLs

| Data Type | Current TTL | New TTL | Rationale |
|-----------|-------------|---------|-----------|
| Quote (Angel One) | 60s | 30s | Faster refresh |
| Quote (Google) | 60s | 20s | Lower latency acceptable |
| Screener batch | 30s | 15s | Fresher data for screener |
| Indices | 60s | 30s | Indices change slower |

### Rate Limiting (FastAPI)

| Endpoint | Current | New | Rationale |
|----------|---------|-----|-----------|
| `/prices/*` | 120/min | 300/min | Google scraping can handle more |
| `/screener` | 30/min | 60/min | More frequent screener refreshes |

---

## Phase 10: Monitoring & Observability

### Logging

Add structured logging for:
- `ip_rotator.strategy` — which rotation strategy is active (ipv6/ipv4/tor/headers)
- `ip_rotator.fetch_success` — successful scrape with response time
- `ip_rotator.fetch_failed` — failed scrape with error
- `ip_rotator.ipv6_unavailable` — IPv6 not available, falling back
- `ip_rotator.ipv4_single_ip` — only one IPv4 available, no rotation possible
- `ip_rotator.tor_unavailable` — Tor not available, falling back

### Metrics (Redis counters)

```python
redis.incr("stats:google_finance:requests")
redis.incr("stats:google_finance:success")
redis.incr("stats:google_finance:failures")
```

---

## Implementation Order

```
Phase 1: Build IP rotation module
  ├── backend/app/core/ip_rotator.py
  │     ├── IPv6Rotator (detect subnet, generate random IPs)
  │     ├── IPv4Rotator (detect multiple IPv4s, round-robin)
  │     ├── TorRotator (SOCKS5 proxy + circuit rotation)
  │     ├── HeaderRotator (UA + browser headers)
  │     └── IPRotator (unified strategy selector)
  └── Update config.py + .env.example

Phase 2: Upgrade Google Finance Adapter
  ├── Remove circuit breaker
  ├── Add IP rotation (IPv6 → IPv4 → Tor → Headers)
  ├── Add header rotation
  ├── Improve HTML parsing (BeautifulSoup, JSON-LD)
  └── Increase concurrency (5 → 20)

Phase 3: Wire Up All Live Price Consumers
  ├── Update factory.py → return GoogleFinanceAdapter as default
  ├── Update screener_service.py → Google Finance fallback
  ├── Verify price_service.py fallback (already done)
  ├── Verify WebSocket price streaming
  └── Verify forecast endpoints

Phase 4: Cache & Rate Limit Tuning
  ├── Update Redis TTLs
  └── Update rate limiter configs

Phase 5: Monitoring
  ├── Add structured logging
  ├── Add Redis metrics
  └── Admin dashboard integration
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `backend/app/core/ip_rotator.py` | IPv6Rotator, IPv4Rotator, TorRotator, HeaderRotator, IPRotator |

## Files to Modify

| File | Changes |
|------|---------|
| `backend/app/brokers/google_finance_adapter.py` | Remove circuit breaker, add rotation, better parsing |
| `backend/app/brokers/factory.py` | Return GoogleFinanceAdapter when no broker configured |
| `backend/app/services/screener_service.py` | Add Google Finance fallback |
| `backend/app/core/config.py` | Add IP rotator settings |
| `.env.example` | Add IP rotator env vars |

## Files NOT Modified

| File | Reason |
|------|--------|
| `backend/app/services/price_service.py` | Already has Google Finance fallback |
| `backend/app/brokers/base.py` | Interface unchanged |
| `backend/app/brokers/angel_one.py` | Still used for order execution |
| `backend/app/brokers/upstox.py` | Still used for order execution |
| `backend/app/api/v1/live_portfolio.py` | Order execution still needs broker |
| `backend/app/api/v1/prices.py` | Uses price_service, no direct changes needed |
| `backend/app/api/v1/ws.py` | Uses price_service, no direct changes needed |
| `backend/app/api/v1/forecasts.py` | Uses price_service, no direct changes needed |
