# AI Trader — Full System Design Document

> **Status**: Pre-implementation review (v2 — post review-comments applied)  
> **Target**: Production-ready, invite-only closed-group platform, Indian markets (NSE/BSE)  
> **Audience**: Developer review before any code is written

---

## Table of Contents

1. [Goals & Non-Goals](#1-goals--non-goals)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Project Structure](#4-project-structure)
5. [Backend Design](#5-backend-design)
   - 5.1 [Framework & App Layout](#51-framework--app-layout)
   - 5.2 [Broker Adapter Pattern](#52-broker-adapter-pattern)
   - 5.3 [Authentication & Authorization](#53-authentication--authorization)
   - 5.4 [API Design Conventions](#54-api-design-conventions)
   - 5.5 [WebSocket Architecture](#55-websocket-architecture)
   - 5.6 [Background Tasks (Celery)](#56-background-tasks-celery)
   - 5.7 [Paper Trading Engine](#57-paper-trading-engine)
   - 5.8 [Live Order Routing](#58-live-order-routing)
6. [AI/ML Pipeline](#6-aiml-pipeline)
   - 6.1 [Models & Responsibilities](#61-models--responsibilities)
   - 6.2 [Training Pipeline](#62-training-pipeline)
   - 6.3 [Inference Pipeline](#63-inference-pipeline)
   - 6.4 [MLflow Model Registry](#64-mlflow-model-registry)
   - 6.5 [Signal Explainability (LLM Integration)](#65-signal-explainability-llm-integration)
7. [News & Sentiment Pipeline](#7-news--sentiment-pipeline)
8. [Database Design](#8-database-design)
   - 8.1 [PostgreSQL + TimescaleDB (Persistent)](#81-postgresql--timescaledb-persistent)
   - 8.2 [Redis (In-Memory / Cache)](#82-redis-in-memory--cache)
9. [Logging Strategy](#9-logging-strategy)
   - 9.1 [Active Alerting](#91-active-alerting)
10. [Configuration Management](#10-configuration-management)
11. [Error Handling Strategy](#11-error-handling-strategy)
12. [Frontend Design (Web)](#12-frontend-design-web)
13. [IP Rotation Library (Personal Use)](#13-ip-rotation-library-personal-use)
14. [Mobile App Design](#14-mobile-app-design)
15. [Admin Panel](#15-admin-panel)
16. [Security Design](#16-security-design)
17. [Docker & Infrastructure](#17-docker--infrastructure)
18. [Development Phases](#18-development-phases)
19. [Future Hosting Plan](#19-future-hosting-plan)
20. [Testing & CI/CD Strategy](#20-testing--cicd-strategy)

---

## 1. Goals & Non-Goals

### Goals
- Real-time Indian stock market data via Angel One SmartAPI and Upstox (user-selectable, no hardcoding)
- AI-generated buy/sell signals using an ensemble of FinBERT, TFT, and LightGBM
- News sentiment pipeline from multiple free sources feeding into signal generation
- Paper trading (simulated) and live trading (broker API) modes — each user connects their own broker account
- One-tap order placement via Angel One or Upstox APIs, routed through the user's own connected broker account
- Invite-only user access: admin creates accounts for trusted users (friends), no public self-registration
- Role-based access control: admin, trader, viewer
- Admin panel: user management (invite, deactivate), data backfill, model management, system health, configuration
- Web app (Next.js) + Mobile app (React Native/Expo) — same backend
- All infrastructure runs on Docker Compose; no paid cloud required during development
- RTX 3050 (4 GB VRAM) compatible for all training and inference tasks (with sequential model loading)
- Order status updates via broker webhooks (postbacks), not polling

### Non-Goals
- Zerodha Kite Connect integration (paid API — deferred to future)
- Groww API (no public API available)
- Options strategy engine (out of scope for v1)
- Crypto markets
- High-frequency trading (sub-second latency)
- **Public self-registration** — accounts are created only by the admin; no open sign-up page
- **Public SaaS / commercial signal distribution** — the platform is a private closed group. If it ever scales to a commercial service distributing signals to paying users, SEBI RIA/RA registration becomes mandatory at that point
- **Shared broker account** — every user must connect their own personal broker API credentials; the system never pools or shares a single broker account across users

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────┐
│                   CLIENTS                        │
│  Next.js Web App        React Native Mobile App  │
└────────────────┬────────────────────────────────┘
                 │ HTTPS / WSS
┌────────────────▼────────────────────────────────┐
│              FASTAPI BACKEND                     │
│  REST API  │  WebSocket Server  │  Admin API     │
└────┬───────┴──────────┬─────────┴───────┬────────┘
     │                  │                 │
┌────▼────┐    ┌────────▼──────┐ ┌────────▼───────┐
│  Redis   │    │  PostgreSQL   │ │  Celery Workers │
│ Pub/Sub  │    │ +TimescaleDB  │ │  (AI, News,     │
│ Cache    │    │  (Persistent) │ │   Data tasks)   │
└────┬────┘    └───────────────┘ └────────┬────────┘
     │                                    │
┌────▼────────────────────────────────────▼───────┐
│              EXTERNAL SERVICES                   │
│  Angel One SmartAPI  │  Upstox API  │  RSS Feeds │
│  yfinance            │  NSE Bhavcopy│  StockTwits│
└─────────────────────────────────────────────────┘
```

### Data Flow — Live Prices
```
Angel One / Upstox WebSocket
         │
   FastAPI Ingestor (broker adapter)
         │
   Redis Pub/Sub  ──────────────────→  TimescaleDB (1-min OHLCV bar)
         │
   WebSocket broadcast
         │
   Web / Mobile clients (TradingView chart updates)
```

### Data Flow — Signal Generation
```
Celery Beat (every 5 min during market hours)
         │
   Pull latest prices + sentiment scores from Redis/DB
         │
   LightGBM + TFT → price signal
   FinBERT pipeline → sentiment signal
         │
   Ensemble → final signal (BUY / SELL / HOLD) + confidence %
         │
   Store in signals table → push to Redis → broadcast via WebSocket
```

---

## 3. Technology Stack

| Layer             | Technology                        | Reason                                      |
|-------------------|-----------------------------------|---------------------------------------------|
| Backend API       | FastAPI 0.111+                    | Async, fast, typed, excellent WebSocket     |
| Task Queue        | Celery 5 + Redis broker           | Distributed background jobs, scheduling     |
| In-memory store   | Redis 7                           | Pub/Sub for live prices, caching, Celery    |
| Database          | PostgreSQL 16 + TimescaleDB 2     | Relational + time-series in one             |
| ORM               | SQLModel (Pydantic + SQLAlchemy)  | Type-safe, FastAPI-native                   |
| DB Migrations     | Alembic                           | Versioned schema migrations                 |
| Auth              | FastAPI + python-jose + passlib   | JWT access/refresh tokens, bcrypt hashing  |
| ML — Forecasting  | PyTorch Forecasting (TFT)         | Best-in-class multi-horizon forecasting     |
| ML — Tabular      | LightGBM                          | Fast, accurate, low memory                  |
| ML — NLP          | FinBERT (HuggingFace Transformers)| Finance-pretrained sentiment model          |
| ML — Anomaly      | PyTorch LSTM Autoencoder          | Pump/dump & anomaly detection               |
| Model Registry    | MLflow (self-hosted)              | Experiment tracking, versioning             |
| Live data         | Angel One SmartAPI + Upstox API   | Free, WebSocket, Indian markets             |
| Historical data   | yfinance + NSE Bhavcopy           | Free, reliable backfill                     |
| News sentiment    | Multiple RSS + PRAW + gnews       | Free, no API key needed                     |
| Web Frontend      | Next.js 14 (App Router)           | SSR, file-based routing, RSC                |
| UI Components     | Shadcn/UI + Tailwind CSS          | Accessible, customizable, free              |
| Charts            | TradingView Lightweight Charts    | Professional, free, open-source             |
| State (server)    | TanStack Query v5                 | Caching, background refetch                 |
| State (client)    | Zustand                           | Simple, performant                          |
| Mobile            | React Native + Expo (managed)     | Cross-platform iOS/Android, free            |
| Mobile charts     | Victory Native XL                 | Performant charts on mobile                 |
| Push notifications| Expo Push Notifications           | Free, no Firebase needed                    |
| Containerization  | Docker + Docker Compose           | Single command startup                      |
| Logging           | structlog + Python logging        | Structured JSON logs                        |
| API docs          | FastAPI auto (Swagger + ReDoc)    | Built-in, zero config                       |

---

## 4. Project Structure

```
ai-trader/
│
├── backend/                          # FastAPI application
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                   # App factory, lifespan, middleware
│   │   │
│   │   ├── api/                      # Route handlers (thin — delegate to services)
│   │   │   ├── __init__.py
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py         # Aggregates all v1 routers
│   │   │   │   ├── auth.py
│   │   │   │   ├── users.py
│   │   │   │   ├── admin.py
│   │   │   │   ├── portfolio.py
│   │   │   │   ├── orders.py
│   │   │   │   ├── signals.py
│   │   │   │   ├── screener.py
│   │   │   │   ├── prices.py
│   │   │   │   └── watchlist.py
│   │   │
│   │   ├── brokers/                  # Broker adapter pattern
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # Abstract BrokerAdapter interface
│   │   │   ├── angel_one.py          # Angel One SmartAPI implementation
│   │   │   ├── upstox.py             # Upstox API v3 implementation
│   │   │   ├── nse_fallback.py       # NSE public feed (no account)
│   │   │   └── factory.py            # get_broker_adapter(user) → adapter
│   │   │
│   │   ├── services/                 # Business logic layer
│   │   │   ├── auth_service.py
│   │   │   ├── user_service.py
│   │   │   ├── order_service.py      # Routes to paper engine or live broker
│   │   │   ├── portfolio_service.py
│   │   │   ├── signal_service.py
│   │   │   ├── screener_service.py
│   │   │   ├── market/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ingestor.py       # WebSocket listener → Redis pub/sub
│   │   │   │   ├── ohlcv_builder.py  # Tick → 1-min OHLCV bar → DB
│   │   │   │   └── universe.py       # Ticker universe management
│   │   │   ├── paper_engine/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── engine.py         # Simulated order matching
│   │   │   │   └── pnl.py            # Virtual P&L calculation
│   │   │   ├── ml/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── tft_model.py      # TFT wrapper
│   │   │   │   ├── lgbm_model.py     # LightGBM wrapper
│   │   │   │   ├── finbert.py        # FinBERT sentiment wrapper
│   │   │   │   ├── autoencoder.py    # LSTM anomaly detection wrapper
│   │   │   │   └── ensemble.py       # Weighted ensemble combiner
│   │   │   └── news/
│   │   │       ├── __init__.py
│   │   │       ├── fetcher.py        # RSS + gnews + PRAW fetchers
│   │   │       ├── ner.py            # Ticker extraction from headlines
│   │   │       └── pipeline.py       # Orchestrates fetch → NER → FinBERT → DB
│   │   │
│   │   ├── tasks/                    # Celery task definitions
│   │   │   ├── __init__.py
│   │   │   ├── celery_app.py         # Celery app factory
│   │   │   ├── market_tasks.py       # Start/stop market data ingestion
│   │   │   ├── signal_tasks.py       # Scheduled signal generation
│   │   │   ├── news_tasks.py         # News fetch + sentiment (every 15 min)
│   │   │   ├── training_tasks.py     # ML model retraining
│   │   │   └── maintenance_tasks.py  # Backfill, cleanup, health checks
│   │   │
│   │   ├── websocket/
│   │   │   ├── __init__.py
│   │   │   ├── manager.py            # ConnectionManager: room-based broadcasting
│   │   │   └── router.py             # WebSocket route handlers
│   │   │
│   │   ├── models/                   # SQLModel DB models
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── ticker.py
│   │   │   ├── price.py              # TimescaleDB hypertable
│   │   │   ├── signal.py
│   │   │   ├── order.py
│   │   │   ├── trade.py
│   │   │   ├── portfolio.py
│   │   │   ├── watchlist.py
│   │   │   ├── news_sentiment.py
│   │   │   └── model_run.py
│   │   │
│   │   ├── schemas/                  # Pydantic request/response schemas
│   │   │   ├── auth.py
│   │   │   ├── user.py
│   │   │   ├── order.py
│   │   │   ├── signal.py
│   │   │   └── ...
│   │   │
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py             # Settings (pydantic-settings, env-driven)
│   │   │   ├── database.py           # Async SQLAlchemy engine + session factory
│   │   │   ├── redis.py              # Redis connection pool
│   │   │   ├── security.py           # JWT, password hashing utilities
│   │   │   ├── exceptions.py         # Custom exception classes
│   │   │   ├── logging.py            # Structured logging setup (structlog)
│   │   │   └── dependencies.py       # FastAPI dependency injections
│   │   │
│   │   └── middleware/
│   │       ├── __init__.py
│   │       ├── logging_middleware.py  # Request/response logging
│   │       ├── rate_limit.py          # Per-user rate limiting (slowapi)
│   │       └── correlation_id.py      # Attach correlation ID to each request
│   │
│   ├── alembic/                       # DB migrations
│   │   ├── env.py
│   │   ├── versions/
│   │   └── alembic.ini
│   │
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── unit/
│   │   └── integration/
│   │
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/                          # Next.js 14 web app
│   ├── app/
│   │   ├── layout.tsx                 # Root layout (providers, navbar)
│   │   ├── page.tsx                   # Redirect to dashboard
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── register/page.tsx
│   │   ├── dashboard/page.tsx
│   │   ├── screener/page.tsx
│   │   ├── signals/page.tsx
│   │   ├── portfolio/page.tsx
│   │   ├── watchlist/page.tsx
│   │   ├── orders/page.tsx
│   │   ├── settings/page.tsx          # Broker selection, trading mode, API keys
│   │   └── admin/
│   │       ├── layout.tsx             # Admin-only guard
│   │       ├── page.tsx               # Admin dashboard
│   │       ├── users/page.tsx
│   │       ├── data/page.tsx          # Historical backfill, universe management
│   │       ├── models/page.tsx        # ML model management
│   │       └── system/page.tsx        # Health, logs, worker status
│   ├── components/
│   │   ├── ui/                        # Shadcn components
│   │   ├── charts/
│   │   │   ├── PriceChart.tsx         # TradingView Lightweight Charts wrapper
│   │   │   ├── SignalOverlay.tsx      # Signal markers on chart
│   │   │   └── SentimentGauge.tsx
│   │   ├── broker/
│   │   │   └── BrokerSelector.tsx     # Dropdown in top-right navbar
│   │   ├── orders/
│   │   │   ├── OrderPanel.tsx         # Buy/Sell panel
│   │   │   └── OrderConfirmDialog.tsx
│   │   └── layout/
│   │       ├── Navbar.tsx
│   │       └── Sidebar.tsx
│   ├── lib/
│   │   ├── api-client.ts              # Re-exports @ai-trader/shared/api-client (web baseURL + cookie token)
│   │   ├── websocket.ts               # Browser WebSocket client (token via ?token= query param)
│   │   └── utils.ts
│   ├── store/
│   │   ├── authStore.ts               # Zustand auth state (web-only: httpOnly cookie handling)
│   │   ├── priceStore.ts              # Re-exports @ai-trader/shared/store/priceStore
│   │   └── signalStore.ts             # Re-exports @ai-trader/shared/store/signalStore
│   ├── hooks/
│   │   ├── useLivePrice.ts
│   │   ├── useSignalFeed.ts
│   │   └── useOrders.ts
│   ├── Dockerfile
│   ├── package.json
│   └── tsconfig.json
│
├── mobile/                            # React Native (Expo)
│   ├── app/
│   │   ├── _layout.tsx
│   │   ├── (auth)/
│   │   ├── (tabs)/
│   │   │   ├── dashboard.tsx
│   │   │   ├── screener.tsx
│   │   │   ├── signals.tsx
│   │   │   ├── portfolio.tsx
│   │   │   └── settings.tsx
│   ├── components/
│   ├── lib/
│   │   └── websocket.ts               # Mobile WS client (Expo AsyncStorage for token)
│   ├── store/                         # Zustand (mobile-specific adaptors only)
│   ├── app.json
│   └── package.json                   # References packages/shared as workspace dep
│
├── packages/                          # npm workspace — shared internal packages
│   └── shared/
│       ├── package.json               # name: "@ai-trader/shared"
│       ├── tsconfig.json
│       └── src/
│           ├── types/                 # API response types auto-generated from OpenAPI
│           │   ├── signal.ts          # Signal, SignalAction, ComponentScores
│           │   ├── order.ts           # Order, OrderStatus, TradingMode
│           │   ├── ticker.ts          # Ticker, OHLCV
│           │   └── index.ts
│           ├── api-client.ts          # Axios instance factory (base URL injected)
│           └── store/
│               ├── signalStore.ts     # Zustand signal feed (platform-agnostic)
│               └── priceStore.ts      # Zustand live price state
│
├── db_init/
│   ├── 01_init_timescaledb.sql        # Extensions, hypertables
│   └── 02_seed_universe.sql           # Initial ticker list (Nifty 500)
│
├── mlflow/                            # MLflow tracking server config
│   └── docker-entrypoint.sh
│
├── scripts/                           # One-off utility scripts (not in app code)
│   ├── backfill_historical.py
│   └── seed_admin_user.py
│
├── docker-compose.yml
├── docker-compose.override.yml        # Dev overrides (volume mounts, hot reload)
├── package.json                       # Root npm workspace (workspaces: [frontend, mobile, packages/*])
├── tsconfig.base.json                 # Shared TS compiler options
├── .env.example
└── DESIGN.md                          # This document
```

---

## 5. Backend Design

### 5.1 Framework & App Layout

FastAPI application uses the **lifespan** pattern (not deprecated `startup`/`shutdown` events):

```python
# main.py — app factory approach
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB pool, Redis pool, load ML models into memory
    await init_db()
    await init_redis()
    await load_ml_models()
    # Spawn per-worker Redis Pub/Sub listener — every worker process
    # gets its own subscriber so all workers receive every tick simultaneously
    asyncio.create_task(redis_pubsub_listener(connection_manager))
    logger.info("Application started")
    yield
    # Shutdown: graceful cleanup
    await close_db()
    await close_redis()
    logger.info("Application shutdown")

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, ...)
    app.include_router(api_v1_router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/ws")
    register_middleware(app)
    register_exception_handlers(app)
    return app
```

**Key principles:**
- Routes are thin — they validate input and call service layer, nothing else
- Service layer holds all business logic
- DB access only through service layer, never in routes
- All DB operations are async (asyncpg driver)
- All external API calls are async (httpx)

### 5.2 Broker Adapter Pattern

All broker interactions go through a single abstract interface. This means adding a new broker never touches existing code.

```
BrokerAdapter (abstract base)
├── get_live_price(symbol) → LivePrice
├── subscribe_live(symbols, callback) → None
├── get_history(symbol, from, to, interval) → List[OHLCV]
├── place_order(order: OrderRequest) → OrderResponse
├── cancel_order(order_id) → bool
├── get_positions() → List[Position]
└── get_profile() → BrokerProfile

Implementations:
├── AngelOneAdapter   (SmartAPI WebSocket + REST)
├── UpstoxAdapter     (Upstox API v3 WebSocket + REST)
└── NSEFallbackAdapter (yfinance + NSE public feed, no auth)
```

**Dependency injection — not a factory:** Rather than a factory that constructs adapters inline, the adapter is resolved at the FastAPI dependency layer and injected into services. This keeps `order_service.py` and all service layer code completely decoupled from concrete broker implementations and trivially mockable in tests without touching the network layer.

```python
# core/dependencies.py
async def get_broker_adapter(settings: Settings = Depends(get_settings)) -> BrokerAdapter:
    """Resolves the configured broker adapter. Replace with any mock in tests."""
    broker = settings.BROKER_PREFERENCE          # read from env/user config
    creds = decrypt_creds(settings)              # Fernet decryption here only
    if broker == "angel_one":
        return AngelOneAdapter(creds)
    elif broker == "upstox":
        return UpstoxAdapter(creds)
    return NSEFallbackAdapter()

# api/v1/orders.py  — no concrete broker class ever imported here
async def place_order(
    order: OrderRequest,
    broker: BrokerAdapter = Depends(get_broker_adapter),
    svc: OrderService = Depends(get_order_service),
):
    return await svc.place(order, broker)
```

API keys stored in DB using **Fernet symmetric encryption** (cryptography library). Encryption key stored only in server environment variable — never in DB.

### 5.3 Authentication & Authorization

**Token strategy:** Short-lived JWT access token (15 min) + long-lived refresh token (7 days, stored in httpOnly cookie).

**Roles:**
| Role    | Permissions                                                                        |
|---------|------------------------------------------------------------------------------------|
| `admin` | All access, create/deactivate users, system config, model management               |
| `trader`| Own portfolio, own broker connection, own orders, signals, screener, settings      |
| `viewer`| Read-only signals and screener — no order placement, no broker connection required |

**Security features:**
- Passwords hashed with bcrypt (cost factor 12)
- Email verification required before trading enabled
- TOTP 2FA (Google Authenticator compatible) — **mandatory for the admin account, enforced at the application layer** (login blocked until TOTP is configured and verified); cannot be disabled once enabled
- Rate limiting on auth endpoints (5 attempts/min per IP)
- Refresh token rotation on each use
- **JWT revocation via Redis `jti` blocklist** (see below)

**JWT revocation — the stateless trap:**

Standard JWT access tokens are verified mathematically (signature check) — the database is never consulted. This means that if an admin deactivates a user, or a user changes their password, their current 15-minute access token remains cryptographically valid and will pass auth middleware checks until its intrinsic expiry. For a trading platform with live order placement, this 15-minute window represents a real risk.

Fix: every JWT includes a unique `jti` (JWT ID) claim. The auth middleware checks a Redis blocklist on **every request** before granting access.

```
Token lifecycle:
  On issue: JWT payload includes { ..., "jti": "<uuid4>" }

Revocation trigger (any of the following):
  - User password change
  - Admin deactivates user account
  - User explicitly logs out
  - Admin issues "revoke all sessions" command

Revocation action:
  redis.setex(f"blocklist:jti:{jti}", ttl=remaining_token_lifetime_seconds, value="1")
  (TTL = token exp - now; key auto-expires when the token would have expired anyway)

Auth middleware (runs on every protected request):
  1. Decode JWT (verify signature + expiry as normal)
  2. Extract jti claim
  3. Check: redis.get(f"blocklist:jti:{jti}")
  4. If key exists → 401 Unauthorized ("Token revoked")
  5. Otherwise → allow request

Refresh token revocation:
  Refresh tokens (7-day) are stored in the DB (hashed). On revocation,
  the DB row is deleted — all future refresh attempts fail regardless of
  the Redis blocklist state.
```

- Redis lookup adds ~0.5ms per request — negligible vs DB queries
- Blocklist keys are bounded by token TTL — no unbounded growth
- The phrase "All tokens invalidated on password change" now means: all active `jti` values for that user are written to the blocklist simultaneously

**Live trading additional gate:** User must explicitly enable live trading in settings → triggers email OTP confirmation. Cannot be enabled by API alone.

### 5.4 API Design Conventions

- All routes versioned under `/api/v1/`
- All responses use consistent envelope:
  ```json
  {
    "success": true,
    "data": { ... },
    "meta": { "page": 1, "total": 100 }
  }
  ```
  Or on error:
  ```json
  {
    "success": false,
    "error": { "code": "ORDER_REJECTED", "message": "...", "details": {} }
  }
  ```
- Pagination: cursor-based for time-series, offset-based for lists
- All timestamps in ISO 8601 UTC
- Snake_case for JSON keys

### 5.5 WebSocket Architecture

**Authentication:**

Browser WebSockets cannot send custom HTTP headers (e.g. `Authorization: Bearer`) during the initial handshake. Token is passed as a query parameter instead:

```
wss://api.example.com/ws/prices?token=<access_jwt>
```

The server validates the token on connection. Connection is rejected with `4001` close code if the token is missing, invalid, or expired.

**Token expiry while connected (15-min access token):**

A user keeping the dashboard open all day will have their initial 15-min token expire while the WebSocket remains open. The frontend is responsible for keeping the connection authorized:

```
Frontend token refresh loop (inside websocket.ts):
  Every 13 minutes (2 min before expiry):
    1. Call POST /api/v1/auth/refresh → receive new access_token
    2. Send over the open WebSocket channel:
       { "action": "auth_refresh", "token": "<new_jwt>" }
  Server side:
    Validates the new token, updates the connection's auth state
    If invalid: closes connection with 4001 (client must reconnect)
```

This avoids tearing down and re-establishing the WebSocket on every token refresh.

**Two WebSocket channels:**

**1. Prices channel** (`/ws/prices?token=<jwt>`)
```
Client subscribes with: { "action": "subscribe", "symbols": ["RELIANCE", "TCS"] }
Server broadcasts: { "type": "price_tick", "symbol": "RELIANCE", "ltp": 2450.50, "change_pct": 0.5, "timestamp": "..." }
```

**2. Signals channel** (`/ws/signals?token=<jwt>`)
```
Server broadcasts when new signal generated:
{ "type": "signal", "symbol": "TCS", "action": "BUY", "confidence": 0.74, "timestamp": "..." }
```

**ConnectionManager — per-worker, not global:**

When FastAPI runs with multiple worker processes (`uvicorn --workers N` or `gunicorn`), each worker has its own isolated memory space and its own `ConnectionManager` instance. A Redis Pub/Sub push to one worker does not automatically reach clients connected to other workers.

The fix: the lifespan startup event spawns a **dedicated async Redis Pub/Sub listener task inside every worker process**. When Redis publishes a tick, every worker receives it simultaneously and each broadcasts only to the WebSocket clients physically connected to its own `ConnectionManager`. No cross-worker coordination is needed — Redis acts as the shared broadcast medium.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_redis()
    await load_ml_models()
    # Each worker spawns its own subscriber — all receive every Redis message
    asyncio.create_task(redis_pubsub_listener(connection_manager))
    logger.info("Worker started", worker_pid=os.getpid())
    yield
    await close_db()
    await close_redis()
```

`ConnectionManager` only tracks connections belonging to its own worker process. Symbol-level filtering (only broadcast to subscribed clients) happens inside each worker independently.

### 5.6 Background Tasks (Celery)

**Celery Beat Schedule (production):**

| Task                     | Schedule              | Description                              |
|--------------------------|-----------------------|------------------------------------------|
| `start_market_ingestor`  | 9:00 AM IST weekdays  | Start WebSocket feed from broker         |
| `stop_market_ingestor`   | 3:35 PM IST weekdays  | Stop WebSocket feed + flush stale Redis orderbook keys |
| `apply_corporate_actions`| 7:00 PM IST weekdays  | Check for new corporate_actions rows; adjust price_1day history; adjust active user portfolio rows and pending orders for affected tickers |
| `generate_signals`       | Every 5 min (market hrs) | Run ensemble on all watched tickers (**Redis lock — skip if previous run still active**) |
| `fetch_news_sentiment`   | Every 15 min          | RSS crawl + FinBERT scoring              |
| `build_ohlcv_bars`       | Every 1 min           | Aggregate ticks → 1-min candles → DB     |
| `fetch_bhavcopy`         | 6:30 PM IST weekdays (first attempt) | NSE EOD Bhavcopy — validates date header, retries every 15 min if stale |
| `cleanup_old_ticks`      | 2:00 AM daily         | Delete raw tick data older than 7 days   |
| `health_check`           | Every 5 min           | Verify DB, Redis, broker connections     |

**NSE Bhavcopy — date validation and retry loop:**

NSE targets 6:00 PM for Bhavcopy uploads but frequently delays until 7:30–8:00 PM during high-volume sessions or technical issues. A blind download at 6:30 PM will silently ingest yesterday's file if today's hasn't been published yet.

```
fetch_bhavcopy(trading_date: date = today):
  1. Download file from NSE Bhavcopy URL
  2. Parse the date header inside the CSV (first row contains the report date)
  3. If parsed_date != trading_date:
       logger.warning("Bhavcopy for {trading_date} not yet available "
                      "(file date: {parsed_date}). Retrying in 15 min.")
       # Re-enqueue self with a 15-minute countdown (not a Celery retry — use
       # apply_async(countdown=900) to avoid consuming beat schedule slots)
       fetch_bhavcopy.apply_async(args=[trading_date], countdown=900)
       return  # Exit current task cleanly
  4. If parsed_date == trading_date:
       Proceed with ingestion → price_1day table
       logger.info("Bhavcopy ingested for {trading_date}")

  Hard stop: if trading_date is still not available by 10:00 PM IST,
  log ERROR and fire a Discord alert. Do not retry beyond midnight.
```

**Distributed lock — preventing the dogpile effect on `generate_signals`:**

Sequentially loading, inferencing, and unloading 3 ML models across 500 tickers can take longer than the 5-minute Celery beat interval. If the beat fires while the previous run is still active, tasks will stack up in `high_priority` queue and eventually execute concurrently — directly violating the sequential GPU loading strategy and triggering an OOM crash.

Every `generate_signals` invocation must acquire a Redis lock before doing any work:

```python
# tasks/signals.py
LOCK_KEY = "lock:generate_signals"
LOCK_TTL = 600  # 10 min hard ceiling — auto-released if task crashes

@celery_app.task(queue="high_priority")
def generate_signals():
    acquired = redis_client.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL)
    if not acquired:
        logger.warning("generate_signals skipped — previous run still holds lock")
        return  # graceful abort, no re-queue
    try:
        _run_inference_pipeline()
    finally:
        redis_client.delete(LOCK_KEY)  # release immediately on success or exception
```

- `NX` (set-if-not-exists) is the atomic gate — no race condition possible
- `EX 600` is a hard TTL safety net: if the worker dies without reaching `finally`, the lock auto-expires and unblocks the next cycle
- The skip is a `WARNING` log, not an error — skipping one cycle is acceptable; OOM crashes are not
- The same pattern applies to `fetch_news_sentiment` if FinBERT inference is included in its run (use `lock:fetch_news_sentiment`, TTL 300)

**Market data ingestor resilience (`ingestor.py`):**

Broker WebSockets drop frequently during peak volatility windows (market open 9:15 AM, close 3:00 PM). The ingestor must treat disconnection as a normal operating condition, not an error:

```
Reconnection loop (inside ingestor.py, not via Celery retry):
  on disconnect:
    1. Immediately flush all orderbook:{symbol} Redis keys (DEL) — prevents
       paper engine from filling orders against frozen stale prices
    2. Log WARNING: {broker, disconnect_reason, timestamp}
    3. Wait: exponential backoff — 1s → 2s → 4s → 8s → cap at 60s
    4. Re-authenticate with broker if token expired
    5. Re-subscribe to all symbols from the active universe
    6. Log INFO: reconnected, resume normal operation

  health_check Celery task (every 5 min) also verifies:
    - Last tick received timestamp < 2 min ago (during market hours)
    - If stale: trigger reconnect attempt, alert via log CRITICAL
```

The stale-key flush on disconnect is non-negotiable: a 5-second old order book in Redis looks valid to the paper engine but reflects pre-spike prices.

**Watchlist price alert evaluation:**

The ingestor is the correct place to evaluate `watchlist.alert_price` thresholds — it already processes every tick and is the earliest point in the pipeline where the LTP is known. Polling from a Celery beat task would add 1-min+ latency to price alerts.

```
Ingestor startup (and on any PATCH /watchlist/:symbol to update alert_price):
  1. Load all active alerts from DB:
     SELECT user_id, symbol, alert_price, alert_direction
     FROM watchlist
     WHERE alert_price IS NOT NULL AND alert_fired_at IS NULL
  2. Cache in Redis:
     HSET alerts:{symbol} "{user_id}:{watchlist_id}" "ABOVE:{price}"  -- or BELOW
     (one hash per symbol; field = composite key; value = direction+threshold)

On every incoming tick for {symbol}:
  1. HGETALL alerts:{symbol}           ← O(N alerts for symbol), typically 0–5
  2. For each entry:
       if direction == "ABOVE" and ltp >= alert_price: fire
       if direction == "BELOW" and ltp <= alert_price: fire
  3. On fire:
       a. HDEL alerts:{symbol} "{user_id}:{watchlist_id}"  ← remove from cache immediately
          (prevents double-fire on the next tick before DB write lands)
       b. Enqueue Celery task (high_priority queue):
          send_watchlist_alert.delay(user_id=user_id, symbol=symbol,
                                     alert_price=alert_price, ltp=ltp,
                                     direction=direction)

send_watchlist_alert Celery task:
  1. UPDATE watchlist SET alert_fired_at = now()
     WHERE user_id = ? AND symbol = ? AND alert_fired_at IS NULL
     -- idempotent: if fired_at already set (e.g. race with reconnect reload), no-op
  2. Fetch all active expo_push_tokens for user_id
  3. POST to Expo Push API:
     { "to": token, "title": "Price Alert: {symbol}",
       "body": "{symbol} touched ₹{ltp} (alert: {direction} ₹{alert_price})" }
  4. Handle DeviceNotRegistered response → mark token is_active = false (see Section 13.1)
```

**Alert reload on reconnect:** When the ingestor reconnects after a broker disconnect, it must re-run step 1 (load from DB and repopulate Redis) because the `DEL orderbook:{symbol}` flush also clears nothing for alerts — but a full Redis flush or crash could. The safe pattern: always reload alerts from DB on ingestor startup, not from Redis.

**Task queues (separate Celery queues for priority):**
```
high_priority    → signal generation, order status updates
default          → news sentiment, data ingestion
low_priority     → model training, backfill, maintenance
```

### 5.7 Paper Trading Engine

Paper trading simulates orders using real live prices from Redis. The engine must never use LTP (Last Traded Price) for fill simulation — LTP is historical the moment it's recorded, and using it produces unrealistically optimistic paper P&L that diverges severely from live performance in volatile or low-liquidity conditions.

**Order matching logic (bid/ask spread simulation):**
- **Market BUY**: filled at current Redis `orderbook:{symbol}` → `best_ask` (lowest ask price)
- **Market SELL**: filled at current Redis `orderbook:{symbol}` → `best_bid` (highest bid price)
- **Limit order**: queued; checked every 1-min candle — BUY triggers when candle `low ≤ limit_price`, SELL when `high ≥ limit_price`
- **SL order**: triggered when `best_bid` (for SELL SL) or `best_ask` (for BUY SL) crosses the stop price, then filled as market
- **Quantity constraint**: if `orderbook` quantity at best bid/ask is less than the order quantity, partial fill is simulated for the available quantity; remainder stays open as a pending order
- **Order size cap**: maximum simulated order quantity is capped at **25% of the top-1 bid or ask quantity** (whichever side is relevant). Orders exceeding this threshold are rejected at submission with a clear error message (`PAPER_ORDER_TOO_LARGE`). This prevents the engine from attempting to calculate slippage across multiple depth levels that L2 data can't accurately support, and keeps simulated fills realistic relative to actual available liquidity.
- **Fallback**: If order book data in Redis is stale (TTL expired), the order is **rejected** rather than filled against frozen prices. A WARNING is logged and the user sees `ORDERBOOK_STALE` as the rejection reason. Never fall back to LTP for fill simulation.

**Per-user virtual account:**
- Default balance: ₹10,00,000 (configurable by admin)
- Tracks: cash balance, holdings, realized P&L, unrealized P&L
- Brokerage simulation: 0.03% per trade (matches typical discount broker)

**Paper vs Live isolation:** `order_service.py` checks `user.trading_mode` before routing:
```python
if user.trading_mode == TradingMode.PAPER:
    return await paper_engine.place_order(order)
else:
    adapter = broker_factory.get_adapter(user)
    return await adapter.place_order(order)
```

**Idempotency — duplicate order prevention:**

If a mobile user taps "Buy" and loses connectivity before receiving the `200 OK`, the client has no way to know whether the order was placed. Tapping again produces a duplicate order. This is prevented with an idempotency key:

```
Client side (every order attempt):
  Generate UUID v4 idempotency_key and attach as header: Idempotency-Key: <uuid>
  Store key locally; on retry, send the SAME key (not a new one)

Server side (order_service.place_order):
  1. Check Redis: GET idempotency:{user_id}:{idempotency_key}
  2. If found: return the cached previous response immediately (no re-execution)
  3. If not found:
     a. Process the order normally
     b. SET idempotency:{user_id}:{idempotency_key} = {order_id, status} EX 300 (5 min TTL)
     c. Return response
```

- TTL of 5 minutes covers all realistic network retry windows
- The key is scoped to `user_id` so keys cannot be shared or spoofed across accounts
- Applies to both paper and live order placement paths

### 5.8 Live Order Routing

For live trades:
1. User must have `trading_mode = LIVE` and `is_live_trading_enabled = True`
2. Broker adapter validates API key is still valid (cached 5-min)
3. Order submitted to broker API
4. Broker returns order ID → stored in DB with status `PENDING`
5. **Order status is updated via broker postback webhook (not polling)** — see below
6. Final status written to DB and pushed to user via WebSocket

**Order Status — Webhook-First Design:**

Both Angel One SmartAPI and Upstox support postback URLs (webhooks) that the broker calls whenever an order status changes. This is the primary mechanism.

```
Broker (Angel One / Upstox)
  │  POST /api/v1/webhooks/order-update  (on any status change)
  ▼
FastAPI webhook endpoint
  ├── Verify request signature (HMAC — each broker provides a secret)
  ├── Update order status in DB
  └── Push update to user via WebSocket (real-time UI refresh)
```

**Postback URL configuration:**
- Registered once in the broker's developer portal (Angel One / Upstox dashboard)
- In development: use **Cloudflare Tunnel** (`cloudflared tunnel`) — provides a free, persistent URL that survives tunnel restarts. This means the broker postback URL only needs to be registered in the developer portal once and never changes between development sessions. ngrok free tier randomizes the URL on every restart, requiring a manual portal update each time — avoid it for webhooks.
  ```bash
  cloudflared tunnel --url http://localhost:8000
  # e.g. https://your-name.trycloudflare.com  — stable across restarts
  ```
- In production: the Indian-region backend URL is registered permanently

**Webhook race condition — early-arrival postbacks:**

Indian brokers (especially Angel One during high-liquidity sessions) execute market orders fast enough that the postback webhook can arrive at `POST /api/v1/webhooks/order-update` **before** the initiating REST request has finished committing the `PENDING` row to PostgreSQL. Naively querying `live_orders WHERE broker_order_id = ?` at this point finds nothing and silently drops the update — leaving the order permanently stuck on `PENDING`.

Mitigation — Celery retry with countdown:

```python
# backend/app/api/v1/webhooks.py (simplified)
@router.post("/order-update")
async def order_update_webhook(request: Request):
    payload = OrderUpdatePayload(**await request.json())

    updated = await _update_order(payload)
    if not updated:
        # Row not committed yet — schedule a retry in 3 seconds
        retry_order_update.apply_async(
            args=[payload.orderid, payload.status, payload.filledshares, payload.averageprice],
            countdown=3,
        )

    return {"received": True}   # always 200 — prevents broker from re-sending
```

```python
# backend/app/tasks/webhook_retry.py
@celery_app.task(name="app.tasks.webhook_retry.retry_order_update",
                 max_retries=3, default_retry_delay=3)
def retry_order_update(broker_order_id, broker_status, filledshares, averageprice):
    with get_sync_session() as session:
        row = session.exec(
            select(LiveOrder).where(LiveOrder.broker_order_id == broker_order_id)
        ).first()
        if row is None:
            if self.request.retries < self.max_retries:
                raise self.retry()
            logger.warning("webhook.retry_exhausted", broker_order_id=broker_order_id)
            return
        # update row …
```

Key properties of this design:
- **Always 200** — `{"received": True}` returned regardless; prevents broker from re-sending the postback
- **Bounded retries**: `max_retries=3` × `default_retry_delay=3 s` covers up to 9 s of commit delay; genuine orphans after that are caught by EOD reconciliation
- **No Redis dependency** — payload args are passed directly to the Celery task; no stash key to manage

**Idempotency — network timeout recovery (`order_tag`):**

If the backend call to the broker's `placeOrder` API raises `TimeoutError` / `ConnectionError` (network hiccup after the order was dispatched), a naive retry would create a duplicate order. The `ordertag` field (Angel One's 20-char idempotency tag) prevents this:

```python
# backend/app/services/live_trade_service.py (simplified)
order_tag = str(order_id)[:20]           # internal UUID → idempotency key
try:
    result = await adapter.place_order(..., order_tag=order_tag)
except (TimeoutError, ConnectionError, OSError):
    # Check if broker actually received the order before retrying
    result = await adapter.get_order_by_tag(order_tag)
    if result is None:
        # Order genuinely did not land — safe to mark TIMEOUT
        await _mark_timeout(session, order_id)
        raise RuntimeError("Order placement timed out and was not confirmed by broker")
    # else: order did land — continue with result (no duplicate)
```

```python
# backend/app/brokers/angel_one.py (simplified)
async def get_order_by_tag(self, order_tag: str) -> Optional[OrderResult]:
    """Scan getOrderBook for an order matching our client-side ordertag."""
    book = await self._smart_api.getOrderBook()
    for order in (book.get("data") or []):
        if order.get("ordertag") == order_tag[:20]:
            return OrderResult(broker_order_id=order["orderid"], ...)
    return None
```

- `status = 'TIMEOUT'` is a valid value in the `live_orders.status` column (fits `VARCHAR(16)`)
- On `TIMEOUT`, the frontend displays an actionable error; the user can verify in their broker app and re-submit if needed

**Fallback — EOD reconciliation only (not real-time polling):**
```
Celery Beat task @ 4:00 PM IST (after market close)
  └── Fetch all live_orders with status PENDING/OPEN from DB
  └── Query broker REST API once for each open order
  └── Reconcile any status mismatches (missed webhooks)
  └── Log discrepancies with WARNING level
```
This replaces per-order polling entirely. The broker API is queried at most once per open order per day for reconciliation, not continuously.

---

## 6. AI/ML Pipeline

### 6.1 Models & Responsibilities

| Model               | Task                            | Input Features                                    | Output                    | Size    | VRAM (fp16) |
|---------------------|---------------------------------|---------------------------------------------------|---------------------------|---------|-------------|
| **LightGBM**        | Daily directional classification | OHLCV, technical indicators, sentiment score       | BUY/SELL/HOLD + prob      | ~10 MB  | CPU only    |
| **TFT**             | Multi-horizon price forecasting  | OHLCV sequences, macro features, time features    | Price range next 1-5 days | ~120 MB | ~1.5 GB     |
| **FinBERT**         | News/headline sentiment          | Tokenized financial text                          | +1 (bull) / -1 (bear)     | ~440 MB | ~900 MB     |
| **LSTM Autoencoder**| Anomaly / pump-dump detection    | OHLCV z-scores, volume spike ratio               | Anomaly score 0–1         | ~30 MB  | ~400 MB     |

**VRAM — Sequential Loading Required (RTX 3050, 4 GB):**

Do NOT load all models simultaneously. PyTorch memory fragmentation, CUDA context overhead, and Windows display driver reservation (~300–500 MB) will cause OOM errors even though 2.8 GB looks safe on paper.

All Celery inference tasks follow strict sequential model loading:
```
1. Load LightGBM     → CPU inference → done (no GPU needed)
2. torch.cuda.empty_cache()
3. Load FinBERT (fp16) → GPU inference on news batch → unload → torch.cuda.empty_cache()
4. Load TFT (fp16)     → GPU inference → unload → torch.cuda.empty_cache()
5. Load LSTM AE (fp16) → GPU inference → unload → torch.cuda.empty_cache()
6. Assemble ensemble result
```

Models are **not** kept loaded in memory between inference runs. Each Celery task loads, infers, and explicitly unloads. This trades ~10s of extra GPU load/unload time (acceptable for a 5-min interval task) for guaranteed OOM-free operation.

For development convenience, a `USE_GPU=false` env flag routes all inference to CPU, slower but zero VRAM risk.

**Ensemble logic:**
```
final_score = (
    0.35 × lgbm_signal
  + 0.35 × tft_signal
  + 0.25 × finbert_sentiment
  + 0.05 × (1 - anomaly_score)     # reduces confidence when anomaly detected
)

if final_score > 0.60 → BUY
if final_score < 0.40 → SELL
else                  → HOLD
```
Weights are configurable in admin panel and stored in DB (not hardcoded).

### 6.2 Training Pipeline

Triggered manually from Admin panel or scheduled weekly (low_priority Celery queue).

```
1. Feature Engineering
   ├── Technical indicators (ta-lib / pandas_ta):
   │     RSI(14), Bollinger Bands, VWAP, ATR(14)
   │     MACD: raw line, signal line, histogram
   │       → also compute derived discrete fields for features_snapshot:
   │           macd_cross_direction: "bullish_cross" | "bearish_cross" | "no_cross"
   │             (bullish_cross  = MACD line crossed above signal line this bar)
   │             (bearish_cross = MACD line crossed below signal line this bar)
   │           macd_histogram_trend: "expanding_positive" | "contracting_positive"
   │                                 | "expanding_negative" | "contracting_negative"
   │       These discrete strings are stored alongside the raw MACD numeric values
   │       in the features_snapshot JSONB column so the LLM explainability prompt
   │       can inject them directly without doing arithmetic on floats.
   ├── Macro features: Nifty 50 daily return, USD/INR, crude oil price (yfinance)
   ├── Rolling sentiment: 24h avg FinBERT score per ticker
   ├── Options data: Put/Call ratio (Upstox) — if available
   └── Calendar features: day of week, week of year, days to expiry

2. Train LightGBM
   └── Labels: next-day direction (1/-1/0) with 0.5% minimum move threshold
   └── Cross-validation: time-series split (no data leakage)

3. Train TFT
   └── Sequence length: 60 days input → 5 days output
   └── Trainer: PyTorch Lightning (GPU auto-detect)
   └── Early stopping: patience 5 epochs

4. LSTM Autoencoder
   └── Trained on "normal" data (exclude known pump/dump events)

5. Log all runs to MLflow (metrics, params, artifacts)
6. Promote best model version via MLflow API
7. Reload models in FastAPI without restart (via model registry check on inference)
```

### 6.3 Inference Pipeline

Called by `generate_signals` Celery task every 5 minutes during market hours.

```
For each ticker in universe:
  1. Pull last 60 days of 1-day OHLCV from TimescaleDB
  2. Pull latest sentiment score from Redis (or DB if Redis miss)
  3. Run LightGBM → signal + prob
  4. Run TFT → forecasted price range
  5. Run LSTM Autoencoder → anomaly score
  6. Ensemble → final signal + confidence
  7. Write to signals table
  8. Publish to Redis channel `signals:{ticker}`
  9. WebSocket manager broadcasts to subscribed clients
```

### 6.4 MLflow Model Registry

Self-hosted MLflow server, backed by PostgreSQL (same DB, separate schema).

- Each training run logs: accuracy, precision, recall, F1, Sharpe ratio on test period
- Model stages: `Staging` → `Production` → `Archived`
- Only `Production` models are loaded for inference
- Admin panel shows all MLflow runs with metrics comparison

### 6.5 Signal Explainability (LLM Integration)

Raw ensemble confidence scores (`lgbm_prob: 0.71`, `finbert_score: 0.62`, etc.) are statistically meaningful but opaque to end-users. A user seeing a BUY signal on RELIANCE with 68% confidence has no intuition for whether the driver is price momentum, a positive earnings headline, or an anomaly score drop.

**Approach: lightweight LLM narration of signal context**

After the ensemble produces a final signal, a separate `explain_signal` Celery task (queue: `low_priority`) asynchronously generates a concise human-readable explanation and stores it alongside the signal row.

```
explain_signal(signal_id):
  1. Load signal row from DB (ticker, action, confidence, component scores)
  2. Load features_snapshot JSON (stored with each signal — key technicals:
     RSI, MACD, volume_zscore, price_vs_52w_high, earnings_days_out)
  3. Load top 3 recent news headlines for this ticker (from news_sentiment table,
     last 24h, ordered by |score| DESC)
  4. Construct prompt → call LLM → store result in signals.explanation TEXT column
```

**LLM options (in preference order, all free-tier viable):**

| Option | Model | Cost | Latency |
|--------|-------|------|---------|
| Gemini 2.0 Flash via API | `gemini-2.0-flash` | Free tier: 1,500 req/day | ~1–2s |
| Gemini 1.5 Flash (fallback) | `gemini-1.5-flash` | Free tier: 1,500 req/day | ~1–2s |
| Local quantized (no API cost) | Gemma 3 1B (Q4_K_M, ~700MB) via `llama-cpp-python` | Free, CPU-only | ~5–15s on VPS |

**Recommended default**: Gemini 2.0 Flash — 1,500 free requests/day comfortably covers 500 tickers × 5-min signal cycles during market hours (max ~300 signals/session that meet the BUY/SELL confidence threshold; HOLD signals get no explanation). Add `GEMINI_API_KEY` to `.env`; set `EXPLAINABILITY_BACKEND: gemini | local | disabled` in config.

**Prompt design (signal context injection):**

```
System: You are a concise financial assistant explaining AI trading signals
to retail investors in 2-3 sentences. Be factual, not advisory.
Never recommend buying or selling. Explain what the data shows.

User: The AI generated a BUY signal for {ticker} ({company_name}) with
{confidence:.0%} confidence.

Technical snapshot:
- RSI(14): {rsi} ({rsi > 70 and "overbought" or rsi < 30 and "oversold" or "neutral"})
- MACD: {macd_signal} ({macd_cross_direction})
- Volume today vs 20-day avg: {volume_zscore:+.1f}σ
- Price vs 52-week high: {price_vs_52wh:.1%}

Recent news sentiment ({finbert_score:+.2f} average, scale -1 to +1):
{headline_1}
{headline_2}
{headline_3}

Anomaly score: {anomaly_score:.2f} (0 = normal, >1 = unusual pattern detected)

Explain in 2-3 sentences what drove this signal.
```

**Example output stored in `signals.explanation`:**
> "RELIANCE shows a strong technical setup with RSI at 58 (neutral momentum) and a volume spike 2.3 standard deviations above average, suggesting institutional interest. Recent news sentiment is moderately positive following the Jio subscriber growth announcement. The anomaly detector finds no unusual price pattern, so this appears to be a clean momentum signal rather than a spike event."

**Frontend display:**
- Web: collapsible "Why this signal?" card below each signal row in the Signal Log
- Mobile: tap the signal to open a bottom sheet with the explanation
- If `explain_signal` is still pending (async), show a skeleton loader; if `EXPLAINABILITY_BACKEND=disabled`, hide the card entirely

**Guardrails:**
- The prompt explicitly instructs the LLM to describe, not advise; any output containing phrases like "you should buy" or "I recommend" is detected by a regex filter and replaced with "Explanation unavailable — please review the raw signal data."
- `explanation` column is nullable; a missing explanation never blocks signal delivery
- Gemini quota hit (429) → automatically routes to local Gemma 3 1B; only falls back to `explanation = null` if both API and local model are unavailable (`EXPLAINABILITY_BACKEND=disabled` or `LOCAL_LLM_PATH` not set)

---

## 7. News & Sentiment Pipeline

**Sources (all free, no paid API key):**

| Source                  | Method          | Frequency  |
|-------------------------|-----------------|------------|
| Economic Times Markets  | RSS             | Every 15 min |
| Moneycontrol            | RSS             | Every 15 min |
| Business Standard       | RSS             | Every 15 min |
| Livemint                | RSS             | Every 15 min |
| Google News (per ticker)| `gnews` library | Every 30 min |
| NSE announcements       | NSE RSS         | Every 15 min |
| BSE filings             | BSE API (free)  | Every 30 min |
| Reddit r/IndiaInvestments| PRAW           | Every 30 min |
| StockTwits              | Public REST API | Every 30 min |

**Rate limiting & IP ban resilience:**

All traffic from the DigitalOcean droplet originates from a single static datacenter IP. Reddit and StockTwits have aggressive anti-bot detection; polling from a known datacenter IP at 30-minute intervals will produce HTTP 429 responses or silent shadowbans within days.

Design rules for `fetcher.py`:

```
Per-source circuit breaker:
  - Each source has an independent health state: HEALTHY | BACKOFF | DISABLED
  - On HTTP 429: switch source to BACKOFF; skip all requests to that source
    for backoff_seconds (start 300s, double on repeat 429s, cap at 7200s)
  - On HTTP 5xx or connection error: exponential backoff up to 3 retries,
    then mark BACKOFF and skip until next Celery beat cycle
  - A 429 on one source NEVER aborts other sources — each is fetched independently
  - Log WARNING on 429; log ERROR only after 3 consecutive 429s (potential shadowban)

Reddit / PRAW rules:
  - MUST use a registered Reddit API application (type: "script") —
    personal-use script apps are free and are not subject to the stricter
    OAuth rate limits applied to unregistered scrapers
  - MUST set a compliant User-Agent: "platform:appname:version (by u/username)"
    Requests with generic User-Agents (python-requests, etc.) are deprioritised
    and rate-limited more aggressively by Reddit
  - PRAW handles Reddit's native rate limit headers automatically; do NOT bypass
    or override PRAW's built-in rate limit sleep
  - Store `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME` in .env

StockTwits rules:
  - Public REST API has no official documented rate limit, but datacenter IPs
    are monitored; treat any 429 with the same circuit breaker pattern above
  - If StockTwits is DISABLED for >24h, alert admin via structlog CRITICAL
```

**Pipeline per article:**
```
1. Fetch RSS/scrape → headline + lead paragraph only
   (NOT full body text — FinBERT has a hard 512 sub-word token limit.
    Full articles routinely exceed this. Silent truncation drops the
    conclusion, which often carries the strongest sentiment signal.
    Headline + lead paragraph fits comfortably within 512 tokens and
    contains the highest-density sentiment information.)
2. Deduplicate: SHA256(url + headline) checked in Redis, TTL 48h
3. Named Entity Recognition → extract company names → map to NSE ticker symbols
4. FinBERT inference (headline + lead paragraph, truncation=True as safety net,
   max_length=512) → sentiment score per ticker mentioned
5. Store: news_sentiment table (ticker, timestamp, source, headline, score, url)
6. Update Redis: `sentiment:{ticker}` → rolling 24h weighted average (recent = higher weight)
```

---

## 8. Database Design

### 8.1 PostgreSQL + TimescaleDB (Persistent)

**Relational tables:**

```sql
-- Users & Auth
users (
  id UUID PK,
  email VARCHAR UNIQUE NOT NULL,
  hashed_password VARCHAR NOT NULL,
  full_name VARCHAR,
  role VARCHAR DEFAULT 'trader',              -- admin | trader | viewer
  is_active BOOL DEFAULT true,
  is_email_verified BOOL DEFAULT false,
  is_live_trading_enabled BOOL DEFAULT false,
  totp_secret VARCHAR,                        -- encrypted TOTP secret; NOT NULL enforced at app layer for admin role
  is_totp_configured BOOL DEFAULT false,       -- false until user completes TOTP setup on first login
  is_totp_verified BOOL DEFAULT false,         -- session-level: true after TOTP code entered this session
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)

-- User broker config (one row per user per broker)
user_broker_config (
  id UUID PK,
  user_id UUID FK → users,
  broker VARCHAR NOT NULL,                    -- angel_one | upstox | nse_fallback
  is_primary BOOL DEFAULT false,
  encrypted_api_key TEXT,
  encrypted_api_secret TEXT,
  access_token TEXT,                          -- short-lived, refreshed daily
  token_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)

-- User trading settings
user_settings (
  user_id UUID PK FK → users,
  trading_mode VARCHAR DEFAULT 'paper',       -- paper | live
  paper_balance DECIMAL(15,2) DEFAULT 1000000,
  max_position_pct DECIMAL(5,2) DEFAULT 10.0, -- max % of portfolio per trade
  daily_loss_limit_pct DECIMAL(5,2) DEFAULT 5.0,
  notification_signals BOOL DEFAULT true,
  notification_orders BOOL DEFAULT true,
  updated_at TIMESTAMPTZ
)

-- Ticker universe
tickers (
  symbol VARCHAR PK,                          -- NSE symbol e.g. RELIANCE
  name VARCHAR NOT NULL,
  sector VARCHAR,
  industry VARCHAR,
  exchange VARCHAR DEFAULT 'NSE',
  is_active BOOL DEFAULT true,
  added_at TIMESTAMPTZ
)

-- Portfolio (live holdings per user)
portfolio (
  id UUID PK,
  user_id UUID FK → users,
  symbol VARCHAR FK → tickers,
  quantity INTEGER NOT NULL,
  avg_buy_price DECIMAL(12,2) NOT NULL,
  trading_mode VARCHAR,                       -- paper | live (separate portfolio per mode)
  updated_at TIMESTAMPTZ,
  UNIQUE (user_id, symbol, trading_mode)
)

-- Orders
orders (
  id UUID PK,
  user_id UUID FK → users,
  symbol VARCHAR FK → tickers,
  trading_mode VARCHAR,
  broker VARCHAR,
  broker_order_id VARCHAR,                    -- returned by broker, null for paper
  order_type VARCHAR,                         -- MARKET | LIMIT | SL | SL-M
  transaction_type VARCHAR,                   -- BUY | SELL
  quantity INTEGER NOT NULL,
  price DECIMAL(12,2),                        -- null for market orders
  trigger_price DECIMAL(12,2),
  status VARCHAR DEFAULT 'PENDING',          -- PENDING | OPEN | COMPLETE | REJECTED | CANCELLED
  filled_quantity INTEGER DEFAULT 0,
  avg_fill_price DECIMAL(12,2),
  placed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)

-- Trade history (completed fills)
trades (
  id UUID PK,
  order_id UUID FK → orders,
  user_id UUID FK → users,
  symbol VARCHAR,
  transaction_type VARCHAR,
  quantity INTEGER,
  price DECIMAL(12,2),
  brokerage DECIMAL(10,4),
  pnl DECIMAL(12,2),                         -- realized P&L for SELL trades
  trading_mode VARCHAR,
  traded_at TIMESTAMPTZ
)

-- Watchlist
watchlist (
  id UUID PK,
  user_id UUID FK → users,
  symbol VARCHAR FK → tickers,
  alert_price DECIMAL(12,2),
  added_at TIMESTAMPTZ,
  UNIQUE (user_id, symbol)
)

-- ML model tracking (mirrors MLflow, for quick UI access)
model_runs (
  id UUID PK,
  mlflow_run_id VARCHAR UNIQUE,
  model_name VARCHAR,
  version INTEGER,
  stage VARCHAR,                              -- staging | production | archived
  accuracy DECIMAL(6,4),
  sharpe_ratio DECIMAL(8,4),
  trained_at TIMESTAMPTZ,
  metrics_json JSONB
)

-- Ensemble weights (configurable from admin)
ensemble_config (
  id INTEGER PK DEFAULT 1,                   -- single row
  lgbm_weight DECIMAL(4,3) DEFAULT 0.35,
  tft_weight DECIMAL(4,3) DEFAULT 0.35,
  finbert_weight DECIMAL(4,3) DEFAULT 0.25,
  anomaly_weight DECIMAL(4,3) DEFAULT 0.05,
  buy_threshold DECIMAL(4,3) DEFAULT 0.60,
  sell_threshold DECIMAL(4,3) DEFAULT 0.40,
  updated_at TIMESTAMPTZ,
  updated_by UUID FK → users
)
```

**TimescaleDB hypertables (time-series):**

```sql
-- 1-minute OHLCV bars
price_1min (
  symbol VARCHAR NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  open DECIMAL(12,2),
  high DECIMAL(12,2),
  low DECIMAL(12,2),
  close DECIMAL(12,2),
  volume BIGINT,
  PRIMARY KEY (symbol, timestamp)
)
-- Create hypertable with explicit 1-week chunk interval:
-- SELECT create_hypertable('price_1min', 'timestamp', chunk_time_interval => INTERVAL '1 week');
--
-- Rationale: Nifty 500 at 375 trading min/day = ~187,500 rows/day.
-- 1-week chunks = ~1.3M rows. At ~200 bytes/row uncompressed,
-- the active chunk is ~260 MB, fitting within 1–2 GB VPS RAM.
-- Default chunking (7 days) would also work but being explicit prevents
-- silent regressions if TimescaleDB changes its default in a future version.
-- Compression: compress chunks older than 7 days
-- Retention: drop chunks older than 1 year

-- End-of-day OHLCV (from NSE Bhavcopy)
price_1day (
  symbol VARCHAR NOT NULL,
  date DATE NOT NULL,
  open DECIMAL(12,2),
  high DECIMAL(12,2),
  low DECIMAL(12,2),
  close DECIMAL(12,2),
  adj_close DECIMAL(12,2),              -- adjusted close (split/bonus/dividend adjusted)
  is_adjusted BOOL DEFAULT false,       -- true once corporate-action adjustment has been applied
  volume BIGINT,
  delivery_pct DECIMAL(5,2),            -- from Bhavcopy
  PRIMARY KEY (symbol, date)
)

-- Corporate actions (splits, bonuses, dividends)
corporate_actions (
  id UUID DEFAULT gen_random_uuid() PK,
  symbol VARCHAR NOT NULL,
  ex_date DATE NOT NULL,                -- date on which the action applies
  action_type VARCHAR NOT NULL,         -- SPLIT | BONUS | DIVIDEND
  ratio DECIMAL(10,5),                  -- e.g. 2.0 for 2:1 split, 0.5 for 1:2 consolidation
  dividend_amount DECIMAL(12,4),        -- populated for DIVIDEND type
  is_applied BOOL DEFAULT false,        -- true once historical data has been retroactively adjusted
  source VARCHAR,                       -- bhavcopy | nse_rss | manual
  created_at TIMESTAMPTZ DEFAULT now()
)
-- Indexed on (symbol, ex_date).
--
-- When a new corporate action row is inserted and is_applied = false:
--   A Celery task (low_priority) fires to multiply all historical price_1day rows
--   for that symbol with date < ex_date by the inverse of the ratio (for splits)
--   and sets is_adjusted = true on those rows, then sets is_applied = true.
--   price_1min is NOT retroactively adjusted (too large); models must use
--   price_1day (adj_close) as the price series for training/inference.
--
-- LIVE USER IMPACT — portfolio and orders must also be adjusted:
--   After adjusting price_1day rows, apply_corporate_actions must:
--
--   a) PORTFOLIO rows (for SPLIT / BONUS actions only — not DIVIDEND):
--        UPDATE portfolio
--          SET quantity    = quantity    * ratio,
--              avg_buy_price = avg_buy_price / ratio
--        WHERE symbol = ? AND trading_mode IN ('paper','live');
--      This keeps the user's notional portfolio value identical pre/post split.
--      (For DIVIDEND: portfolio value is unchanged; no quantity/price adjustment.)
--
--   b) PENDING / OPEN orders for the affected ticker:
--        For LIMIT and SL orders: the limit_price is now stale after a split.
--        Safe choices (in order of preference):
--          1. Cancel the order automatically and notify the user via WebSocket
--             + Discord alert with reason "Cancelled: corporate action ex-date".
--          2. Adjust limit_price = limit_price / ratio and quantity = quantity * ratio
--             if the broker API supports in-flight order modification (Angel One does not).
--        MARKET orders are unaffected (they execute at prevailing price).
--        Default behaviour: CANCEL and notify. Never silently leave a stale LIMIT
--        order live — a 2:1 split doubles the apparent fill value, causing unintended
--        large buys/sells at market open.
--
--   Execution order within apply_corporate_actions:
--     1. Adjust price_1day (adj_close retroactive)
--     2. Cancel/notify affected LIMIT/SL orders
--     3. Adjust portfolio rows
--     4. Set corporate_actions.is_applied = true
--   All steps wrapped in a single DB transaction; rollback on any failure.
--
-- STRICT RULE — 1-minute data and split ex-dates:
--   price_1min bars that cross a split ex-date will diverge violently from
--   the adjusted daily bars (a 2:1 split looks like a 50% crash in unadjusted
--   1-min data). To prevent false signals:
--     a) All ML inference pipelines must ONLY use price_1day (adj_close) as
--        the canonical price series. price_1min is for live signal timing only.
--     b) Any intraday feature derived from price_1min must be bounded to
--        bars with timestamp >= ex_date of the most recent unapplied action
--        for that symbol (i.e., never cross a split boundary in a 1-min window).
--     c) On the day of a corporate action ex-date, price_1min data is considered
--        unreliable until market close; intraday signal generation is suspended
--        for that symbol for the remainder of the session.

-- Market depth is NOT persisted to PostgreSQL.
-- L2 order book data updates multiple times per second per symbol.
-- Writing tick-level arrays to a relational DB at this frequency
-- causes severe disk I/O degradation and storage bloat even with
-- TimescaleDB compression. See Redis section for live order book storage.
--
-- If historical L2 data is needed for model research in future:
-- write to flat Parquet/binary files via Celery task (not to this DB).

-- News sentiment
news_sentiment (
  id UUID DEFAULT gen_random_uuid(),
  symbol VARCHAR NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  source VARCHAR,
  headline TEXT,
  url TEXT,
  sentiment_score DECIMAL(4,3),            -- -1.0 to 1.0
  PRIMARY KEY (symbol, timestamp, id)
)

-- AI signals
signals (
  symbol VARCHAR NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  signal VARCHAR NOT NULL,                 -- BUY | SELL | HOLD
  confidence DECIMAL(4,3),
  lgbm_score DECIMAL(4,3),
  tft_score DECIMAL(4,3),
  finbert_score DECIMAL(4,3),
  anomaly_score DECIMAL(4,3),
  features_snapshot JSONB,                 -- all feature values at inference time (raw numerics + derived discrete strings)
  model_version JSONB,                     -- which model version was used
  explanation TEXT,                        -- LLM-generated human-readable summary (nullable; async, non-blocking)
  actual_return_1d DECIMAL(8,4),           -- filled by EOD job: actual % price move next day (for accuracy tracking)
  actual_return_5d DECIMAL(8,4),           -- filled by EOD job: actual % price move over 5 days
  outcome_label VARCHAR,                   -- filled by EOD job: "correct" | "incorrect" | "hold_neutral"
  PRIMARY KEY (symbol, timestamp)
)
```

### 8.2 Redis (In-Memory / Cache)

> **Note:** Pipeline task **status** is stored in PostgreSQL (`pipeline_task_status` table), not Redis. Redis holds only ephemeral per-task log lines.

| Key Pattern                  | Value                          | TTL        | Purpose                          |
|------------------------------|--------------------------------|------------|----------------------------------|
| `price:ltp:{symbol}`         | `{ltp, change, timestamp}`     | 30s        | Latest tick for screener/API     |
| `orderbook:{symbol}`         | `{best_bid, best_bid_qty, best_ask, best_ask_qty, bids[5], asks[5], ts}` | 5s | Live L2 order book — used by paper engine for realistic bid/ask fills; never written to PostgreSQL |
| `sentiment:rolling:{symbol}` | Float (-1.0 to 1.0)            | 1h         | Rolling 24h sentiment average    |
| `signal:latest:{symbol}`     | `{signal, confidence, ts}`     | 10 min     | Latest signal for quick access   |
| `session:{user_id}`          | User session metadata          | 15 min     | JWT session cache                |
| `broker:token:{user_id}`     | Broker access token            | Until expiry | Avoid re-auth on every request |
| `news:seen:{content_hash}`   | `{url, scored_at}`             | 48h        | Deduplication for news articles. Hash is `SHA256(url + headline)` — not URL alone. Breaking news articles frequently update their headline/body at the same URL; hashing URL+headline ensures a materially updated article triggers a fresh FinBERT re-score rather than being silently skipped. |
| `ratelimit:{ip}:{endpoint}`  | Request count                  | 1 min      | Rate limiting — key uses **true client IP** extracted from `CF-Connecting-IP` header (Cloudflare) or `X-Forwarded-For` (generic proxy), not the proxy/tunnel IP. FastAPI must be configured with `ProxyHeadersMiddleware` (or slowapi's `get_remote_address` override) to trust and extract the real IP. Without this, one user's rate limit blocks the entire platform. |
| `idempotency:{user_id}:{key}` | `{order_id, status}`          | 5 min      | Duplicate order prevention — client sends `Idempotency-Key` UUID header per order attempt; server returns cached response on retry instead of re-executing |
| `otp:live_trading:{user_id}` | 6-digit OTP string             | 600s       | Single-use OTP for live trading enablement (`POST /users/me/live-trading/enable` → `confirm`). Key is deleted immediately on first successful verification; TTL expiry = OTP expiry. |
| `pipeline:logs:{task_name}`  | List of structured log lines   | 7 days     | Per-task log lines (capped at 500) for the Admin pipeline log viewer. Written by `append_task_log()` in `task_utils.py`. |
| `pub:prices`                 | Pub/Sub channel                | —          | Broadcasting live prices         |
| `pub:signals`                | Pub/Sub channel                | —          | Broadcasting new signals         |

---

## 9. Logging Strategy

Every layer uses **structlog** for structured JSON logging. No raw `print()` or unstructured `logging.info("string")` anywhere in the codebase.

### Log Levels

| Level    | When to use                                                     |
|----------|-----------------------------------------------------------------|
| DEBUG    | Detailed technical trace (disabled in production by default)    |
| INFO     | Normal operations: request received, task started, model loaded |
| WARNING  | Recoverable issues: retries, fallback broker used, cache miss   |
| ERROR    | Failures that need attention: order rejected, API error         |
| CRITICAL | System-level failures: DB down, Redis disconnected              |

### Log Format (JSON, production)

```json
{
  "timestamp": "2026-04-16T10:30:00.123Z",
  "level": "info",
  "logger": "app.services.order_service",
  "correlation_id": "abc-123-def",
  "user_id": "uuid-here",
  "event": "order_placed",
  "symbol": "RELIANCE",
  "order_type": "MARKET",
  "quantity": 10,
  "trading_mode": "paper",
  "duration_ms": 45
}
```

### Correlation ID

Every HTTP request gets a unique `X-Correlation-ID` header (generated if not present). This ID flows through:
- Request log
- All service logs during that request
- All Celery task logs spawned by that request
- Response header (for client-side debugging)

This makes it trivial to trace a single user action across all log lines.

### Log Outputs

| Environment | Output                | Format     |
|-------------|-----------------------|------------|
| Development | Console (stdout)      | Colorful, human-readable (structlog ConsoleRenderer) |
| Production  | stdout (Docker)       | JSON (structlog JSONRenderer) |
| Production  | `/logs/app.log`       | JSON, rotated daily, 30-day retention |

Docker Compose captures stdout JSON logs. In production on VPS, use `docker logs` or ship to Grafana Loki (free, self-hosted) for search.

### Celery Task Logging

Each Celery task logs:
- Task start: `{task_name, task_id, args_summary}`
- Task success: `{task_name, task_id, duration_ms}`
- Task failure: `{task_name, task_id, error, traceback}`

### What is NOT logged (security)
- Passwords (even hashed)
- API keys or secrets (even encrypted)
- JWT tokens
- Full order book data (too verbose)
- **WebSocket `?token=` query parameter** — standard HTTP request loggers record the full URL including query parameters. Every WebSocket handshake (`GET /ws/prices?token=<jwt>`) would write an active JWT into `app.log` in plaintext. The `logging_middleware.py` must strip or mask this before emitting the request log:

  ```python
  import re
  _TOKEN_RE = re.compile(r'([?&]token=)[^&\s#]+')

  def sanitize_url(url: str) -> str:
      """Replace ?token=<value> with ?token=*** in logged URLs."""
      return _TOKEN_RE.sub(r'\1***', url)
  ```

  Applied in `logging_middleware.py` before `logger.info("request", path=sanitize_url(request.url))`. This applies to both the WebSocket upgrade path and any URL that happens to carry a token query parameter.

### 9.1 Active Alerting

Structured logs are passive — they require someone to look. If the Celery beat scheduler dies silently at 9:30 AM or the DigitalOcean droplet OOMs, no amount of JSON logs will wake you up. Active alerting fires outbound notifications automatically when critical conditions are detected.

**Alerting channel: Discord webhook (free, no bot required)**

A private Discord server with a `#ai-trader-alerts` channel. Discord's incoming webhook URL is stored in `.env` as `DISCORD_WEBHOOK_URL`. Alerts are fired by the `health_check` Celery task and Docker healthchecks via a thin `alerting.py` utility:

```python
# core/alerting.py
from datetime import datetime, timezone
import httpx

async def fire_alert(level: str, title: str, body: str):
    """POST to Discord webhook. Non-blocking — failure to alert is logged but never raises."""
    payload = {
        "embeds": [{
            "title": f"[{level.upper()}] {title}",
            "description": body,
            "color": 0xFF0000 if level == "critical" else 0xFF9900,  # red | orange
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(settings.DISCORD_WEBHOOK_URL, json=payload)
    except Exception:
        logger.error("Failed to send Discord alert", title=title)
```

**Alert rules:**

| Condition | Trigger | Severity |
|-----------|---------|----------|
| No broker WebSocket tick received for >5 min during market hours (09:15–15:30 IST) | `health_check` Celery task | CRITICAL |
| `generate_signals` lock held for >8 min (approaching 10-min hard TTL ceiling) | `health_check` | WARNING |
| Celery beat has not run any task in >10 min (beat scheduler down) | `health_check` (checks Redis key `beat:last_heartbeat`) | CRITICAL |
| PostgreSQL `pg_isready` healthcheck fails | Docker Compose `healthcheck` → script calls webhook | CRITICAL |
| Redis ping fails | `health_check` | CRITICAL |
| News pipeline source in DISABLED state for >24h | `fetch_news_sentiment` task epilogue | WARNING |
| Any live order API call returns 5xx three times in a row | `order_service.py` | ERROR |
| Gemini free tier quota hit (429 on explainability) | `explain_signal` task | WARNING |
| Database backup (pg_dump) cron job exits non-zero | Cron script | CRITICAL |

**Celery beat heartbeat (dead-man's switch):**

Celery beat does not expose a native health endpoint. The `health_check` task detects beat death by checking a Redis key that beat must renew:

```
Celery beat config (celeryconfig.py):
  Add a "beat_heartbeat" task every 1 minute:
    redis.setex("beat:last_heartbeat", 120, "1")  # TTL = 2 min

health_check task (every 5 min):
  if not redis.exists("beat:last_heartbeat"):
    fire_alert("critical", "Celery Beat Down",
      "No heartbeat key found. Beat scheduler may have crashed.")
```

If beat dies, the heartbeat key expires within 2 minutes (its TTL), and the next `health_check` — if workers are still alive — fires the alert. If workers are also dead, the Docker `healthcheck` on the Celery container catches it.

**Docker healthchecks (docker-compose.yml):**

```yaml
celery-worker-high:
  healthcheck:
    test: ["CMD", "celery", "-A", "app.celery_app", "inspect", "ping", "-d", "celery@$$HOSTNAME"]
    interval: 60s
    timeout: 10s
    retries: 3
    start_period: 30s

postgres:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
    interval: 30s
    timeout: 5s
    retries: 5
```

Docker Compose does not natively fire webhooks on healthcheck failure. The `health_check` Celery task covers this gap for application-level services. For the PostgreSQL container itself (if the whole container is down and Celery can't reach it), a separate lightweight cron script on the VPS host polls `docker inspect` and fires the webhook directly.

---

## 10. Configuration Management

All config via environment variables. **No config values hardcoded anywhere.**

`core/config.py` uses `pydantic-settings`:

```python
class Settings(BaseSettings):
    # App
    APP_ENV: Literal["development", "production"] = "development"
    DEBUG: bool = False
    SECRET_KEY: str                          # JWT signing key
    ENCRYPTION_KEY: str                      # Fernet key for broker API keys

    # Database
    DATABASE_URL: str                        # postgresql+asyncpg://...
    REDIS_URL: str                           # redis://...

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ML
    MODELS_DIR: str = "/app/models"
    USE_GPU: bool = True

    # Celery
    CELERY_BROKER_URL: str                   # same as REDIS_URL
    CELERY_RESULT_BACKEND: str

    # MLflow
    MLFLOW_TRACKING_URI: str

    # Feature flags
    LIVE_TRADING_ENABLED: bool = False       # global kill switch
    NEWS_PIPELINE_ENABLED: bool = True

    class Config:
        env_file = ".env"
```

`.env.example` committed to repo. `.env` in `.gitignore` always.

### Secrets Management Across Environments

The same codebase runs in three environments (development, staging, production), each with different secrets. The management strategy per environment:

| Environment | Secrets delivery method | Notes |
|---|---|---|
| **Development** (laptop) | `.env` file in project root | Never committed; `.gitignore` enforced |
| **Staging** (AWS EC2 / DO droplet) | `.env` file on server, copied manually via `scp` once | Stored only on the server filesystem, not in repo or CI |
| **Production** (DO Bangalore droplet) | `.env` file on server + Docker Compose `env_file` directive | Same pattern; rotated manually when secrets change |

**Rules that apply to all environments:**
- `.env` is always in `.gitignore` — CI/CD pipelines (if added later) must inject secrets via environment variables, never via committed files
- `SECRET_KEY`, `ENCRYPTION_KEY`, broker API credentials, and `RCLONE_B2_KEY` are **never** logged (enforced by the logging middleware blocklist in Section 9)
- The `.env.example` file contains only placeholder values (e.g., `SECRET_KEY=changeme`) and documents every required variable — this is the only env-related file committed to version control
- When rotating a secret (e.g., JWT `SECRET_KEY`): update `.env` on server → restart Docker Compose services → all existing sessions are invalidated (expected behavior)
- Vercel (frontend) secrets are set via the Vercel dashboard environment variables UI — `NEXT_PUBLIC_API_URL` and any frontend-only keys only; no backend secrets ever touch Vercel

---

## 11. Error Handling Strategy

### Custom Exception Hierarchy

```python
class AppException(Exception):
    status_code: int
    error_code: str
    message: str

class AuthException(AppException): ...       # 401/403
class ValidationException(AppException): ... # 422
class BrokerException(AppException): ...     # 502 (broker API failure)
class InsufficientFundsException(AppException): ... # 400
class OrderRejectedException(AppException): ... # 400
class ModelNotLoadedException(AppException): ... # 503
```

### Global Exception Handler

Registered in `main.py`. Catches all `AppException` subclasses and returns consistent error envelope. Logs all exceptions with `ERROR` level and correlation ID. Unexpected exceptions return `500` and log with full traceback.

### Broker API Failures

Broker APIs can be flaky. Strategy:
- Wrap all broker calls in retry logic (tenacity library): 3 retries, exponential backoff
- If primary broker fails after retries → fall back to `NSEFallbackAdapter` for read-only operations (cannot fall back for order placement)
- Log warning with broker name, ticker, error message

---

## 12. Frontend Design (Web)

### Page Map

| Route              | Access  | Description                                      |
|--------------------|---------|--------------------------------------------------|
| `/login`           | Public  | Login form                                       |
| `/register`        | Public  | Registration form                                |
| `/dashboard`       | Auth    | Portfolio overview, Signal feed, Live chart      |
| `/screener`        | Auth    | Filter stocks by fundamentals + signal strength  |
| `/signals`         | Auth    | Signal log with FinBERT sentiment overlay        |
| `/portfolio`       | Auth    | Holdings, P&L, trade history                     |
| `/watchlist`       | Auth    | Watched tickers with price alerts                |
| `/orders`          | Auth    | Open orders, order history, one-tap buy/sell     |
| `/settings`        | Auth    | Broker, trading mode, API keys, risk settings    |
| `/admin/*`         | Admin   | All admin panel pages                            |

### Key UI Components

**BrokerSelector** (navbar, top-right):
- Dropdown showing: Angel One ● | Upstox | NSE Fallback
- Shows connection status (green dot = connected, red = error)
- Clicking opens a modal to connect/disconnect and enter API keys
- Saves preference to user settings via API

**PriceChart** (TradingView Lightweight Charts):
- OHLCV candlestick chart
- Signal overlays (BUY = green arrow up, SELL = red arrow down)
- Sentiment line series below (FinBERT rolling score)
- Switching between 1-min / 1-day intervals

**OrderPanel**:
- BUY (green) / SELL (red) tabs
- Order type dropdown: Market / Limit / SL / SL-M
- Quantity input with quick +/-10 buttons
- Shows: available margin, estimated order value
- Paper/Live badge prominently shown to avoid confusion
- Confirmation dialog required for live trades > ₹10,000

**WebSocket Disconnect Overlay** (applies to both web and mobile):

When the client's WebSocket connection to the FastAPI backend drops, `priceStore` and `signalStore` freeze on the last known tick. A user placing an order against frozen prices risks a significant fill discrepancy. The client must handle this explicitly:

- `websocket.ts` tracks connection state: `CONNECTED | RECONNECTING | DISCONNECTED`
- On disconnect: state transitions to `RECONNECTING` immediately
- A full-screen semi-transparent overlay renders over `PriceChart` and `OrderPanel` with: **"Market data disconnected — Reconnecting..."** + a spinner
- All order submission buttons are **disabled** while in `RECONNECTING` or `DISCONNECTED` state — enforced in the `OrderPanel` component, not just visually
- Automatic reconnect with exponential backoff (mirrors backend ingestor strategy: 1s → 2s → 4s → cap 30s)
- On successful reconnect: overlay dismisses, Zustand stores flush stale cached prices and re-populate from the first incoming WebSocket tick
- If reconnect fails after 5 attempts: state transitions to `DISCONNECTED` with a "Connection lost. Refresh the page." message

---

## 13. IP Rotation Library (Personal Use)

> **Status**: Planned — to be implemented before the Mobile App phase.  
> **Scope**: Internal-only Python library (`backend/app/lib/ip_rotator.py`) for personal, non-commercial use on this closed system.

### 13.1 Purpose

The news pipeline fetches per-symbol articles from Google News (`gnews`) and Yahoo Finance (`yfinance`). Both sources implement IP-based rate limiting. This library provides transparent, pluggable outbound IP rotation so the Celery news task can query more symbols per cycle without hitting rate caps.

RSS feeds (ET Markets, Moneycontrol, Business Standard, etc.) do **not** require rotation — they are open feeds with no per-IP limits and are excluded from this library's scope.

---

### 13.2 Architecture

```
backed/app/lib/
└── ip_rotator/
    ├── __init__.py          # Public API: get_session(), rotate()
    ├── base.py              # Abstract RotatorBackend
    ├── proxy_list.py        # Backend: static proxy list (round-robin / random)
    ├── tor.py               # Backend: Tor SOCKS5 (stem controller)
    ├── vpn_cli.py           # Backend: OS-level VPN CLI (Mullvad / ProtonVPN)
    └── health.py            # Proxy health-check & dead-proxy eviction
```

The library is **backend-agnostic** — callers pick a backend via config; the rest of the codebase only touches the public API.

---

### 13.3 Public API

```python
from app.lib.ip_rotator import IPRotator

rotator = IPRotator.from_settings()  # reads backend choice from .env

# Returns a pre-configured requests.Session with the next outbound IP
with rotator.session() as s:
    resp = s.get("https://news.google.com/...", timeout=10)

# Explicitly trigger a rotation (e.g. after a 429 response)
rotator.rotate()
```

`IPRotator.from_settings()` reads `IP_ROTATOR_BACKEND` from the environment:

| Value | Backend used |
|---|---|
| `proxy_list` | Static SOCKS5/HTTP proxy pool (default) |
| `tor` | Tor network via `stem` |
| `vpn_cli` | OS VPN CLI (Mullvad / ProtonVPN) |
| `none` | No rotation — plain `requests.Session` (dev/default) |

---

### 13.4 Backend Designs

#### 13.4.1 Proxy List Backend

- Proxies loaded from `IP_ROTATOR_PROXY_LIST` env var (newline-separated `socks5://user:pass@host:port` URIs) or a local file path
- Strategy: `round_robin` (default) or `random` (configurable via `IP_ROTATOR_STRATEGY`)
- Each proxy is wrapped in a `ProxyEntry` dataclass tracking: `uri`, `fail_count`, `last_used`, `is_dead`
- Dead-proxy eviction: if a proxy returns 3 consecutive connection errors or 429s, it is marked dead and skipped
- Health check (`health.py`): background thread pings `https://httpbin.org/ip` through each proxy every 10 minutes; revives dead proxies that pass
- Thread-safe: `threading.Lock` guards the proxy index and dead-proxy set

```python
@dataclass
class ProxyEntry:
    uri: str
    fail_count: int = 0
    last_used: datetime | None = None
    is_dead: bool = False
```

#### 13.4.2 Tor Backend

- Requires Tor daemon running (added as optional Docker service `tor` in `docker-compose.yml`)
- Uses `stem` library to send a `NEWNYM` signal to the Tor control port — forces a new circuit (new exit IP)
- Minimum rotation interval: 10 seconds (Tor enforces this)
- Proxy URI: `socks5h://127.0.0.1:9050` (the `h` suffix delegates DNS to Tor, preventing DNS leaks)
- Limitation: Tor exit nodes are blocked by Google and Yahoo in most cases — this backend is a fallback only

```python
from stem import Signal
from stem.control import Controller

def new_tor_circuit():
    with Controller.from_port(port=9051) as ctrl:
        ctrl.authenticate(password=settings.TOR_CONTROL_PASSWORD)
        ctrl.signal(Signal.NEWNYM)
```

#### 13.4.3 VPN CLI Backend

- Calls the host VPN's CLI tool between batches of requests (not between every request — too slow)
- Rotation is triggered at the **task level**, not per-request: once per `fetch_google_news()` / `fetch_yahoo_finance_news()` call
- Supported CLIs:
  - **Mullvad**: `mullvad relay set location in` → `mullvad connect`
  - **ProtonVPN**: `protonvpn-cli connect --random`
- CLI path configurable via `IP_ROTATOR_VPN_CLI` env var
- Post-rotation wait: 4 seconds (configurable via `IP_ROTATOR_VPN_WAIT_SEC`) for new IP to establish before requests resume
- This backend requires the Docker container to run with `network_mode: host` or have the VPN CLI mounted into the container — document in `docker-compose.yml` comments

---

### 13.5 Integration with News Pipeline

The Celery news task (`app/tasks/news_sentiment.py`) is the only caller:

```python
from app.lib.ip_rotator import IPRotator

rotator = IPRotator.from_settings()  # no-op if backend = "none"

# Before targeted fetches:
rotator.rotate()                     # switch IP
with rotator.session() as s:
    gn_articles = fetch_google_news(top_queries, session=s)

rotator.rotate()
with rotator.session() as s:
    yf_articles = fetch_yahoo_finance_news(top_names, session=s)
```

`fetch_google_news` and `fetch_yahoo_finance_news` in `news_fetcher.py` gain an optional `session: requests.Session | None = None` parameter. If `None`, they use their own default session (current behaviour, no breaking change).

---

### 13.6 Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `IP_ROTATOR_BACKEND` | `none` | `none` / `proxy_list` / `tor` / `vpn_cli` |
| `IP_ROTATOR_PROXY_LIST` | _(empty)_ | Newline-separated proxy URIs or file path |
| `IP_ROTATOR_STRATEGY` | `round_robin` | `round_robin` or `random` (proxy_list backend) |
| `IP_ROTATOR_VPN_CLI` | `mullvad` | `mullvad` or `protonvpn-cli` |
| `IP_ROTATOR_VPN_WAIT_SEC` | `4` | Seconds to wait after VPN reconnect |
| `TOR_CONTROL_PORT` | `9051` | Tor control port |
| `TOR_CONTROL_PASSWORD` | _(empty)_ | Tor control port password |

All variables are read via Pydantic `Settings` in `app/core/config.py` — no hardcoded values anywhere.

---

### 13.7 Docker Changes (when implemented)

- Add optional `tor` service to `docker-compose.yml` (image: `dperson/torproxy`)
- `celery-worker` service gains optional `network_mode: host` comment for VPN CLI backend
- New `IP_ROTATOR_*` vars added to `.env.example` with `none` defaults so existing setups are unaffected

---

### 13.8 Dependencies to Add (when implemented)

```
stem==1.8.2          # Tor control (tor backend only)
```

`requests` is already a transitive dependency. `socks` support via `requests[socks]` (adds `PySocks`) — add to `requirements.txt` when implementing.

---

## 14. Mobile App Design

**Framework**: React Native with Expo (managed workflow)

**Connectivity during development**:
```
Laptop (Docker running) → LAN IP: 192.168.x.x:8000
Mobile (same WiFi) → app connects to http://192.168.x.x:8000

OR

ngrok http 8000 → https://abc.ngrok.io
Mobile app (anywhere) → connects via ngrok URL
```
`BASE_URL` configurable in Expo app settings → no code changes needed.

**Shared code between web and mobile** via `packages/shared` (npm workspace):
- `@ai-trader/shared/types` — TypeScript interfaces for all API response shapes (auto-generated from OpenAPI spec via `openapi-typescript`; both apps import the same types, eliminating drift)
- `@ai-trader/shared/api-client` — Axios instance factory; both apps pass their own `baseURL` and token storage mechanism
- `@ai-trader/shared/store/signalStore` and `priceStore` — platform-agnostic Zustand state; the web app wraps these with browser WebSocket logic, mobile with Expo's AsyncStorage token handling

This is a lightweight **npm workspaces** monorepo (root `package.json` declares `workspaces: ["frontend", "mobile", "packages/*"]`). No Turborepo or nx required — the codebase is small enough that plain workspace symlinks suffice. A shared `tsconfig.base.json` at the root enforces consistent compiler options across all packages.

**Mobile-specific features**:
- Expo Push Notifications → signal alerts even when app is closed
- Biometric authentication (Face ID / fingerprint) for order confirmation
- Haptic feedback on order placement
- Same WebSocket disconnect overlay pattern as web: order buttons disabled, "Reconnecting..." banner shown while connection is restoring

### 13.1 Mobile State Resilience

**Offline order queuing:**

A user hitting "Submit Order" during a momentary cellular data drop must not silently lose their intent. The mobile app handles this with a client-side optimistic queue:

```typescript
// mobile/src/store/orderQueue.ts
interface QueuedOrder {
  id: string;           // local UUID
  payload: PlaceOrderRequest;
  queuedAt: string;     // ISO timestamp
  attempts: number;
}

// On submit:
// 1. Append to AsyncStorage orderQueue
// 2. Attempt POST /api/v1/orders immediately
// 3. On success: remove from queue; show confirmation
// 4. On network error: leave in queue; show "Queued — will submit when reconnected"
//    banner with order preview and a "Cancel queued order" button
//
// On app foreground / NetInfo connectivity change → flush queue:
//   for each queued order: POST with its idempotency-key = order.id
//                          (server deduplicates on Idempotency-Key header)
```

**Important constraint**: only MARKET orders are queued during offline. LIMIT and SL orders are rejected with an in-app error: *"Limit orders require live connectivity to prevent stale price submission"*. This prevents a LIMIT order placed during a price spike from filling at an unexpectedly different price seconds later.

The queue is bounded to **1 pending order per user** at a time (matching typical retail trading behaviour). Attempting to queue a second order while one is pending shows: *"1 order pending submission — please wait for it to complete first."*

---

**Expo Push Token lifecycle:**

Expo Push Notification tokens are device-scoped and can rotate (app reinstall, OS token refresh). Stale tokens cause silent delivery failures that are hard to debug.

Storage in PostgreSQL:

```sql
-- Extends user_settings or as a standalone table
CREATE TABLE expo_push_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       VARCHAR(200) NOT NULL,  -- Expo token: ExponentPushToken[...]
    device_id   VARCHAR(100),           -- expo-device/deviceId for dedup
    platform    VARCHAR(10) NOT NULL,   -- 'ios' | 'android'
    is_active   BOOL NOT NULL DEFAULT true,
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at   TIMESTAMPTZ,         -- updated on each successful delivery
    tbl_last_dt    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uniq_expo_token ON expo_push_tokens (token) WHERE is_active = true;
CREATE INDEX idx_expo_tokens_user ON expo_push_tokens (user_id) WHERE is_active = true;
```

**Token rotation flow:**
```
App launch → Expo.getExpoPushTokenAsync()
  If token differs from last registered token:
    POST /api/v1/mobile/push-token  {token, device_id, platform}
      → server: UPSERT on (device_id) — update token if device_id matches,
                else INSERT new row
      → mark any previous token for this device_id as is_active = false
```

**Delivery failure handling:**
Expo's push service returns `DeviceNotRegistered` when a token is invalidated by the OS. The backend notification sender (`notification_service.py`) must handle this:
```python
for ticket in expo_response.tickets:
    if ticket.status == 'error' and ticket.details.error == 'DeviceNotRegistered':
        # Mark token inactive; do NOT retry
        await db.execute(
            "UPDATE expo_push_tokens SET is_active = false WHERE token = ?",
            ticket.token
        )
```

This prevents accumulation of dead tokens that silently bloat the push delivery loop.

---

## 15. Admin Panel

Accessible only to users with `role = admin`. Rendered under `/admin/*` in Next.js with an admin layout guard. Every admin action is written to an immutable `admin_audit_log` table (action, target, before/after snapshot, timestamp, admin user ID) — admins can see every change ever made.

### 14.1 Dashboard — System Health at a Glance

Real-time overview of the entire system. Auto-refreshes every 30 seconds via WebSocket or polling.

**Infrastructure health:**
- PostgreSQL: connection status, query latency (p50/p99), active connections, disk usage
- Redis: connection status, memory used/max, eviction count, connected clients
- Celery workers: online count per queue (`high_priority`, `default`, `low_priority`), tasks/min throughput
- Active WebSocket connections (total, per channel)
- Docker container health badges (postgres, redis, backend, celery workers, mlflow)

**Market data status:**
- Ingestor state: `CONNECTED` | `RECONNECTING` | `STOPPED`
- Last tick received: timestamp + seconds ago
- Symbols subscribed count
- Redis orderbook key count (should be ~500 during market hours, 0 outside)

**Pipeline status:**
- Task status is stored in the `pipeline_task_status` PostgreSQL table (one row per task), read by `GET /admin/pipeline/status`.
- Status values: `idle` · `running` · `done` · `error` · `unknown`
- `unknown` is written at FastAPI startup for any task that was `running` when the previous process was killed — durable across container restarts, no time-based heuristics.
- Task log lines (structured, per-task) are stored as ephemeral Redis lists (`pipeline:logs:{task_name}`, capped at 500 entries, 7-day TTL) and read by `GET /admin/pipeline/{task_name}/logs`.
- Pipeline panel in Admin UI shows status badge per task + "View Logs" button opening a TaskLogModal (auto-polls every 3 s while running).
- Last `generate_signals` run: timestamp, duration, signal count
- Last `fetch_news_sentiment` run: timestamp, article count, per-source status
- Last `fetch_bhavcopy` run: date ingested, file date match status
- News source circuit breaker table: each of 9 sources showing HEALTHY / BACKOFF / DISABLED + time until next retry

**Today's summary:**
- Total signals generated today (BUY/SELL/HOLD breakdown)
- Total paper orders placed today (all users)
- Total live orders placed today (all users)
- Active users today (count who logged in)

---

### 14.2 User Management

**User list:**
- Table: all users with columns: name, email, role, trading mode (paper/live), broker status, last login, account status (active/inactive)
- Filters: role, trading mode, active/inactive, broker connected/not
- Sort by: last login, created date, name

**Per-user actions:**
- **Invite new user**: admin enters email → system generates `itsdangerous` signed magic link (24h TTL, single-use) → link shown to admin to share manually (no email server required in dev)
- **Pending invites list**: all unused invite links with expiry time + "Revoke" button
- **Deactivate / Reactivate**: immediately blocklists all active `jti` tokens for the user (via Redis blocklist) — they are logged out within seconds, not after 15-min token expiry
- **Change role**: admin → trader → viewer (with confirmation modal)
- **Force enable / disable live trading** for a specific user (overrides user's own OTP flow)
- **Reset paper trading**: reset paper balance to default (₹10,00,000) and clear all paper portfolio rows — with double-confirmation modal ("This cannot be undone")
- **View user detail page**: last 10 orders, current portfolio, broker connection status, TOTP configured?, last 5 logins with IP

**Admin-only TOTP enforcement check:**
- Admin dashboard shows a warning banner if any `admin`-role user has `is_totp_configured = false`

---

### 14.3 Invite Management

Separate view from user list for clarity:

| Column | Description |
|--------|-------------|
| Email | Invited email |
| Invited by | Admin who created it |
| Created at | When the link was generated |
| Expires at | 24h after creation |
| Status | `PENDING` \| `USED` \| `EXPIRED` \| `REVOKED` |
| Actions | Revoke (for PENDING only); Re-invite (for EXPIRED/REVOKED) |

---

### 14.4 Live Trading Oversight & Emergency Controls

This section gives admin real-time visibility into all live orders and the ability to act in an emergency.

**Global kill switches:**
- `LIVE_TRADING_ENABLED`: toggle OFF → all new live order API calls are blocked system-wide regardless of user setting; current open orders are NOT cancelled (broker has them) — admin must cancel manually
- `NEWS_PIPELINE_ENABLED`: toggle OFF → stops `fetch_news_sentiment` from running; signal generation continues with last known sentiment scores from Redis
- `SIGNAL_GENERATION_ENABLED`: toggle OFF → stops `generate_signals` from running; existing signals remain visible

**Live orders monitor** (market hours only):
- Real-time table of all open live orders across all users
- Columns: user, symbol, order type, transaction type, quantity, price, broker, placed at, current status
- Filter by user, symbol, status
- Admin can click "Cancel Order" on any open live order → calls broker API cancellation on behalf of the user (emergency use only; logged to `admin_audit_log`)

**Risk alerts panel:**
- Any user who has used ≥ 80% of their `daily_loss_limit_pct` — highlighted in orange
- Any user who has used ≥ 100% — highlighted in red (their trading is auto-halted by `order_service.py`; admin can manually reset the daily limit)

---

### 14.5 Data Management

**Universe management:**
- Searchable table of all tickers with sector, industry, is_active, is_in_nifty50 flags
- Add ticker: enter NSE symbol → system fetches company metadata from NSE/yfinance
- Deactivate ticker: removes from signal generation and WebSocket subscription
- Bulk import: upload CSV with columns `symbol, name, sector, industry`

**Historical data backfill:**
- Form: select tickers (multi-select or "all Nifty 500"), date range, source (Angel One / Upstox / NSE Bhavcopy)
- "Backfill" button → enqueues `backfill_historical` Celery task on `low_priority` queue
- Real-time progress bar: shows completed/total symbols + estimated remaining time (uses WebSocket task progress events)
- Task log: shows last 10 backfill runs with status (running/success/failed) and row counts inserted

**Manual triggers:**
- "Fetch today's Bhavcopy now" → triggers `fetch_bhavcopy` immediately (ignores beat schedule)
- "Run corporate action adjustment now" → triggers `apply_corporate_actions` for all pending rows
- "Refresh broker tokens now" → forces re-authentication for all connected brokers

**Raw data viewer (read-only):**
- Query `price_1min` or `price_1day` for any (symbol, date range)
- Result shown as table + mini OHLC chart (Victory or Recharts)
- Row count + download as CSV

---

### 14.6 Corporate Actions

| Column | Description |
|--------|-------------|
| Symbol | Ticker |
| Ex-date | When the action takes effect |
| Type | SPLIT / BONUS / DIVIDEND |
| Ratio / Amount | e.g. `2.0` for 2:1 split |
| Source | bhavcopy / nse_rss / manual |
| Applied? | Whether historical `price_1day` has been retroactively adjusted |
| Actions | "Apply now" (for pending); "View affected rows" |

Admin can **manually add** a corporate action (enter symbol, ex-date, type, ratio) for cases where NSE RSS missed it.
"Apply now" button triggers the adjustment Celery task immediately.

---

### 14.7 AI Model Management

**MLflow run table:**
- All runs with columns: model name, version, stage, accuracy, precision, recall, F1, Sharpe ratio, trained at, trained by, training data window
- Sortable by any metric
- "Promote to Production" button: sets stage = `production` via MLflow API → previous Production version auto-archived
- "Rollback" button: promotes previous version back to Production
- "Archive" button: marks version as archived

**Active model versions:**
- Currently loaded model versions in Production (pulled from DB `model_runs` table)
- Shows memory usage estimate per model

**Trigger retraining:**
- Form: select model(s) to retrain, training data window (start/end date), optional hyperparameter overrides
- "Retrain" button → enqueues on `low_priority` queue
- Estimated duration shown (based on previous run times for that model)
- Progress shown in real-time via WebSocket task events

**Ensemble weight editor:**
- Sliders for lgbm_weight, tft_weight, finbert_weight, anomaly_weight
- Real-time warning if weights don't sum to 1.0
- Separate inputs for `buy_threshold` and `sell_threshold`
- "Save" → writes to `ensemble_config` table + logs to `admin_audit_log`; takes effect on next signal run

---

### 14.8 Signal Audit & Accuracy

**Signal log:**
- Table: timestamp, symbol, signal (BUY/SELL/HOLD), confidence, all component scores (lgbm, tft, finbert, anomaly)
- Expandable row: shows `features_snapshot` as formatted key-value list + LLM explanation if available
- Filters: ticker, date range, signal type, confidence threshold, outcome label
- Outcome column: `correct` / `incorrect` / `hold_neutral` / `pending` (if EOD job hasn't backfilled yet)

**Accuracy dashboard:**
- Overall directional accuracy (correct / total labelled BUY+SELL signals, last 30/60/90 days)
- Per-model contribution accuracy (how often was LightGBM the swing factor vs TFT vs FinBERT?)
- Accuracy broken down by sector
- Accuracy over time chart (weekly rolling)
- Worst-performing tickers by signal accuracy (table)

**Signal explainability management:**
- Count of signals missing `explanation` (LLM quota hit or disabled)
- "Regenerate missing explanations" button → enqueues `explain_signal` for all NULL explanation rows in last 7 days

---

### 14.9 News Pipeline Monitor

| Source | Status | Last Fetch | Last Article Count | Last 429 | Backoff Until |
|--------|--------|------------|-------------------|----------|---------------|
| Economic Times | HEALTHY | 2 min ago | 12 | — | — |
| Reddit | BACKOFF | 47 min ago | — | 43 min ago | 13 min |
| StockTwits | DISABLED | 26h ago | — | 25h ago | — |

Actions:
- "Reset circuit breaker" for a specific source (immediately re-enables, clearing BACKOFF/DISABLED state)
- "Disable source" manually
- "View last 20 articles" for any source (with sentiment scores)

---

### 14.10 Celery Task Explorer

**Queue depth:**
- Live count of pending tasks per queue (high_priority, default, low_priority)
- Workers consuming each queue

**Recent task history** (last 100 tasks):
- Table: task name, status, started at, duration, worker, error (if failed)
- Filter by task name, status, date
- Click to expand: full task args + traceback if failed

**Actions:**
- "Revoke task" for any PENDING task in the queue (removes it before it runs)
- "Retry failed task" for a FAILURE task
- "Purge queue" for a specific queue — with double-confirmation (destroys all pending tasks)

---

### 14.11 Backup & Restore Status

| Backup | Date | Size | Status | Location |
|--------|------|------|--------|----------|
| pg_dump | 2026-04-16 02:30 IST | 142 MB | ✓ Success | Backblaze B2 |
| pg_dump | 2026-04-15 02:30 IST | 139 MB | ✓ Success | Backblaze B2 |

- "Download latest backup" button (admin only) → generates a pre-signed B2 URL, valid 1h
- "Trigger backup now" button → runs `pg_dump` immediately as a Celery task
- Last 14 backups shown with size, status, and B2 path
- Warning banner if last backup is >25 hours old

---

### 14.12 System Configuration

| Setting | Value | Description |
|---------|-------|-------------|
| `LIVE_TRADING_ENABLED` | toggle | Global kill switch |
| `NEWS_PIPELINE_ENABLED` | toggle | Enable/disable news fetch + sentiment |
| `SIGNAL_GENERATION_ENABLED` | toggle | Enable/disable `generate_signals` task |
| `EXPLAINABILITY_BACKEND` | `gemini` / `local` / `disabled` | LLM backend for signal explanations |
| `DEFAULT_TRADING_MODE` | `paper` / `live` | Mode assigned to new users |
| `DEFAULT_PAPER_BALANCE` | number input | ₹ default for new paper accounts |
| `PAPER_ORDER_SIZE_CAP_PCT` | number input | Max % of top-1 L2 qty (default: 25%) |

All changes written to `admin_audit_log` with before/after values.

---

## 16. Security Design

| Threat                       | Mitigation                                                                              |
|------------------------------|-----------------------------------------------------------------------------------------|
| Credential theft             | bcrypt hashing (cost 12), no plaintext storage ever                                     |
| JWT token theft              | Short 15-min access token, httpOnly refresh token cookie                                |
| JWT not revoked on logout/deactivation | `jti` claim in every token; Redis blocklist checked on every request; TTL = remaining token lifetime so keys never accumulate |
| WebSocket token leakage      | Token passed as query param (browser WS limitation); mitigated by short 15-min TTL and HTTPS/WSS in all non-local environments — token never stored server-side in logs |
| Broker API key exposure      | Fernet-encrypted in DB, encryption key only in env var                                  |
| Admin account compromise     | TOTP 2FA **mandatory** for admin — enforced at application layer, cannot be disabled    |
| CSRF                         | SameSite=Strict cookie, CSRF token for state-changing requests                          |
| SQL injection                | SQLAlchemy ORM only — no raw string-interpolated queries                                |
| XSS                          | Next.js auto-escaping, Content-Security-Policy headers                                  |
| Rate limiting                | slowapi on all auth + order endpoints; **`ProxyHeadersMiddleware` configured to read `CF-Connecting-IP` / `X-Forwarded-For`** so rate limits key on true client IP, not Cloudflare proxy IP |
| Insider access to live trades| Requires email OTP to enable, enforced server-side                                      |
| Accidental live order        | Paper/Live badge, order confirmation dialog, global admin kill switch                   |
| Log leakage of secrets       | Explicit blocklist in logging middleware (API keys, tokens, passwords)                  |
| Dependency vulnerabilities   | `pip-audit` in CI, `npm audit` for frontend                                             |
| Webhook spoofing             | HMAC signature verification on all broker postback webhook endpoints                    |
| Duplicate order execution    | Idempotency key (`Idempotency-Key` header) checked in Redis before processing; cached 5 min per user |

---

## 17. Docker & Infrastructure

### Services (docker-compose.yml)

```yaml
services:

  postgres:
    image: timescale/timescaledb:latest-pg16
    environment: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
    volumes: postgres_data:/var/lib/postgresql/data
    healthcheck: pg_isready

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes: redis_data:/data

  backend:
    build: ./backend
    depends_on: [postgres, redis]
    environment: (from .env)
    volumes: ./backend:/app  # dev only: hot reload
    ports: 8000:8000
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]  # RTX 3050 passthrough

  celery-worker-high:
    build: ./backend
    command: celery -A app.tasks.celery_app worker -Q high_priority -c 4
    depends_on: [postgres, redis]

  celery-worker-default:
    build: ./backend
    command: celery -A app.tasks.celery_app worker -Q default,low_priority -c 2
    depends_on: [postgres, redis]

  celery-beat:
    build: ./backend
    command: celery -A app.tasks.celery_app beat --scheduler redbeat.RedBeatScheduler
    depends_on: [redis]

  mlflow:
    image: ghcr.io/mlflow/mlflow:latest
    command: mlflow server --backend-store-uri postgresql://... --host 0.0.0.0
    ports: 5000:5000
    depends_on: [postgres]

  frontend:
    build: ./frontend
    ports: 3000:3000
    environment: NEXT_PUBLIC_API_URL=http://localhost:8000
```

### GPU passthrough for Docker on Windows
Requires: WSL2 + CUDA 12.x + NVIDIA Container Toolkit. Add `deploy.resources.reservations.devices` section to backend and worker services.

### Dev vs Prod separation
`docker-compose.override.yml` (dev): mounts source code as volumes, enables hot reload, disables GPU reservation for faster startup on machines without GPU.

### VPS Hardening (DigitalOcean Bangalore Droplet)

Run once after provisioning the droplet, before starting Docker Compose:

**1. Create a non-root deploy user**
```bash
adduser deploy                         # or: useradd -m -s /bin/bash deploy
usermod -aG sudo,docker deploy
# Copy your SSH public key
mkdir -p /home/deploy/.ssh
echo "<your-public-key>" >> /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh ; chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

**2. Harden SSH (`/etc/ssh/sshd_config`)**
```ini
Port 2222                   # non-standard port reduces automated scanning noise
PermitRootLogin no          # never allow root login
PasswordAuthentication no   # key-only; no brute-forceable passwords
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 20
X11Forwarding no
```
```bash
systemctl restart ssh
```

**3. Firewall (UFW)**
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 2222/tcp       # SSH on non-standard port
# Do NOT open 80 or 443 — Cloudflare Tunnel handles ingress without open ports
# Do NOT open 8000 (FastAPI), 5432 (Postgres), 6379 (Redis) — all internal-only
ufw enable
ufw status verbose
```

> **Why no port 80/443?** The Cloudflare Tunnel (`cloudflared`) process inside Docker connects outbound to Cloudflare's edge with a persistent mTLS tunnel. Browser traffic arrives at Cloudflare → tunnelled to the local `backend:8000` container. The VPS never needs to accept inbound HTTP/HTTPS, which eliminates the public attack surface entirely. The only open inbound port is SSH.

**4. fail2ban**
```bash
apt install fail2ban -y
cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
```
Edit `/etc/fail2ban/jail.local`:
```ini
[sshd]
enabled  = true
port     = 2222
maxretry = 3
bantime  = 3600     # 1 hour ban
findtime = 600
```
```bash
systemctl enable --now fail2ban
fail2ban-client status sshd   # verify
```

**5. Unattended security upgrades**
```bash
apt install unattended-upgrades -y
dpkg-reconfigure --priority=low unattended-upgrades
# Accept defaults: security updates only, auto-reboot disabled (manual reboot schedule)
```

**6. Docker security defaults**
All containers run with these in `docker-compose.prod.yml`:
```yaml
security_opt:
  - no-new-privileges:true
read_only: true              # where applicable (not postgres/redis data volumes)
tmpfs:
  - /tmp                    # writable scratch space without persistent volume
```
The `.env` file on VPS is `chmod 600` and owned by `deploy` only.

**Hardening checklist (run before first deploy):**
- [ ] Root SSH disabled
- [ ] SSH key-only auth confirmed (`ssh -o PubkeyAuthentication=no` returns "Permission denied")
- [ ] UFW active; only port 2222 open inbound
- [ ] fail2ban running; `fail2ban-client status sshd` shows active jail
- [ ] Cloudflare Tunnel health check green in Cloudflare dashboard
- [ ] `.env` file is `chmod 600 .env`
- [ ] `docker compose ps` shows all containers healthy
- [ ] Unattended upgrades configured

---

### 17.x Localized Static Assets — The Logo Strategy

**Current approach**: `scripts/download_logos.py` fetches stock logo `.png` binaries from a public CDN (Clearbit / Logo.dev) at setup time and writes them to `backend/app/static/logos/`. FastAPI serves them via `StaticFiles` mounted at `/static/logos/`.

**Why this is the right call for now:**

| Concern | Current approach |
|---|---|
| Latency | Zero — served directly from the local container filesystem |
| Privacy | No third-party CDN ever sees which symbols your users are viewing |
| Uptime | Logo availability is fully decoupled from external services |
| Offline dev | Works without internet after initial download |

**The Git bloat problem**: Hundreds of binary `.png` files committed to the repository will:
- Inflate `git clone` time proportionally (every clone downloads the full binary history)
- Grow the Docker build context and final image size
- Make `git log --all` slower as history accumulates new logo updates

**Current mitigation**: `backend/app/static/logos/` is added to `.gitignore` — logos are **not** committed to git. `download_logos.py` is re-run as part of the Docker image build (`RUN python /app/scripts/download_logos.py` in `Dockerfile`) so the logos are baked into the image layer, not the repository.

**Production migration path (before scaling to thousands of users)**:

1. Create an **AWS S3 bucket** (or DigitalOcean Spaces) named `aitrader-static`
2. Upload the logos folder: `aws s3 sync backend/app/static/logos/ s3://aitrader-static/logos/ --acl public-read`
3. Put **Cloudflare CDN** in front of the bucket (free tier, global edge caching, 0ms latency for repeat requests)
4. Replace the `StaticFiles` mount with a single env var: `LOGO_BASE_URL=https://cdn.yourdomain.com/logos`
5. Frontend reads `LOGO_BASE_URL` from the API config endpoint — no hardcoded paths in React components
6. Remove the `RUN python /app/scripts/download_logos.py` line from `Dockerfile` — backend image is now purely code, ~200MB smaller

This migration is a **one-afternoon task** and should be done before the first public deploy. Until then, the current local-binary approach is the correct tradeoff.

---

## 18. Development Phases

### Phase 1 — Foundation
- [ ] Project scaffold: Docker Compose, folder structure
- [ ] TimescaleDB init SQL (hypertables, indexes)
- [ ] Alembic migrations setup
- [ ] FastAPI app factory, lifespan, middleware, logging
- [ ] User model + auth endpoints (login, refresh) — no public self-registration; accounts created by admin only
- [ ] Admin-only `POST /api/v1/admin/users/invite` endpoint: accepts email, generates signed invite token (itsdangerous, 24h TTL), sends registration link via email
- [ ] `POST /api/v1/auth/register` endpoint: accepts invite token + new password; validates token; activates account — token single-use, invalidated on use or expiry
- [ ] `seed_admin_user.py` script for first-run admin account creation
- [ ] User settings model (broker preference, trading mode)
- [ ] Basic health check endpoint
- [ ] Next.js project with Shadcn/UI, login page (no public register page)
- [ ] JWT token management in frontend (interceptors, refresh)

### Phase 2 — Data Pipeline
- [ ] Broker adapter abstract interface + NSE fallback implementation
- [ ] Angel One SmartAPI adapter
- [ ] Upstox adapter
- [ ] FastAPI dependency injection resolver (`get_broker_adapter` dependency)
- [ ] BrokerSelector component in frontend navbar
- [ ] Market data ingestor (WebSocket listener → Redis Pub/Sub)
- [ ] 1-min OHLCV builder (ticks → bars → TimescaleDB)
- [ ] yfinance historical backfill Celery task
- [ ] NSE Bhavcopy EOD ingestion task
- [ ] Admin: historical backfill UI with live progress

### Phase 3 — AI/ML Pipeline
- [ ] Feature engineering pipeline (technical indicators + macro)
- [ ] LightGBM training + inference wrapper
- [ ] TFT training + inference wrapper (fp16)
- [ ] LSTM Autoencoder training + inference wrapper
- [ ] FinBERT inference wrapper (fp16)
- [ ] Ensemble combiner with configurable weights
- [ ] MLflow integration (logging runs, promoting models)
- [ ] Signal generation Celery task (every 5 min)
- [ ] Admin: model management UI (MLflow metrics, promote/rollback)

### Phase 4 — News Sentiment Pipeline
- [ ] RSS fetchers (ET, Moneycontrol, BS, Livemint, NSE, BSE)
- [ ] Google News fetcher (gnews library)
- [ ] Reddit PRAW fetcher
- [ ] StockTwits API fetcher
- [ ] NER pipeline (spaCy → map company names to NSE symbols)
- [ ] FinBERT batch scoring
- [ ] Rolling sentiment aggregation → Redis
- [ ] Celery Beat schedule (every 15 min)

### Phase 5 — WebSocket & Live UI
- [ ] ConnectionManager (room-based broadcasting)
- [ ] Price WebSocket endpoint + Redis subscriber
- [ ] Signal WebSocket endpoint
- [ ] TradingView Lightweight Charts component (web)
- [ ] Signal overlay on chart
- [ ] Dashboard page: live portfolio + signal feed
- [ ] Screener page with filters

### Phase 6 — Paper Trading Engine
- [ ] Virtual account model (per user, per mode)
- [ ] Paper order matching engine
- [ ] Virtual P&L calculation (real-time)
- [ ] Order panel component (web)
- [ ] Order confirmation dialog (paper vs live UX)
- [ ] Paper portfolio page

### Phase 7 — Live Trading
- [ ] Live trading enablement flow (email OTP gate)
- [ ] Order routing to broker via DI-injected adapter
- [ ] Broker postback webhook endpoint (`POST /api/v1/webhooks/order-update`) with HMAC signature verification
- [ ] Register postback URL in Angel One + Upstox developer portals (Cloudflare Tunnel URL for dev, VPS URL for prod)
- [ ] WebSocket push to frontend on order status change
- [ ] EOD reconciliation Celery task (4:00 PM IST) — fallback for missed webhooks only
- [ ] Live portfolio sync from broker positions
- [ ] Safety guardrails (daily loss limit auto-switch)
- [ ] Admin: global live trading kill switch

### Phase 8 — Mobile App
- [ ] Expo project setup, navigation
- [ ] Shared API client config
- [ ] Auth screens + biometric login
- [ ] Dashboard, Screener, Signals, Portfolio, Settings tabs
- [ ] WebSocket live prices + signals
- [ ] Expo Push Notifications for signal alerts
- [ ] Cloudflare Tunnel (`cloudflared`) setup for stable local webhook URL — register once in both broker portals

---

## 19. Future Hosting Plan

### Development (now)
- Full Docker Compose on laptop (Windows + WSL2 + RTX 3050)
- Mobile connects via LAN IP or ngrok
- Broker postback webhooks exposed via Cloudflare Tunnel (stable URL, registered once in broker portals)

### Broker API Geo-Blocking — Critical Constraint

> **Important:** Angel One SmartAPI and Upstox actively geo-block API requests originating from non-Indian data centers (Europe, US). Oracle Free Tier (US/EU regions) and Hetzner (EU) **will not work reliably** for broker connectivity in production. The backend hosting any live broker WebSocket connection or order API must be in India.

### Staging (free — India region required for broker APIs)
- ~~Oracle Cloud Always Free Tier~~ — **skip for broker-connected services** (Europe/US region only on free tier)
- **Option A — AWS Free Tier (12 months)**: EC2 t2.micro in `ap-south-1` (Mumbai) — free for 12 months
  - Run: FastAPI + Celery workers (broker-facing services)
  - Postgres + Redis can run on same instance during staging
- **Option B — Stay on laptop via Cloudflare Tunnel** during development/testing; defer cloud until production

### Production (India-region backend required)
| Component       | Host                              | Cost                  | Notes                            |
|-----------------|-----------------------------------|-----------------------|----------------------------------|
| Backend + DB    | AWS EC2 t3.small `ap-south-1`     | ~$15–20/month         | Mumbai region, broker APIs work  |
| Backend + DB    | DigitalOcean Droplet (Bangalore)  | ~$12/month            | Cheaper alt, DO has BLR region   |
| Frontend        | Vercel (free tier)                | Free                  | Static/SSR, no Indian IP needed  |
| Model training  | Vast.ai (hourly rental)           | ~$0.20/hr on-demand   | Rent GPU only when retraining    |
| Model storage   | Backblaze B2                      | Free ≤10 GB           | Store model artifacts            |
| CDN + Security  | Cloudflare free tier              | Free                  | Proxy + DDoS protection          |
| Monitoring      | Grafana + Loki (self-hosted)      | Free                  | On same backend instance         |

**Recommended production path**: DigitalOcean Bangalore droplet (~$12/month) — cheapest option with confirmed Indian IP.

**Frontend (Next.js) does not need an Indian IP** — it only talks to the backend API, which handles all broker connectivity. Vercel remains free and valid for the frontend.

### Disaster Recovery — Automated Database Backup

A single DigitalOcean droplet with a local Docker volume is a single point of failure. A full database backup strategy is mandatory:

```
Cron job on the VPS (runs daily at 2:30 AM IST, after cleanup_old_ticks task):

  pg_dump -Fc -U $POSTGRES_USER $POSTGRES_DB \
    | gzip \
    > /tmp/backup_$(date +%Y%m%d).dump.gz

  # Upload to Backblaze B2 using rclone (pre-configured with B2 credentials)
  rclone copy /tmp/backup_$(date +%Y%m%d).dump.gz b2:ai-trader-backups/db/

  # Remove local temp file
  rm /tmp/backup_$(date +%Y%m%d).dump.gz
```

- **Tool**: `rclone` (free, supports Backblaze B2 natively)
- **Storage**: Backblaze B2 free tier (10 GB) — sufficient for compressed TimescaleDB dumps of Nifty 500 daily OHLCV
- **Retention**: keep last 14 daily backups in B2; older ones deleted via B2 lifecycle rule
- **MLflow artifacts** (model files) are also synced to the same B2 bucket under `b2:ai-trader-backups/mlflow/`
- **Restore**: `rclone copy b2:ai-trader-backups/db/backup_YYYYMMDD.dump.gz . && pg_restore -d $DB backup.dump.gz`
- `RCLONE_B2_ACCOUNT` and `RCLONE_B2_KEY` are injected as environment variables — never hardcoded

### Restore Drill Procedure

A backup that has never been tested is not a backup — it is a hope. The following procedure must be executed on a schedule:

**Frequency**: Monthly (first Sunday of each month, before market open at 9:15 AM IST)

**Procedure:**
```bash
# Step 1: Spin up an ephemeral staging postgres container (separate from production)
docker run -d \
  --name pg-restore-test \
  -e POSTGRES_USER=$POSTGRES_USER \
  -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
  -e POSTGRES_DB=ai_trader_restore_test \
  -p 5433:5432 \
  timescale/timescaledb:latest-pg16

# Step 2: Fetch the most recent backup from Backblaze B2
rclone copy b2:ai-trader-backups/db/ /tmp/restore_test/ \
  --include "backup_$(date +%Y%m%d).dump.gz"

# Step 3: Restore into the ephemeral container
gunzip -c /tmp/restore_test/backup_$(date +%Y%m%d).dump.gz \
  | pg_restore -h localhost -p 5433 \
              -U $POSTGRES_USER \
              -d ai_trader_restore_test \
              --no-owner --no-acl

# Step 4: Verify row counts match production (key canary tables)
psql -h localhost -p 5433 -U $POSTGRES_USER ai_trader_restore_test -c "
  SELECT
    'users'           AS tbl, COUNT(*) FROM users
  UNION ALL SELECT 'tickers',          COUNT(*) FROM tickers
  UNION ALL SELECT 'price_1day',       COUNT(*) FROM price_1day
  UNION ALL SELECT 'signals',          COUNT(*) FROM signals
  UNION ALL SELECT 'orders',           COUNT(*) FROM orders;
"

# Step 5: Tear down ephemeral container
docker rm -f pg-restore-test
rm -rf /tmp/restore_test/
```

**Pass criteria**: row counts for all 5 canary tables match production within 1-day tolerance (last 24h of live writes are acceptable delta). All Alembic migration version rows present in `alembic_version` table.

**On failure**: fire a `CRITICAL` Discord alert immediately. Do not wait for next day. Investigate backup cron logs on VPS.

**Admin panel integration**: The admin panel (Section 14) must show the last successful restore drill date. A banner warning appears if no drill has been logged in the past 35 days. The drill result (pass/fail, row counts, timestamp) is written to a `backup_drills` PostgreSQL table:
```sql
CREATE TABLE backup_drills (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    performed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    backup_date     DATE NOT NULL,
    passed          BOOL NOT NULL,
    row_counts      JSONB,          -- {tbl: count} snapshot
    notes           TEXT,
    performed_by    UUID REFERENCES users(id) SET NULL
);
```

**The "completely free" infrastructure goal applies to development only.** Production requires an Indian-region server; budget ~$12–20/month for the backend VPS.

---

## 20. Testing & CI/CD Strategy

### Unit Tests vs Integration Tests

| Dimension | Unit Test | Integration Test |
|-----------|-----------|-----------------|
| **Scope** | Single function or class in isolation | Multiple components wired together |
| **External deps** | All I/O mocked (DB, Redis, broker APIs, LLM) | Real PostgreSQL + TimescaleDB + Redis (ephemeral containers) |
| **Speed** | <10ms per test | 100ms–5s per test |
| **Location** | `tests/unit/` | `tests/integration/` |
| **Examples** | `order_service.place_order()` with mock broker; `ensemble.py` weight math; `alerting.py` payload shape | Full order lifecycle through DB; Alembic migration applies cleanly; auth token flow end-to-end |

**What belongs in unit tests:**
- `order_service.py` — every branch (paper vs live routing, idempotency key hit, order size cap rejection)
- `ensemble.py` — score weighting, confidence threshold decisions
- `paper_engine.py` — fill logic, stale orderbook rejection, `PAPER_ORDER_TOO_LARGE` cap
- `core/security.py` — JWT issue/decode, `jti` blocklist check logic (mock Redis)
- `alerting.py` — Discord payload construction (mock `httpx`)
- News `fetcher.py` — circuit breaker state transitions on 429/5xx

**What belongs in integration tests:**
- Full auth flow: register with invite token → login → get access token → refresh → logout (token blocklisted)
- Order placement end-to-end: POST `/api/v1/orders` → DB row created → paper engine fills → portfolio row updated
- Alembic migration: run all migrations from zero → verify schema matches SQLModel definitions
- TimescaleDB hypertable creation: verify `price_1min` is a hypertable with correct `chunk_time_interval`
- Celery task smoke tests: enqueue `build_ohlcv_bars` with test tick data → verify OHLCV row written to DB

### Mocking Broker Adapters

The FastAPI DI pattern (`Depends(get_broker_adapter)`) makes mocking trivial — no monkey-patching required:

```python
# tests/unit/test_order_service.py
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_broker_adapter

class MockAngelOneAdapter:
    async def place_order(self, order) -> dict:
        return {"order_id": "MOCK-12345", "status": "COMPLETE"}

    async def get_order_status(self, order_id: str) -> dict:
        return {"order_id": order_id, "status": "COMPLETE", "filled_qty": 10}

    async def cancel_order(self, order_id: str) -> dict:
        return {"order_id": order_id, "status": "CANCELLED"}

def test_live_order_placed_via_broker():
    # Override the DI dependency for this test only
    app.dependency_overrides[get_broker_adapter] = lambda: MockAngelOneAdapter()
    client = TestClient(app)
    response = client.post("/api/v1/orders", json={...}, headers={"Authorization": "Bearer <test_jwt>"})
    assert response.status_code == 201
    assert response.json()["data"]["broker_order_id"] == "MOCK-12345"
    app.dependency_overrides.clear()  # always clean up
```

The broker adapter interface (`BrokerAdapter` ABC) defines the contract. `MockAngelOneAdapter` implements the same interface — the service layer is completely unaware it is speaking to a mock.

Redis mocking uses `fakeredis` (in-process Redis implementation):
```python
import fakeredis.aioredis as fakeredis
app.dependency_overrides[get_redis] = lambda: fakeredis.FakeRedis()
```

### Test Database Lifecycle

**Strategy: ephemeral PostgreSQL + TimescaleDB container per CI run (not transactional rollbacks).**

Rationale:
- TimescaleDB extension must be installed (`CREATE EXTENSION timescaledb`) before Alembic can create hypertables. This is a one-time DDL operation that cannot live inside a transaction that rolls back.
- Testing Alembic migrations as part of each run is a first-class requirement — the migration itself is what is under test, not just the schema's end state.
- A fresh container guarantees test isolation across parallel CI runs without any shared-state leakage.

Transactional rollbacks (`ROLLBACK` after each test) are viable for pure application-layer tests that don't involve DDL, but they cannot cover migration testing and add complexity without meaningful benefit here given the low overhead of a containerised DB.

```yaml
# docker-compose.test.yml — ephemeral test stack
services:
  test-postgres:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: aitrader_test
    ports: ["5433:5432"]

  test-redis:
    image: redis:7-alpine
    ports: ["6380:6379"]
```

```
CI test lifecycle:
  1. docker compose -f docker-compose.test.yml up -d
  2. Wait for postgres healthcheck (pg_isready)
  3. alembic upgrade head          # run all migrations including TimescaleDB DDL
  4. pytest tests/ -v --cov=app --cov-report=xml
  5. docker compose -f docker-compose.test.yml down -v  # destroy volumes too
```

`pytest-asyncio` handles async test coroutines. `pytest-cov` reports coverage. Target: >80% coverage on `services/` and `api/` layers.

### GitHub Actions CI Pipeline

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: timescale/timescaledb:latest-pg16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: aitrader_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U test"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: pip install -r backend/requirements.txt -r backend/requirements-dev.txt

      - name: Lint & format check (ruff)
        run: ruff check backend/ && ruff format --check backend/

      - name: Type check (mypy)
        run: mypy backend/app --ignore-missing-imports

      - name: Run Alembic migrations
        env:
          DATABASE_URL: postgresql+asyncpg://test:test@localhost:5432/aitrader_test
          REDIS_URL: redis://localhost:6379/0
        run: alembic -c backend/alembic.ini upgrade head

      - name: Run test suite
        env:
          DATABASE_URL: postgresql+asyncpg://test:test@localhost:5432/aitrader_test
          REDIS_URL: redis://localhost:6379/0
          APP_ENV: test
          SECRET_KEY: test-secret-key-not-real
        run: pytest backend/tests/ -v --cov=app --cov-report=xml --cov-fail-under=80

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          file: coverage.xml

  frontend-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci --prefix frontend
      - run: npm run lint --prefix frontend
      - run: npm run build --prefix frontend

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pip-audit
      - run: pip-audit -r backend/requirements.txt
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm audit --audit-level=high --prefix frontend
```

**Pipeline summary:**
- `test` job: migrations → pytest → coverage gate (80% minimum)
- `frontend-check` job: lint + type-check + production build (no runtime errors)
- `security` job: `pip-audit` + `npm audit` on every PR — dependency vulnerabilities block merge
- All 3 jobs run in parallel; PR cannot merge unless all pass
- `ruff` handles both linting and formatting in one tool (replaces flake8 + black)
- No secrets are needed in CI for the test suite — no live broker calls, no real LLM calls, all mocked
