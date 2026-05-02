# AI Trader — System Design Document

> **Version**: 3.0  
> **Last Updated**: May 2026  
> **Status**: Production-Ready

AI Trader is a full-stack algorithmic trading platform for Indian equity markets (NSE/BSE). It combines technical analysis, machine learning signal generation, news sentiment analysis, and multi-broker integration to provide automated and semi-automated trading capabilities.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [Technology Stack](#3-technology-stack)
4. [Component Deep Dive](#4-component-deep-dive)
5. [Data Pipeline](#5-data-pipeline)
6. [Machine Learning Models](#6-machine-learning-models)
7. [Broker Integration](#7-broker-integration)
8. [Security Architecture](#8-security-architecture)
9. [Infrastructure & Deployment](#9-infrastructure--deployment)
10. [API Design](#10-api-design)
11. [Frontend Architecture](#11-frontend-architecture)
12. [Scheduled Tasks](#12-scheduled-tasks)
13. [Configuration Reference](#13-configuration-reference)
14. [Database Schema](#14-database-schema)
15. [Development Phases](#15-development-phases)

---

## 1. Project Overview

### 1.1 Goals

- **Automated Signal Generation**: Generate BUY/SELL/HOLD signals using ensemble ML models and technical indicators
- **Multi-Broker Support**: Execute trades via Angel One and Upstox APIs with paper trading fallback
- **Real-Time Data**: WebSocket-based live price streaming and signal notifications
- **News Sentiment Analysis**: FinBERT-powered sentiment scoring from Google News, Yahoo Finance, and RSS feeds
- **Risk Management**: Daily loss limits, position sizing caps, and automatic trading halts
- **Explainable AI**: LLM-generated natural language explanations for trading signals

### 1.2 Target Users

| Role       | Capabilities                                                            |
| ---------- | ----------------------------------------------------------------------- |
| **Viewer** | Read-only access to signals, prices, portfolio, screener                |
| **Trader** | All viewer access + paper trading; live trading if enabled via OTP      |
| **Admin**  | Full access including user management, model training, pipeline control |

### 1.3 Supported Markets

- **NSE**: National Stock Exchange of India (primary)
- **BSE**: Bombay Stock Exchange (secondary)
- **NSE Coverage**: 3 concurrent bulk index calls (Nifty 50, Next 50, Midcap 100) via `equity-stockIndices` — snapshot time < 2s vs ~40s sequential

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (React/Vite)                          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │Dashboard│ │Screener │ │Signals  │ │Portfolio│ │Forecast │ │Settings │   │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │
│       └──────────┬┴──────────┬┴──────────┬┴──────────┬┴──────────┬┘        │
│                  │     WebSocket + REST API          │                      │
└──────────────────┴───────────────────────────────────┴──────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BACKEND (FastAPI)                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Auth API   │  │  Prices API  │  │  Signals API │  │  Orders API  │     │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├──────────────┤     │
│  │  Portfolio   │  │   Screener   │  │   Forecast   │  │   Webhooks   │     │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├──────────────┤     │
│  │    Admin     │  │     News     │  │   Settings   │  │   WebSocket  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │
│                           │                                                  │
│  ┌────────────────────────┴────────────────────────┐                        │
│  │              SERVICES LAYER                      │                        │
│  │  • Price Service      • Screener Service        │                        │
│  │  • Paper Trade Svc    • Live Trade Service      │                        │
│  │  • Feature Engineer   • ML Loader               │                        │
│  │  • LSTM Service       • TFT Service             │                        │
│  │  • News Fetcher       • Sentiment Scorer        │                        │
│  │  • NER Mapper         • Discord Service         │                        │
│  │  • Email Service      • Push Notification Svc   │                        │
│  │  • Explainer          • Credential Pool         │                        │
│  └──────────────────────────────────────────────────┘                        │
│                           │                                                  │
│  ┌────────────────────────┴────────────────────────┐                        │
│  │              BROKER ADAPTERS                     │                        │
│  │  • Angel One (SmartAPI)                         │                        │
│  │  • Upstox (REST API v2)                         │                        │
│  │  • yfinance (fallback for delayed data)         │                        │
│  └──────────────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  TimescaleDB  │   │     Redis     │   │    Celery     │
│  (PostgreSQL) │   │   (Cache +    │   │   (Workers +  │
│               │   │    Broker)    │   │     Beat)     │
└───────────────┘   └───────────────┘   └───────────────┘
        │                    │                    │
        └────────────────────┴────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│    MLflow     │   │   Cloudflare  │   │    Flower     │
│   (Tracking)  │   │    Tunnel     │   │  (Monitoring) │
└───────────────┘   └───────────────┘   └───────────────┘
```

### 2.1 Key Architectural Decisions

| Decision                              | Rationale                                                                                        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **TimescaleDB over plain PostgreSQL** | Native time-series compression, continuous aggregates, and automatic partitioning for OHLCV data |
| **Redis for session caching**         | 23-hour TTL Angel One JWT caching avoids TOTP re-auth per request across workers                 |
| **Celery for background tasks**       | Decouples heavy ML inference and data ingestion from API request cycle                           |
| **WebSocket for live data**           | Single broadcaster pattern with per-connection queues for efficient fan-out                      |
| **Cloudflare Tunnel**                 | Stable webhook URLs for broker postbacks without exposing ports                                  |

---

## 3. Technology Stack

### 3.1 Backend

| Component      | Technology  | Version | Purpose                           |
| -------------- | ----------- | ------- | --------------------------------- |
| **Runtime**    | Python      | 3.11+   | Core language                     |
| **Framework**  | FastAPI     | 0.110+  | Async REST API + WebSocket        |
| **ORM**        | SQLModel    | 0.0.14+ | SQLAlchemy + Pydantic integration |
| **Task Queue** | Celery      | 5.3+    | Distributed task execution        |
| **Scheduler**  | Celery Beat | -       | Cron-like periodic tasks          |

### 3.2 Data Layer

| Component        | Technology  | Version | Purpose                                     |
| ---------------- | ----------- | ------- | ------------------------------------------- |
| **Database**     | PostgreSQL  | 16      | Primary relational store                    |
| **Time-Series**  | TimescaleDB | 2.x     | OHLCV data compression + queries            |
| **Cache/Broker** | Redis       | 7       | Session cache, Celery broker, rate limiting |
| **Migrations**   | Alembic     | 1.13+   | Schema version control                      |

### 3.3 Machine Learning

| Component               | Technology             | Purpose                                     |
| ----------------------- | ---------------------- | ------------------------------------------- |
| **Signal Model** | LightGBM | Binary classifier for BUY/SELL signals |
| **Anomaly Detection** | LSTM Autoencoder | Reconstruction-error based anomaly scoring |
| **Price Forecasting** | PatchTST (primary) / TFT (fallback) | 5-day ahead price prediction |
| **Forecast Evaluation** | RMSE / MAE / Directional Accuracy | Self-evaluation tracked in `forecast_history` |
| **Sentiment Analysis** | FinBERT | Zero-centred polarity: P(pos) − P(neg) |
| **NER** | spaCy (en_core_web_sm) | Entity extraction for news → symbol mapping |
| **Experiment Tracking** | MLflow | Model versioning and metrics |

### 3.4 Frontend

| Component      | Technology    | Version | Purpose                           |
| -------------- | ------------- | ------- | --------------------------------- |
| **Framework**  | React         | 18+     | UI library                        |
| **Build Tool** | Vite          | 5+      | Fast HMR development              |
| **Language**   | TypeScript    | 5+      | Type safety                       |
| **State**      | Zustand       | 4+      | Lightweight state management      |
| **Charts**     | Recharts      | 2+      | Financial visualizations          |
| **Styling**    | CSS Variables | -       | Dark theme with custom properties |

### 3.5 Infrastructure

| Component            | Technology        | Purpose                              |
| -------------------- | ----------------- | ------------------------------------ |
| **Containerization** | Docker Compose    | Multi-service orchestration          |
| **Reverse Proxy**    | Cloudflare Tunnel | Stable webhook URLs, SSL termination |
| **Task Monitoring**  | Flower            | Celery task visualization            |
| **Model Registry**   | MLflow            | ML experiment tracking               |

---

## 4. Component Deep Dive

### 4.1 Broker Adapters

The broker layer provides a unified interface for market data and order execution across multiple brokers.

```
┌─────────────────────────────────────────────────────────────┐
│                    BrokerAdapter (Abstract)                  │
├─────────────────────────────────────────────────────────────┤
│ + get_quote(symbol) → Quote                                 │
│ + get_quotes_batch(symbols) → List[Quote]                   │
│ + get_history(symbol, period, interval) → List[OHLCVBar]    │
│ + get_indices() → List[Quote]                               │
│ + place_order(...) → OrderResult                            │
│ + cancel_order(order_id) → bool                             │
│ + get_positions() → List[Position]                          │
│ + get_holdings() → List[Position]                           │
└─────────────────────────────────────────────────────────────┘
                    △                    △
                    │                    │
        ┌───────────┴───────┐ ┌─────────┴─────────┐
        │  AngelOneAdapter  │ │   UpstoxAdapter   │
        │  (SmartAPI SDK)   │ │   (REST API v2)   │
        └───────────────────┘ └───────────────────┘
        ┌───────────────────┐
        │ YFinanceAdapter   │  ← Fallback (delayed data)
        │  (read-only)      │
        └───────────────────┘
```

**Session Caching (Angel One)**:

- JWT tokens cached in Redis with 23-hour TTL
- Key pattern: `broker:session:{user_id}:angel_one`
- Avoids TOTP re-authentication per request
- Daily `broker_reconnect` task refreshes all sessions at 8:00 AM IST

**Shared Credential Pool**:

- Users can opt-in their broker credentials for shared quote fetching
- Round-robin selection with degraded credential cooldown
- Reduces per-user API limits impact on WebSocket price streaming
- Stored in `broker_credentials.pool_eligible`

### 4.2 Services Layer

| Service                     | Purpose                                                           |
| --------------------------- | ----------------------------------------------------------------- |
| `price_service`             | Quote caching, batch fetching, shared cache management            |
| `screener_service`          | Paginated stock screener with live prices                         |
| `paper_trade_service`       | Virtual portfolio management with 0.03% brokerage simulation      |
| `live_trade_service`        | Real order execution via broker APIs                              |
| `feature_engineer`          | Technical indicator computation (RSI, MACD, Bollinger, ATR, etc.) |
| `ml_loader`                 | Thread-safe LightGBM model loading and inference                  |
| `lstm_service`              | LSTM autoencoder anomaly detection                                |
| `tft_service`               | TFT-based price forecasting                                       |
| `news_fetcher`              | Multi-source news aggregation (Google News, Yahoo Finance, RSS)   |
| `sentiment_scorer`          | FinBERT sentiment classification                                  |
| `ner_mapper`                | spaCy NER + fuzzy matching for news → symbol mapping              |
| `explainer`                 | LLM-powered signal explanation generation                         |
| `discord_service`           | Webhook notifications for signals and trades                      |
| `email_service`             | SMTP-based transactional emails (invites, OTP)                    |
| `push_notification_service` | Expo push notifications for mobile app                            |

### 4.3 IP Rotator

Built-in proxy rotation for bypassing rate limits on external data sources.

```python
from app.lib.ip_rotator import get_rotator

rotator = get_rotator()
session = rotator.get_session()
resp = session.get("https://api.example.com/data")
```

**Features**:

- Round-robin or random proxy selection strategies
- Dead proxy eviction after 3 consecutive failures
- Background health checks every 10 minutes to revive proxies
- Singleton pattern with lazy initialization

**Configuration**:

```bash
IP_ROTATOR_BACKEND=proxy_list
IP_ROTATOR_PROXY_LIST="socks5://user:pass@proxy1:1080
http://user:pass@proxy2:8080"
IP_ROTATOR_STRATEGY=round_robin
```

---

## 5. Data Pipeline

### 5.1 Data Flow

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           DATA INGESTION                                   │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                   │
│  │ NSE Bhavcopy│    │  Broker API │    │ News Sources│                   │
│  │  (Daily)    │    │   (Live)    │    │ (15-min)    │                   │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘                   │
│         │                  │                   │                          │
│         ▼                  ▼                   ▼                          │
│  ┌─────────────────────────────────────────────────────────┐             │
│  │              Celery Task Queue                           │             │
│  │  • bhavcopy.ingest_bhavcopy (7:30 PM IST)               │             │
│  │  • eod_ingest.ingest_eod (4:30 PM IST)                  │             │
│  │  • news_sentiment.fetch_news_sentiment (every 15 min)   │             │
│  │  • broker_reconnect.refresh_broker_sessions (8:00 AM)   │             │
│  └──────────────────────────┬──────────────────────────────┘             │
│                             │                                             │
│                             ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐             │
│  │                    TimescaleDB                           │             │
│  │  • ohlcv_daily (hypertable)                             │             │
│  │  • news_sentiment (hypertable)                          │             │
│  │  • signals (hypertable)                                 │             │
│  │  • stock_universe (regular table)                       │             │
│  └─────────────────────────────────────────────────────────┘             │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Signal Generation Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      SIGNAL GENERATION (4:45 PM IST)                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. FEATURE ENGINEERING                                                 │
│     ┌─────────────────────────────────────────────────────────┐        │
│     │ For each symbol in stock_universe:                       │        │
│     │   • Load 200 days OHLCV from ohlcv_daily                │        │
│     │   • Compute: RSI, MACD, Bollinger, ATR, OBV, ADX        │        │
│     │   • Add: News sentiment score (24h avg)                  │        │
│     │   • Build feature vector [1 x 18 features]              │        │
│     └─────────────────────────────────────────────────────────┘        │
│                               │                                         │
│                               ▼                                         │
│  2. ML INFERENCE                                                        │
│     ┌─────────────────────────────────────────────────────────┐        │
│     │ LightGBM binary classifier:                              │        │
│     │   P(up) = model.predict_proba(features)[1]              │        │
│     │   signal = BUY if P(up) > 0.6 else SELL if P(up) < 0.4  │        │
│     │   confidence = abs(P(up) - 0.5) * 2                      │        │
│     └─────────────────────────────────────────────────────────┘        │
│                               │                                         │
│                               ▼                                         │
│  3. RISK PARAMETERS                                                     │
│     ┌─────────────────────────────────────────────────────────┐        │
│     │ For BUY signals:                                         │        │
│     │   entry_price  = latest close                            │        │
│     │   target_price = entry × 1.05 (5% target)               │        │
│     │   stop_loss    = entry × 0.97 (3% stop)                 │        │
│     └─────────────────────────────────────────────────────────┘        │
│                               │                                         │
│                               ▼                                         │
│  4. PERSIST & NOTIFY                                                    │
│     ┌─────────────────────────────────────────────────────────┐        │
│     │ • INSERT INTO signals (deduplicated by hash)            │        │
│     │ • PUBLISH to Redis signal:new channel                   │        │
│     │ • POST to Discord webhook (if configured)               │        │
│     │ • Trigger explain_signal task (if confidence > 0.6)     │        │
│     └─────────────────────────────────────────────────────────┘        │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Machine Learning Models

### 6.1 LightGBM Signal Classifier

**Purpose**: Binary classification for next-day price direction

**Training Data**:

- Features: RSI, MACD histogram, Bollinger %B, ATR, OBV delta, ADX, volume ratio, sentiment score
- Label: 1 if close[t+1] > close[t] × 1.005, else 0
- Train/Val split: 85/15 temporal (no shuffle to prevent leakage)

**Retraining Schedule**: Weekly (Saturday 2:00 AM IST)

**Model Storage**: `/app/models/lgbm/lgbm_v{version}.pkl`

### 6.2 LSTM Autoencoder (Anomaly Detection)

**Purpose**: Detect unusual market behavior via reconstruction error

**Architecture**:

```
Input (50 timesteps × 4 features) → LSTM(64) → LSTM(32) →
RepeatVector(50) → LSTM(32) → LSTM(64) → TimeDistributed(Dense(4))
```

**Anomaly Score**: `reconstruction_error / threshold`  
Score > 1.0 indicates anomaly

### 6.3 TFT Price Forecaster

**Purpose**: 5-day ahead price prediction with uncertainty quantification

**Architecture**: Transformer-based with gating mechanisms

**Output**: 5 predicted close prices with confidence intervals

### 6.4 FinBERT Sentiment Scorer

**Purpose**: Classify financial news as positive/negative/neutral

**Model**: `ProsusAI/finbert` (HuggingFace)

**Processing**:

- Sliding window for texts > 512 tokens
- Batch inference for efficiency
- Returns sentiment label + confidence score

---

## 7. Broker Integration

### 7.1 Angel One (Primary)

**SDK**: `smartapi-python`

**Authentication Flow**:

1. Login with API key + client ID + MPIN + TOTP
2. Receive JWT + feed token
3. Cache in Redis (23h TTL)
4. Restore from cache on subsequent requests

**Capabilities**:

- Real-time quotes via `getMarketData`
- Historical OHLCV via `getCandleData`
- Order placement (MARKET/LIMIT)
- Position and holdings queries

### 7.2 Upstox (Secondary / Intraday Fallback)

**SDK**: REST API v2 (httpx)

**Authentication**: OAuth2 one-time browser flow

**OAuth2 Flow**:

1. User calls `GET /broker-credentials/upstox/authorize` → receives browser URL
2. User opens URL, logs in, grants access
3. Upstox redirects to `/broker-credentials/upstox/callback?code=…`
4. Server exchanges code → access token stored encrypted in `broker_credentials.access_token`
5. Access tokens expire at midnight IST; system checks at 7:30 AM and notifies via Discord/email

**Intraday Data Role**: When Angel One fails to return 15-min candles for a symbol, Upstox's
`/v2/historical-candle/{instrument_key}/15minute/{date}/{date}` is used as fallback.

**Capabilities**:

- Intraday 15-min historical candles (primary fallback use)
- Real-time quotes
- Order placement
- Position management

> **Note:** yfinance has been removed from all live data paths. The factory raises `ValueError` when no broker is configured rather than silently falling back to delayed data.

---

## 8. Security Architecture

### 8.1 Authentication

```
┌─────────────────────────────────────────────────────────────┐
│                   AUTHENTICATION FLOW                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  LOGIN:                                                     │
│    POST /auth/login {email, password, totp_code?}          │
│    → access_token (15 min, in response body)               │
│    → refresh_token (7 days, httpOnly cookie)               │
│                                                             │
│  PROTECTED REQUEST:                                         │
│    Authorization: Bearer <access_token>                     │
│    → JWT validated + jti checked against Redis blocklist   │
│                                                             │
│  TOKEN REFRESH:                                             │
│    POST /auth/refresh (cookie sent automatically)           │
│    → new access_token                                       │
│                                                             │
│  LOGOUT:                                                    │
│    POST /auth/logout                                        │
│    → jti added to Redis blocklist                          │
│    → refresh_token cookie deleted                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 TOTP (Two-Factor Authentication)

- **Required for**: Admin users
- **Optional for**: Traders
- **Storage**: TOTP secret encrypted with Fernet key
- **Setup**: QR code generation via `/auth/totp/setup`

### 8.3 Live Trading Gate

Live trading requires explicit enablement via email OTP:

1. User requests live mode: `POST /settings/live-trading/enable`
2. Server sends 6-digit OTP to registered email (10 min TTL)
3. User confirms: `POST /settings/live-trading/confirm` with OTP
4. Brute-force protection: 5 attempts, then 15-min lockout

### 8.4 Sensitive Data Encryption

| Data               | Encryption       | Storage                 |
| ------------------ | ---------------- | ----------------------- |
| TOTP secrets       | Fernet (AES-128) | `users.totp_secret`     |
| Broker credentials | Fernet (AES-128) | `broker_credentials.*`  |
| Passwords          | bcrypt (cost 12) | `users.hashed_password` |

### 8.5 Rate Limiting

| Route                | Limit            |
| -------------------- | ---------------- |
| `POST /auth/login`   | 10/min per IP    |
| `POST /auth/refresh` | 30/min per user  |
| `POST /orders`       | 20/min per user  |
| `GET /screener`      | 30/min per user  |
| `GET /prices/*`      | 120/min per user |
| Other authenticated  | 60/min per user  |

---

## 9. Infrastructure & Deployment

### 9.1 Docker Compose Services

| Service         | Image                             | Purpose                   |
| --------------- | --------------------------------- | ------------------------- |
| `postgres`      | timescale/timescaledb:latest-pg16 | Primary database          |
| `redis`         | redis:7-alpine                    | Cache + Celery broker     |
| `backend`       | Custom (FastAPI)                  | REST API + WebSocket      |
| `celery-worker` | Custom                            | Background task execution |
| `celery-beat`   | Custom                            | Periodic task scheduling  |
| `flower`        | Custom                            | Celery monitoring UI      |
| `mlflow`        | ghcr.io/mlflow/mlflow:v2.14.1     | ML experiment tracking    |
| `frontend`      | node:20-alpine                    | React development server  |
| `cloudflared`   | cloudflare/cloudflared:latest     | Tunnel for webhooks       |
| `db-migrate`    | Custom                            | Alembic migrations (init) |

### 9.2 Volume Mounts

| Volume                | Purpose                       |
| --------------------- | ----------------------------- |
| `postgres_data`       | PostgreSQL persistent storage |
| `redis_data`          | Redis AOF persistence         |
| `trained_models_data` | Shared ML model artifacts     |
| `mlflow_artifacts`    | MLflow experiment data        |
| `hf_model_cache`      | HuggingFace model cache       |
| `celery_beat_data`    | Beat scheduler state          |

### 9.3 Cloudflare Tunnel

Provides stable webhook URLs without exposing ports:

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate --url http://backend:8000
```

Webhook URL: Logged on container startup, register in broker developer portal.

---

## 10. API Design

### 10.1 Route Groups

| Prefix                       | Purpose                                                 |
| ---------------------------- | ------------------------------------------------------- |
| `/api/v1/auth`               | Authentication (login, logout, refresh, register, TOTP) |
| `/api/v1/settings`           | User preferences, live trading enablement               |
| `/api/v1/broker-credentials` | Broker API key management                               |
| `/api/v1/prices`             | Market quotes, indexes, history                         |
| `/api/v1/screener`           | Paginated stock screener                                |
| `/api/v1/signals`            | AI-generated trading signals                            |
| `/api/v1/portfolio/paper`    | Paper trading                                           |
| `/api/v1/portfolio/live`     | Live order execution                                    |
| `/api/v1/forecasts`          | LSTM anomaly + TFT price forecasts                      |
| `/api/v1/news`               | Sentiment scores and article feed                       |
| `/api/v1/mobile`             | Push token management                                   |
| `/api/v1/admin`              | User management, invite system                          |
| `/api/v1/admin/pipeline`     | Data pipeline control                                   |
| `/api/v1/ws/prices`          | WebSocket price streaming                               |
| `/api/v1/ws/signals`         | WebSocket signal notifications                          |
| `/api/v1/webhooks`           | Broker order postbacks                                  |

### 10.2 WebSocket Architecture

**Single Broadcaster Pattern**:

- One background task (`price_broadcaster`) fetches prices every 5 seconds
- Collects unique symbols across all connections
- Fans out price updates to per-connection queues
- Efficient even with hundreds of concurrent connections

```python
# Connection flow
ws://host/api/v1/ws/prices?token=<jwt>&symbols=RELIANCE.NS,TCS.NS

# Message format
{"symbol": "RELIANCE.NS", "price": 2543.50, "change_pct": 1.23, "ts": 1713700800}
```

---

## 11. Frontend Architecture

### 11.1 Page Structure

| Page           | Route               | Purpose                                            |
| -------------- | ------------------- | -------------------------------------------------- |
| Dashboard      | `/`                 | Portfolio summary, recent signals, market overview |
| Screener       | `/screener`         | Stock discovery with filters                       |
| Signal Log     | `/signals`          | Historical signal browser                          |
| Opportunities  | `/opportunities`    | High-confidence actionable signals                 |
| Paper Trading  | `/paper`            | Virtual portfolio management                       |
| Live Portfolio | `/live`             | Real broker positions and orders                   |
| Forecast       | `/forecast/:symbol` | LSTM anomaly + TFT predictions                     |
| Settings       | `/settings`         | Account, risk, broker configuration                |
| Admin          | `/admin`            | User management, pipeline control                  |

### 11.2 State Management

Zustand stores:

- `authStore`: User session, tokens, trading mode
- `settingsStore`: User preferences cache
- `portfolioStore`: Paper/live positions

### 11.3 API Client

Axios-based with interceptors:

- Automatic `Authorization` header injection
- 401 → token refresh → retry
- Base URL from `VITE_API_URL`

---

## 12. Scheduled Tasks

### 12.1 Celery Beat Schedule

| Task | Schedule | Description |
| ---- | -------- | ----------- |
| `bhavcopy.ingest_bhavcopy` | 7:30 PM IST Mon–Fri | NSE Bhavcopy EOD ingest |
| `eod_ingest.ingest_eod` | 4:30 PM IST Mon–Fri | EOD summary ingest |
| `signal_generator.generate_signals` | 8:30 AM IST Mon–Fri | Pre-market signal generation |
| `signal_generator.generate_signals` | 4:45 PM IST Mon–Fri | Post-market EOD signal generation |
| `intraday_ingest.ingest_intraday` | Every 15 min, 9:15–15:30 IST | Hybrid intraday OHLCV ingest (Angel One → Upstox) |
| `intraday_signal_generator.generate_intraday_signals` | Every 15 min, 9:30–15:15 IST | Continuous intraday signals |
| `upstox_token_refresh.check_upstox_tokens` | 7:30 AM IST Mon–Fri | Upstox token validity check + notification |
| `news_sentiment.fetch_news_sentiment` | Every 15 min, 9 AM–3:45 PM | News + sentiment pipeline |
| `news_sentiment.fetch_news_sentiment` | 8:20 AM IST Mon–Fri | Pre-signal warm-up |
| `news_sentiment.fetch_news_sentiment` | 4:40 PM IST Mon–Fri | Post-market news run |
| `breaking_news_scanner.scan_breaking_news` | Every 2 min, 9 AM–4 PM | Fast-path breaking news scanner |
| `forecast_tasks.persist_daily_forecasts` | 4:00 PM IST Mon–Fri | Persist PatchTST/TFT forecasts to `forecast_history` |
| `forecast_tasks.evaluate_forecast_accuracy` | 6:30 AM IST Mon–Fri | Compute RMSE/MAE/directional accuracy on matured forecasts |
| `ml_training.train_model` | Saturday 2:00 AM | Weekly LightGBM retrain |
| `eod_reconciliation.reconcile_live_orders` | 4:00 PM IST Mon–Fri | Live order sync |
| `macro_pulse.update_macro_regime` | Every 30 min, 9 AM–4 PM | Macro regime detection |
| `broker_reconnect.refresh_broker_sessions` | 8:00 AM IST Mon–Fri | Broker session refresh |
| `signal_outcome_evaluation.evaluate_signal_outcomes` | 5:00 PM IST Mon–Fri | EOD signal outcome evaluation |

### 12.2 Task Monitoring

- **Flower UI**: http://localhost:5555
- **Redis Keys**: `pipeline_task_status:{task_name}` stores last run info
- **Admin API**: `GET /admin/pipeline/status` returns all task statuses

---

## 13. Configuration Reference

### 13.1 Required Environment Variables

```bash
# Database
DB_USER=aitrader
DB_PASSWORD=<strong_password>
DB_NAME=aitrader
DB_HOST=postgres
DB_PORT=5432

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<strong_password>

# Security
JWT_SECRET_KEY=<64_char_hex>
FERNET_KEY=<base64_fernet_key>
INVITE_SIGNING_KEY=<32_byte_hex>
```

### 13.2 Optional Environment Variables

```bash
# Angel One Broker
ANGEL_API_KEY=
ANGEL_API_SECRET=
ANGEL_CLIENT_ID=
ANGEL_MPIN=
ANGEL_TOTP_SECRET=

# Upstox Broker
UPSTOX_API_KEY=
UPSTOX_API_SECRET=
UPSTOX_REDIRECT_URI=

# Email (SMTP)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=AI Trader <noreply@example.com>

# Discord Notifications
DISCORD_WEBHOOK_URL=

# ML Models (Google Drive)
LSTM_GDRIVE_ID=
TFT_GDRIVE_ID=

# LLM Explainability
EXPLAINABILITY_BACKEND=groq  # groq|gemini|local|disabled
GROQ_API_KEY=
GEMINI_API_KEY=
LOCAL_LLM_PATH=

# IP Rotation
IP_ROTATOR_BACKEND=none  # proxy_list|none
IP_ROTATOR_PROXY_LIST=
IP_ROTATOR_STRATEGY=round_robin

# Rate Limiting
RATE_LIMIT_DEFAULT=60/minute
RATE_LIMIT_SCREENER=30/minute
RATE_LIMIT_PRICES=120/minute

# Logo Service
LOGO_DEV_TOKEN=

# URLs
ALLOWED_ORIGINS=http://localhost:3000
FRONTEND_URL=http://localhost:3000
```

---

## 14. Database Schema

### 14.1 Core Tables

| Table                  | Type    | Purpose                           |
| ---------------------- | ------- | --------------------------------- |
| `users`                | Regular | User accounts                     |
| `user_settings`        | Regular | User preferences (1:1 with users) |
| `user_invites`         | Regular | Invite tokens for registration    |
| `refresh_tokens`       | Regular | JWT refresh token tracking        |
| `broker_credentials`   | Regular | Encrypted broker API keys         |
| `stock_universe`       | Regular | NSE/BSE symbols with metadata     |
| `paper_trades`         | Regular | Virtual portfolio trades          |
| `live_orders`          | Regular | Real broker orders                |
| `expo_push_tokens`     | Regular | Mobile push notification tokens   |
| `ml_models`            | Regular | Trained model registry            |
| `pipeline_task_status` | Regular | Background task status tracking   |

### 14.2 TimescaleDB Hypertables

| Table | Partition | Compression | Purpose |
| ----- | --------- | ----------- | ------- |
| `ohlcv_daily` | 7 days | After 90 days | Daily OHLCV bars |
| `ohlcv_intraday` | 1 day | After 7 days | 15-min intraday bars |
| `signals` | 7 days | After 30 days | AI trading signals |
| `signal_outcomes` | 7 days | After 30 days | Signal P&L evaluation |
| `news_sentiment` | 1 day | After 14 days | News articles + FinBERT scores |
| `model_predictions` | 7 days | After 30 days | Raw model probability outputs |
| `forecast_history` | 3 months | None | PatchTST/TFT forecasts + RMSE/MAE metrics |

### 14.3 Indexes

```sql
-- Performance-critical indexes
CREATE INDEX idx_ohlcv_symbol_ts ON ohlcv_daily (symbol, ts DESC);
CREATE INDEX idx_signals_active ON signals (is_active, ts DESC);
CREATE INDEX idx_signals_dedup ON signals (dedup_hash);
CREATE INDEX idx_stocks_active ON stock_universe (is_active);
CREATE UNIQUE INDEX idx_expo_active_token ON expo_push_tokens (token) WHERE is_active;
```

---

## 15. Development Phases

### Phase 1: Foundation ✅

- Docker Compose infrastructure
- TimescaleDB + Redis setup
- FastAPI skeleton with auth
- React frontend scaffold
- Alembic migrations

### Phase 2: Data Pipeline ✅

- NSE Bhavcopy ingest
- stock_universe population
- yfinance fallback adapter
- Basic screener UI

### Phase 3: ML Signals ✅

- LightGBM training pipeline
- Feature engineering service
- Signal generation task
- Signal API + UI

### Phase 4: News Sentiment ✅

- Multi-source news fetcher
- FinBERT sentiment scoring
- NER-based symbol mapping
- Sentiment API + UI

### Phase 5: Deep Learning ✅

- LSTM autoencoder (anomaly)
- TFT forecaster
- Colab training notebooks
- Forecast API + UI

### Phase 6: Live Trading ✅

- Angel One integration
- Upstox integration
- Order webhook handling
- Live portfolio UI

### Phase 7: Admin & Ops ✅

- Admin dashboard
- Pipeline monitoring
- User management
- Invite system

### Phase 8: Mobile & Infrastructure ✅

- Push notification backend
- Cloudflare Tunnel
- Shared credential pool
- Broker session caching
- IP rotation for rate limits

---

## Appendix A: File Structure

```
ai-trader/
├── backend/
│   ├── app/
│   │   ├── api/v1/           # REST endpoints
│   │   ├── brokers/          # Broker adapters
│   │   ├── core/             # Config, DB, security
│   │   ├── lib/              # IP rotator, utilities
│   │   ├── middleware/       # Logging, rate limiting
│   │   ├── models/           # SQLModel ORM
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/         # Business logic
│   │   ├── tasks/            # Celery tasks
│   │   └── main.py           # FastAPI app factory
│   ├── alembic/              # Migrations
│   ├── models/               # Trained ML artifacts
│   ├── scripts/              # Utility scripts
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/              # API client
│   │   ├── components/       # Reusable UI
│   │   ├── hooks/            # Custom hooks
│   │   ├── pages/            # Route components
│   │   ├── store/            # Zustand stores
│   │   └── App.tsx
│   └── package.json
├── colab/                    # Training notebooks
├── db_init/                  # TimescaleDB init SQL
├── docker-compose.yml
└── .env.example
```

---

## Appendix B: Key Redis Keys

| Pattern                              | TTL        | Purpose                      |
| ------------------------------------ | ---------- | ---------------------------- |
| `blocklist:{jti}`                    | 7 days     | Revoked JWT tokens           |
| `broker:session:{user_id}:{broker}`  | 23 hours   | Cached broker sessions       |
| `shared:quote:{symbol}`              | 60 seconds | Shared quote cache           |
| `screener_quotes:{broker}:{symbols}` | 30 seconds | Screener batch cache         |
| `news:sentiment:{symbol}`            | 5 minutes  | Sentiment cache              |
| `idem:paper:{user_id}:{key}`         | 5 minutes  | Idempotency cache            |
| `live_enable_otp:{user_id}`          | 10 minutes | Live trading OTP             |
| `live_enable_attempts:{user_id}`     | 15 minutes | OTP attempt counter          |
| `backfill:progress`                  | -          | Backfill progress %          |
| `pool:degraded:{credential_id}`      | 5 minutes  | Degraded credential cooldown |
| `pipeline_task_status:{task}`        | -          | Task execution status        |

---

## 16. NSE Data Architecture

### 16.1 Live Price Data Flow

```
REQUEST
  │
  ▼
Redis Cache (30s TTL)
  ├── HIT  → return instantly (zero external calls)
  └── MISS
        │
        ▼
   NSE India API  ← PRIMARY (1 call = all 50 symbols, ~15s delay, free)
        ├── SUCCESS → cache in Redis → return
        └── FAIL (maintenance / Akamai block)
              │
              ▼
         Broker API  ← FALLBACK (Angel One / Upstox, per user)
               └── SUCCESS / partial → cache in Redis → return
```

### 16.2 Historical Data Flow

```
REQUEST → Redis Cache (5 min TTL)
  └── MISS
        │
        ▼
   YFinance  ← ONLY source (free, years of NSE data, delay irrelevant for history)
        └── returns bars or empty list
```

### 16.3 Adapter Responsibility Matrix

| Adapter | Live Quotes | Indices | History | Orders |
|---------|-------------|---------|---------|--------|
| `NSEIndiaAdapter` | ✅ **PRIMARY** | ✅ **PRIMARY** | ❌ | ❌ |
| `YFinanceAdapter` | ❌ Disabled | ❌ Disabled | ✅ **ONLY SOURCE** | ❌ |
| `AngelOneAdapter` | ✅ Fallback | ✅ Fallback | ❌ | ✅ |
| `UpstoxAdapter` | ✅ Fallback | ✅ Fallback | ❌ | ✅ |

### 16.4 NSE India Adapter Key Details

- **Endpoints**: `/api/equity-stockIndices?index=NIFTY%2050` (bulk), `/api/allIndices`, `/api/quote-equity?symbol=<SYM>`
- **Session**: `curl_cffi` with `impersonate="chrome124"` for TLS fingerprint bypass
- **Circuit Breaker**: 5 failures → 120s recovery pause (module-level, not per-request)
- **Known Issue**: Local `threading.Lock()` is not visible across Celery worker processes. A Redis distributed lock is needed to prevent session stampede when cookies expire simultaneously across 4+ workers.

### 16.5 Redis Cache Key Reference (NSE)

| Key | TTL | Notes |
|-----|-----|-------|
| `nse:quotes:{symbols_sorted}` | 30s | Shared, all users |
| `nse:indices` | 30s | Shared, all users |
| `history:{sym}:{period}:{interval}` | 5min | Broker-agnostic |
| `fundamentals:{SYMBOL}` | 24h | yfinance PE/ROE/etc |
| `sentiment:{SYMBOL}` | 2h | FinBERT per-stock score |
| `macro:sentiment:regime` | 30min | HDBSCAN regime label |
| `macro:news:score` | 2h | FinBERT macro headlines score |

---

## 17. Full Signal Pipeline (9-Phase Architecture)

### 17.1 Target Signal Blend Formula (Institutional Upgrade)

```
base_conf = W_tech×tech + W_lgbm×lgbm + W_arf×arf + W_sentiment×sentiment + W_fno×fno_score + W_fund×fund_score
[Where W_x are dynamically optimized weekly by the Meta-Learner]

after_cap = min(base_conf, fund_hard_cap)
after_drift = after_cap × (1 - drift_penalty)
after_anomaly = after_drift × (1 - anomaly_penalty)
final_conf = min(after_anomaly × regime_multiplier, 1.0)
```

### 17.2 Dynamic Meta-Learner (Weights)

| Source | Default Weight | Meta-Learner Role |
|-------|--------|--------|
| Technical (RSI, MACD, etc.) | 30% | Dynamic Optimization |
| LightGBM (Batch ML) | 35% | Dynamic Optimization |
| River ARF (Online ML) | 10% | Dynamic Optimization |
| FinBERT Sentiment | 10% | Dynamic Optimization |
| F&O (PCR, OI Momentum) | 10% | NEW - Dynamic Optimization |
| Fundamentals Score | 5% | Fixed Multiplier |

### 17.3 Extended Feature Vector (16-feature vector)

```python
FEATURE_NAMES = [
    "rsi_14", "macd_hist", "bb_pct_b", "atr_pct", "obv_trend", "adx_14",
    "volume_ratio", "close_vs_sma20", "close_vs_sma50", "sentiment_score",
    "pcr_ratio",      # NEW: Put-Call Ratio (F&O)
    "oi_momentum",    # NEW: Open Interest % Change (F&O)
    "momentum_1m",    # (close[-1] / close[-22]) - 1
    "momentum_3m",    # (close[-1] / close[-66]) - 1
    "hist_vol_20d",   # std(log_returns[-20:]) * sqrt(252)
    "week52_proximity", # (close - 52w_low) / (52w_high - 52w_low)
]
```

### 17.4 Fundamentals Gate Rules (BUY signals only)

| Condition | Confidence Cap | Rationale |
|-----------|---------------|-----------|
| `debt_equity > 3.0` | 0.50 | High leverage = risky |
| `roe < 5%` | 0.60 | Poor capital efficiency |
| `pe_ratio > 60` | 0.65 | Extremely expensive |
| `pe_ratio < 0` (negative EPS) | 0.55 | Loss-making company |

SELL signals are **not** capped — bad fundamentals reinforce bearish bias.

### 17.5 Data Staleness Targets

| Layer | Refresh Cadence | Max Staleness |
|-------|----------------|---------------|
| Live price | Angel One WebSocket | ~1 second |
| Intraday OHLCV | Every 15 min | 15 minutes |
| News sentiment (per-stock) | Every 15 min | 15 minutes |
| Macro news score | Every 15 min | 15 minutes |
| Intraday signal | Every 15 min | 15 minutes |
| Macro regime (price-based) | Every 30 min | 30 minutes |
| Fundamentals (PE, ROE) | Daily 8:00 PM | 24 hours |
| EOD signal | 8:30 AM + 4:45 PM | ~8 hours overnight |

### 17.6 New Services Required

| File | Status | Purpose |
|------|--------|---------|
| `services/fundamentals_service.py` | **NEW** | yfinance PE/ROE fetch + Redis cache + score |
| `tasks/fundamentals_ingest.py` | **NEW** | Daily 8 PM Celery task for fundamentals refresh |
| `services/macro_news_scorer.py` | ✅ Exists | FinBERT on macro/global headlines |
| `services/river_amf.py` | ✅ Exists | River online learning model |
| `services/drift_detector.py` | ✅ Exists | ADWIN per-feature drift detection |
| `services/regime_detector.py` | ✅ Exists | HDBSCAN regime classification |

---

## 18. IP Rotation & Anti-Ban Strategy

### 18.1 Layer Architecture

```
LAYER A: TLS Fingerprint (curl_cffi)           ← Always ON
└── impersonate Chrome/Firefox/Safari TLS stack
    Effect: Passes Google's #1 bot check

LAYER B: Google Persona Cookies                ← Always ON
└── Pre-harvested NID/SOCS cookies via Playwright
    Effect: Looks like returning trusted user, not new bot

LAYER C: Per-IP Request Budget (Behavioural)   ← Always ON
└── Each IP sends random(5, 10) requests, then rotates
    Effect: Each IP looks like a normal human browsing

LAYER D: IP Pool (pick best available)
├── TRY #1: IPv6 Rotation (18 quintillion IPs if /64 subnet available)
├── TRY #2: IPv4 Rotation (round-robin if multiple IPs)
└── TRY #3: Header-Only (no IP change)

LAYER E: Persona Rotation                      ← Always ON
└── 4 independent browser identities (Chrome/Firefox/Safari)
    Each: own cookies + own IP + own TLS fingerprint

CIRCUIT BREAKER: 5 failures → 2 min block → auto-recover
```

### 18.2 curl_cffi Impersonation Targets

```python
_PERSONAS = ["chrome120", "chrome124", "firefox126", "safari17_0", "edge99"]
```

### 18.3 Google Persona Cookie Management

- `GooglePersonaManager` harvests `NID`, `SOCS`, `CONSENT` cookies via headless Playwright
- Cookies stored in Redis: `gf:persona:cookies` (4h TTL, well within NID's 6-month lifetime)
- Multiple independent personas (one per TLS fingerprint) — Google sees different "users"
- On CAPTCHA detection: cookies invalidated, background refresh triggered

### 18.4 IPv6 Rotation

If server has IPv6 /64 subnet: generate random 64-bit suffix per request. libcurl binds outbound connection to that address via `CURLOPT_INTERFACE`. Falls back to IPv4 multi-IP or header-only.

### 18.5 NSE-Specific Anti-Ban (Session Race Condition Fix)

**Problem**: When NSE cookies expire, all 4+ worker processes simultaneously hit the homepage, triggering Akamai bot detection.

**Fix**: Redis distributed lock on `lock:nse_session`:
```python
async with redis.lock("lock:nse_session", timeout=10):
    if not cache.exists("nse:cookies"):
        cookies = await fetch_new_session()
        cache.set("nse:cookies", cookies, ex=300)
```

---

## 19. Known Bugs & Fixes Log

### 19.1 Active Known Bugs (Not Yet Fixed)

| # | Bug | Severity | Files | Fix |
|---|-----|----------|-------|-----|
| A | **FinBERT formula wrong** — `score * 2 - 1` treats 3-class model as binary | 🔴 High | `news_sentiment.py`, `macro_news_scorer.py`, `news.py`, `signals.py` | Change to `P(positive) - P(negative)` |
| B | **`datetime.utcnow()` used in 15+ places** — day-boundary queries wrong in IST context | 🔴 High | `backfill.py`, `intraday_signal_generator.py`, `signals.py`, `yfinance_adapter.py`, etc. | Replace with `datetime.now(_IST)` where `_IST = timezone(timedelta(hours=5, minutes=30))` |
| C | **NSE session race condition** — local threading.Lock invisible to Celery workers | 🟡 Medium | `nse_india_adapter.py` | Redis distributed lock |
| D | **Realised P&L denominator** — net cash flow, not MTM | 🟡 Medium | `live_trade_service.py` | True Mark-to-Market with PriceService LTP |

### 19.2 Fixed Bugs (Applied via BUGFIXES, April 2026)

| # | Bug | Fix Applied |
|---|-----|-------------|
| 1 | Portfolio value used `SUM(price * filled_qty)` for all orders | ✅ Now uses BUY-minus-SELL net cash (L143–158 in `live_trade_service.py`) |
| 2 | `datetime.utcnow()` in task_utils, signal_generator, signal_outcome_evaluation | ✅ Changed to `datetime.now(_IST)` |
| 3 | Dead `_fetch_via_angel_one()` in `intraday_ingest.py` — O(N) logins | ✅ Removed |
| 4 | Celery intraday schedule fired at 15:30–15:59 (post-NSE close) | ✅ Split into 9:00–14:59 + 15:00,15:15 entries |
| 5 | Win-rate denominator counted unevaluated signals | ✅ Now uses `buy_evaluated_count` (only `is_evaluated=True` rows) |
| 6 | No notifications when signal hits target/stop-loss | ✅ `_send_outcome_notifications()` fires on first `False→True` transition |
| 7 | LightGBM trained with `sentiment_score=0.0` always | ✅ `_fetch_sentiment_history()` now supplies real historical scores |
| 8 | Redis macro cache bypassed — yfinance called 48×/day | ✅ Inline Redis cache added with 1h TTL |
| 9 | PatchTST dead code — TFT always used | ✅ PatchTST tried first; TFT is fallback |
| 10 | Missing standalone `signal_ts` index on `signal_outcomes` | ✅ Migration `0008_add_signal_ts_index.py` added |

### 19.3 Deferred Known Issues

| # | Issue | Reason Deferred |
|---|-------|-----------------|
| D1 | Survivorship bias in LightGBM training | Requires delisted-stock data procurement |
| D2 | `enable_utc=False` in Celery config | Changing requires rewriting all beat schedules to UTC |
| D3 | No NSE market holiday calendar | Requires NSE holiday API or static calendar |
| D4 | Forecast page blank for missing model | Frontend UX improvement |
| D5 | Win-rate shows no sample count | Frontend display enhancement |
| D6 | Signal analytics has no date range selector | Frontend feature request |

---

## 20. Roadmap

### Phase I: Secure-System (Financial Safety & Anti-Ban) — Next Sprint

| Task | Files | Impact |
|------|-------|--------|
| Fix FinBERT polarity formula | `news_sentiment.py`, `macro_news_scorer.py`, `news.py`, `signals.py` | Removes bearish bias from all sentiment |
| Unify all timestamps to IST | 15+ files | Fixes day-boundary signal/data join errors |
| Redis distributed lock for NSE session | `nse_india_adapter.py` | Prevents IP ban on session expiry |

### Phase II: High-Fidelity Data (Sentiment & Signal Quality)

| Task | Files | Impact |
|------|-------|--------|
| News deduplication (SHA256 + Redis SET) | `news_sentiment.py` | Removes 10× artificial sentiment spikes |
| Fundamentals service (PE, ROE, D/E) | New `fundamentals_service.py`, `fundamentals_ingest.py` | Adds quality gate to BUY signals |
| Signal freshness metadata in API | `signals.py`, `ForecastModal.tsx` | Users see signal age; staleness warnings |
| On-demand signal refresh endpoint | `signals.py` API, `ForecastModal.tsx` | `POST /signals/{symbol}/refresh` |
| NSE PDF analysis for earnings | New `pdf_extractor.py`, `pdf_summariser.py` | Richer FinBERT input on earnings days |

### Phase III: Intelligent-Ensemble (ML & Learning Loop) — Long-term

| Task | Files | Impact |
|------|-------|--------|
| Outcome-based ARF training (5-day forward return) | `river_amf.py`, `signal_generator.py` | ARF becomes a "corrector", not a copier |
| Forecast RMSE accuracy leaderboard | New migration + `forecasts.py` | Enables model A/B testing |
| True MTM portfolio valuation | `live_trade_service.py` | Correct circuit breaker denominator |

### Future (Post-Phase III)

- **Phase 10: Institutional Upgrades** (Meta-Learner, F&O Data, ATR Sizing)
- NSE PDF corporate announcement analysis (infrastructure ready, ~4.5h effort)
- Survivorship-bias-free LightGBM training (requires delisted stock data)
- NSE market holiday calendar integration
- Full signal analytics date-range selector (frontend)

---

## 21. Advanced Risk: Volatility-Adjusted Sizing

To achieve institutional-grade risk management, the system uses **ATR-based Position Sizing**. This ensures that the monetary risk (the amount lost if the stop-loss is hit) is constant, regardless of the stock's volatility.

### 21.1 The Math of Sizing

1.  **Risk per Trade ($R$):** The maximum amount of capital the user is willing to lose on a single trade.
    *   `R = Total Portfolio Value × Risk% (e.g., 1%)`
2.  **Distance to Stop ($D$):** The difference between the entry price and the stop-loss price.
    *   `D = Entry Price - Stop Loss Price`
3.  **Quantity ($Q$):** The number of shares to buy.
    *   `Q = R / D`

### 21.2 Example Comparison

| Scenario | Entry | Stop Loss | Distance (D) | Risk (R) | Quantity (Q) | Total Value |
|----------|-------|-----------|--------------|----------|--------------|-------------|
| **Stable Stock** | ₹1000 | ₹980 (2%) | ₹20 | ₹500 | **25 shares** | ₹25,000 |
| **Volatile Stock** | ₹1000 | ₹950 (5%) | ₹50 | ₹500 | **10 shares** | ₹10,000 |

*Result: In both cases, if the stop-loss is hit, the user loses exactly ₹500. This protects the portfolio from high-volatility "whipsaws".*


