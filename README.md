# AI Trader — Phase 1

Invite-only Indian stock market AI trading platform.
Phase 1: Authentication, user management, and foundational infrastructure.

## Quick Start

### 1. Generate secrets and copy `.env`

```bash
# Copy template
cp .env.example .env

# Generate secrets (run each command, paste output into .env)
python -c "import secrets; print(secrets.token_hex(32))"          # JWT_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY
python -c "import secrets; print(secrets.token_hex(32))"          # INVITE_SIGNING_KEY

# Set strong passwords for DB_PASSWORD, REDIS_PASSWORD
# Set ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME
```

### 2. Start services

```bash
docker compose up -d
```

Postgres runs on **5433**, Redis on **6379**, backend on **8000**, frontend on **3000**.

### 3. Run migrations

Migrations run automatically on backend startup via `alembic upgrade head`.
To run manually:

```bash
docker compose exec backend alembic upgrade head
```

### 4. Seed the first admin user

```bash
docker compose exec backend python scripts/seed_admin_user.py
```

### 5. Invite your first trader

Hit the API (or use Swagger at `http://localhost:8000/docs` in development):

```bash
# Login as admin
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"YOUR_ADMIN_PASSWORD"}' | jq .

# Invite a user (use the access_token from login)
curl -s -X POST http://localhost:8000/api/v1/admin/users/invite \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"trader@example.com"}' | jq .
```

The response includes a `registration_url` — share it with the user. They visit
`http://localhost:3000/register?token=...` to create their account.

---

## Re-imaging & Deployment Commands

### Rebuild a single service (e.g. after changing backend code or `requirements.txt`)

```bash
docker compose build backend
docker compose up -d backend
```

### Rebuild all custom images and restart everything

```bash
docker compose build
docker compose up -d
```

### Full nuclear re-image (wipe containers + images, keep data volumes)

```bash
# Stop and remove all containers + orphaned containers
docker compose down --remove-orphans

# Rebuild every image from scratch (no layer cache)
docker compose build --no-cache

# Bring everything back up
docker compose up -d
```

### Full factory reset (wipe ALL data — DB, Redis, model artifacts, HuggingFace cache)

> **Destructive — cannot be undone.** Only do this to start completely fresh.

```bash
docker compose down --volumes --remove-orphans
docker compose build --no-cache
docker compose up -d
```

### Apply new database migrations only

```bash
docker compose exec backend alembic upgrade head
```

### Re-seed the admin user (after a factory reset)

```bash
docker compose exec backend python scripts/seed_admin_user.py
```

### Restart individual services without rebuilding

```bash
# Restart backend + celery worker (e.g. after editing Python source)
docker compose restart backend celery-worker

# Restart everything
docker compose restart
```

### Tail logs for a service

```bash
docker compose logs -f backend
docker compose logs -f celery-worker
docker compose logs -f celery-beat
```

### Check container health

```bash
docker compose ps
```

### Trigger a manual model training run

```bash
docker compose exec celery-worker sh -c \
  "PYTHONPATH=/app celery -A app.tasks.celery_app call app.tasks.ml_training.train_model"
```

### Trigger a manual Bhavcopy ingest

```bash
docker compose exec celery-worker sh -c \
  "PYTHONPATH=/app celery -A app.tasks.celery_app call app.tasks.bhavcopy.ingest_bhavcopy"
```

---

## Architecture

See [DESIGN.md](DESIGN.md), [DATABASE.md](DATABASE.md), and [API.md](API.md).

## Directory Structure

```
backend/               FastAPI backend
  app/
    core/              Config, security, DB, Redis, logging
    models/            SQLModel ORM models
    schemas/           Pydantic request/response schemas
    api/v1/            Route handlers
    services/          Business logic
    middleware/        Request logging
  alembic/             Database migrations
  scripts/             Admin utilities
db_init/               TimescaleDB Docker init SQL
  01_init_timescaledb.sql   Hypertables, indexes, triggers
  02_pipeline_task_status.sql  Pipeline task status table + seed rows
frontend/              Vite + React + TypeScript
docker-compose.yml     Services: postgres, redis, backend, frontend
.env.example           Environment variable template
```

## Phase 1 Features

- [x] JWT authentication (15-min access token, 7-day httpOnly refresh cookie)
- [x] Invite-only registration (admin creates single-use 24h invite links)
- [x] Bcrypt password hashing (cost 12)
- [x] TOTP support for admin (optional for Phase 1; full setup in Phase 2)
- [x] Redis JWT blocklist for logout
- [x] Structlog JSON logging with sensitive data redaction
- [x] Alembic migrations
- [x] TimescaleDB foundation
- [x] React frontend with auth guards, login/register pages, Zustand auth store
- [x] Axios auto-refresh interceptor

## Phase 2 Features (Complete)

- [x] Market data ingestion (TimescaleDB hypertables, Celery EOD ingest)
- [x] WebSocket price streaming with Redis fan-out
- [x] Signal generation pipeline
- [x] Celery workers + Redis task queue

## Phase 3 Features (Complete)

- [x] DB-backed pipeline task status (`pipeline_task_status` PostgreSQL table — durable across worker restarts)
- [x] Admin pipeline panel with per-task live log viewer (TaskLogModal, auto-polling)
- [x] Startup reset: tasks interrupted by a worker crash are shown as `unknown` on next backend start
- [x] DB browser with live row counts for all tables
- [x] `GET /admin/pipeline/{task_name}/logs` API endpoint (reads ephemeral per-task log lines from Redis)
- [x] TanStack Query on the frontend (stale-while-revalidate)
- [x] Alembic migration init container (db-migrate)
- [x] Circuit-breaker pattern on broker API calls

## Phase 4 Features — News Sentiment Pipeline (Complete)

- [x] RSS feed fetcher (ET Markets, Moneycontrol, Business Standard, Livemint, NSE, BSE corporate)
- [x] Google News fetcher (gnews, top 50 symbols by market cap)
- [x] NER entity extraction (spaCy `en_core_web_sm`) + fuzzy symbol mapping (rapidfuzz)
- [x] FinBERT batch inference (ProsusAI/finbert, CPU default / `SENTIMENT_DEVICE=cuda` for GPU)
- [x] `news_sentiment` TimescaleDB hypertable (1-day chunks, dedup by URL+symbol)
- [x] Celery Beat task every 15 min Mon–Fri 9:00 AM – 3:45 PM IST
- [x] Redis rolling 24-h weighted sentiment cache per symbol (`sentiment:<SYM>`, 2-h TTL)
- [x] `GET /api/v1/news/sentiment?symbol=X` — aggregated score from cache (DB fallback)
- [x] `GET /api/v1/news/feed?symbol=X&limit=20` — recent headlines with per-article scores

## Phase 3 Features — AI/ML Pipeline (Complete)

- [x] Feature engineering: RSI(14), MACD histogram, Bollinger %B, ATR%, OBV trend, ADX(14), volume ratio, SMA20/50 deviation, Phase 4 sentiment score
- [x] LightGBM binary classifier — trained on sliding-window OHLCV features
- [x] `ml_models` table — tracks model versions, artifact paths, metrics, active flag
- [x] `model_predictions` TimescaleDB hypertable — per-symbol ML output audit trail
- [x] Signal generator upgraded: blends technical (40%) + ML probability (45%) + sentiment (15%)
- [x] Graceful fallback to technical-only when no active model exists
- [x] MLflow integration — optional; set `MLFLOW_TRACKING_URI` env var to enable
- [x] Admin endpoints: `POST /train-model`, `GET /models`, `POST /models/{id}/promote`, `POST /models/{id}/rollback`
- [x] In-process ML model loader with 5-min auto-reload on version change

## Phase 5 Features — Paper Trading + Execution (Complete)

- [x] `paper_trades` TimescaleDB table — per-user simulated trade ledger
- [x] Paper balance management in `user_settings` — deducted on open, restored + P&L on close
- [x] Auto-execution: when signals fire, paper trades placed for all `trading_mode='paper'` users (enable via `PAPER_AUTO_TRADE=true`)
- [x] `GET /api/v1/portfolio/paper/summary` — cash balance, realized P&L, win rate, open positions
- [x] `GET /api/v1/portfolio/paper/positions` — open paper trades
- [x] `GET /api/v1/portfolio/paper/history?limit=N` — closed trade history
- [x] `POST /api/v1/portfolio/paper/orders` — manually place a paper trade
- [x] `POST /api/v1/portfolio/paper/orders/{id}/close` — close position (auto-fetches live price via yfinance if not provided)

## Phase 6 Features — Live Execution + Advanced ML (Complete)

- [x] Angel One SmartAPI live order routing (`backend/app/brokers/angel_one.py`)
- [x] Fernet-encrypted broker credentials stored per user (`broker_credentials` table)
- [x] Idempotent order placement — broker `ordertag` (UUID) prevents double-orders on network timeout
- [x] Timeout recovery: queries Angel One `getOrderBook` by tag before failing, marks row `TIMEOUT` if genuinely lost
- [x] Broker order-status webhook `POST /api/v1/webhooks/order-update` (Angel One postback)
- [x] Webhook race-condition safety — Celery retry (`countdown=3`) if postback arrives before INSERT commits
- [x] LSTM autoencoder for OHLCV anomaly detection (`backend/app/services/lstm_service.py`)
- [x] TFT (Temporal Fusion Transformer) multi-step price forecasting (`backend/app/services/tft_service.py`)
- [x] Google Drive model download at startup (`LSTM_GDRIVE_ID`, `TFT_GDRIVE_ID` in `.env`)
- [x] Celery `worker_ready` hook — eagerly loads LSTM + TFT models on worker boot (no cold-load spike)
- [x] Discord webhook alerts for order fills (`DISCORD_WEBHOOK_URL` in `.env`)
- [ ] Upstox live execution (deferred — OAuth redirect flow requires a public redirect URI)

## Coming in Phase 3 (AI/ML)

- ~~LightGBM / XGBoost signal models trained on technical features~~ ✅ Done (Phase 3)
- ~~LSTM autoencoder for anomaly detection~~ → Phase 5 (GPU recommended)
- ~~TFT (Temporal Fusion Transformer) price forecasting~~ → Phase 5 (GPU recommended)
- ~~Model versioning with MLflow~~ ✅ Done (Phase 3)
- ~~Trade execution (paper + live via Angel One / Upstox)~~ → Paper ✅ Done (Phase 5) · Live → Phase 5
- ~~TOTP setup endpoint for admin~~ ✅ Done (Phase 1)
- ~~Discord webhook alerts~~ ✅ Done (Phase 3)

