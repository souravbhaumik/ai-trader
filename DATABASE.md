# AI Trader — Database Schema Documentation

> **PostgreSQL 16 + TimescaleDB 2**  
> **Version**: 2.0  
> **Last Updated**: April 2026

All UUIDs use `gen_random_uuid()`. All timestamps are `TIMESTAMPTZ` (UTC stored, IST displayed in UI).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Entity Relationship Diagram](#2-entity-relationship-diagram)
3. [Relational Tables](#3-relational-tables)
   - 3.1 [users](#31-users)
   - 3.2 [user_settings](#32-user_settings)
   - 3.3 [user_invites](#33-user_invites)
   - 3.4 [refresh_tokens](#34-refresh_tokens)
   - 3.5 [broker_credentials](#35-broker_credentials)
   - 3.6 [stock_universe](#36-stock_universe)
   - 3.7 [paper_trades](#37-paper_trades)
   - 3.8 [live_orders](#38-live_orders)
   - 3.9 [ml_models](#39-ml_models)
   - 3.10 [expo_push_tokens](#310-expo_push_tokens)
   - 3.11 [pipeline_task_status](#311-pipeline_task_status)
4. [TimescaleDB Hypertables](#4-timescaledb-hypertables)
   - 4.1 [ohlcv_daily](#41-ohlcv_daily)
   - 4.2 [ohlcv_1min](#42-ohlcv_1min)
   - 4.3 [signals](#43-signals)
   - 4.4 [news_sentiment](#44-news_sentiment)
   - 4.5 [model_predictions](#45-model_predictions)
5. [Indexes](#5-indexes)
6. [Triggers & Functions](#6-triggers--functions)
7. [Redis Keys](#7-redis-keys)
8. [Maintenance](#8-maintenance)

---

## 1. Overview

### Database Technologies

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Primary DB** | PostgreSQL 16 | Relational data storage |
| **Time-Series Extension** | TimescaleDB 2 | OHLCV data, signals, news with automatic partitioning and compression |
| **Cache/Broker** | Redis 7 | Session cache, rate limiting, Celery broker |
| **Migrations** | Alembic | Schema version control |

### Table Categories

| Category | Tables | Storage |
|----------|--------|---------|
| **User Management** | users, user_settings, user_invites, refresh_tokens | Regular PostgreSQL |
| **Trading** | paper_trades, live_orders, broker_credentials | Regular PostgreSQL |
| **Market Data** | ohlcv_daily, ohlcv_1min, stock_universe | TimescaleDB hypertables (OHLCV) |
| **ML/AI** | signals, ml_models, model_predictions, news_sentiment | TimescaleDB hypertables |
| **Infrastructure** | expo_push_tokens, pipeline_task_status | Regular PostgreSQL |

### Storage Estimates

| Table | Rows/Day (est.) | Compression | Retention |
|-------|-----------------|-------------|-----------|
| `ohlcv_daily` | ~2,500 | 90 days | Indefinite |
| `ohlcv_1min` | ~900,000 | 7 days | 90 days |
| `signals` | ~500 | None | Indefinite |
| `news_sentiment` | ~1,000 | 14 days | 180 days |

---

## 2. Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            USER DOMAIN                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   users ──────────────────────────────────────────────────────────────────┐ │
│     │                                                                     │ │
│     ├──(1:1)──► user_settings                                            │ │
│     │                                                                     │ │
│     ├──(1:N)──► user_invites (as inviter via invited_by)                │ │
│     │              └──(N:1)──► users (as invitee via user_id)           │ │
│     │                                                                     │ │
│     ├──(1:N)──► refresh_tokens                                           │ │
│     │                                                                     │ │
│     ├──(1:N)──► broker_credentials                                       │ │
│     │                                                                     │ │
│     ├──(1:N)──► paper_trades                                             │ │
│     │                                                                     │ │
│     ├──(1:N)──► live_orders                                              │ │
│     │                                                                     │ │
│     ├──(1:N)──► expo_push_tokens                                         │ │
│     │                                                                     │ │
│     └──(self)──► users (invited_by self-reference)                       │ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          MARKET DATA DOMAIN                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   stock_universe ─────────────────────────────────────────────────────────┐ │
│     │  (symbol is the FK reference for time-series tables)               │ │
│     │                                                                     │ │
│     ├──(1:N)──► ohlcv_daily      [hypertable]                           │ │
│     │                                                                     │ │
│     ├──(1:N)──► ohlcv_1min       [hypertable]                           │ │
│     │                                                                     │ │
│     ├──(1:N)──► signals          [hypertable]                           │ │
│     │                                                                     │ │
│     └──(1:N)──► news_sentiment   [hypertable]                           │ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                            ML DOMAIN                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ml_models ──────────────────────────────────────────────────────────────┐ │
│     │                                                                     │ │
│     ├──(1:N)──► model_predictions  [hypertable]                         │ │
│     │                                                                     │ │
│     └──(N:1)──► users (promoted_by)                                      │ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                         INFRASTRUCTURE                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   pipeline_task_status ── standalone (no FK, infrastructure table)         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Relational Tables

### 3.1 `users`

Primary identity table. All user-scoped tables reference this via foreign key.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK, DEFAULT `gen_random_uuid()` | Surrogate key |
| `email` | `VARCHAR(255)` | UNIQUE NOT NULL | Login credential |
| `hashed_password` | `VARCHAR(255)` | NOT NULL | bcrypt hash (cost 12) |
| `full_name` | `VARCHAR(100)` | | Display name |
| `role` | `VARCHAR(20)` | NOT NULL DEFAULT `'trader'` | `admin` \| `trader` \| `viewer` |
| `is_active` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Deactivated users cannot log in |
| `is_email_verified` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | Must be `TRUE` before trading |
| `is_live_trading_enabled` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | Set via email OTP confirmation |
| `totp_secret` | `VARCHAR(255)` | | Fernet-encrypted TOTP secret |
| `is_totp_configured` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | `TRUE` after TOTP setup complete |
| `invited_by` | `UUID` | FK → `users.id` SET NULL | Admin who created the invite |
| `last_login_at` | `TIMESTAMPTZ` | | Updated on successful login |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Auto-updated via trigger |

**Indexes**:
- `idx_users_email` ON `(email)`

**Relationships**:
- `users` → `user_settings` : **1:1** (created automatically on registration)
- `users` → `broker_credentials` : **1:N** (one per broker)
- `users` → `paper_trades` : **1:N**
- `users` → `live_orders` : **1:N**
- `users` → `refresh_tokens` : **1:N**
- `users` → `user_invites` : **1:N** (as inviter)
- `users` → `expo_push_tokens` : **1:N**

---

### 3.2 `user_settings`

User preferences and trading configuration. One row per user.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `user_id` | `UUID` | PK, FK → `users.id` CASCADE | 1:1 with users |
| `trading_mode` | `VARCHAR(10)` | NOT NULL DEFAULT `'paper'` | `paper` \| `live` |
| `paper_balance` | `NUMERIC(15,2)` | NOT NULL DEFAULT `1000000.00` | Virtual capital in ₹ |
| `max_position_pct` | `NUMERIC(5,2)` | NOT NULL DEFAULT `10.00` | Max % of capital per position |
| `daily_loss_limit_pct` | `NUMERIC(5,2)` | NOT NULL DEFAULT `5.00` | Halt trading after N% loss |
| `notification_signals` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Push new signals |
| `notification_orders` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Push order fills |
| `notification_news` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Push sentiment alerts |
| `preferred_broker` | `VARCHAR(20)` | | `angel_one` \| `upstox` \| `yfinance` |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Auto-updated via trigger |

---

### 3.3 `user_invites`

Invitation tokens for user registration. Admins create invites; users register via token.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `email` | `VARCHAR(255)` | NOT NULL | Invited email address |
| `token_hash` | `VARCHAR(255)` | UNIQUE NOT NULL | SHA-256 of raw invite token |
| `status` | `VARCHAR(20)` | NOT NULL DEFAULT `'pending'` | `pending` \| `used` \| `expired` \| `revoked` |
| `invited_by` | `UUID` | FK → `users.id` CASCADE | Admin who created invite |
| `user_id` | `UUID` | FK → `users.id` SET NULL | User who registered (after use) |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL | Token expiration (7 days default) |
| `used_at` | `TIMESTAMPTZ` | | When registration completed |
| `revoked_at` | `TIMESTAMPTZ` | | When manually revoked |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `idx_user_invites_email` ON `(email)`

---

### 3.4 `refresh_tokens`

JWT refresh token tracking for session management.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | FK → `users.id` CASCADE | |
| `token_hash` | `VARCHAR(255)` | UNIQUE NOT NULL | SHA-256 of raw token |
| `jti` | `UUID` | UNIQUE NOT NULL | JWT `jti` claim for blocklist |
| `issued_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL | 7 days after issue |
| `revoked_at` | `TIMESTAMPTZ` | | Set on logout |
| `user_agent` | `VARCHAR(255)` | | Browser/device info |
| `ip_address` | `VARCHAR(45)` | | Client IP (IPv4/IPv6) |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `idx_refresh_tokens_user_id` ON `(user_id)`
- `idx_refresh_tokens_jti` ON `(jti)`

---

### 3.5 `broker_credentials`

Encrypted broker API credentials for market data and order execution.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | FK → `users.id` CASCADE | |
| `broker_name` | `VARCHAR(20)` | NOT NULL | `angel_one` \| `upstox` \| `yfinance` |
| `client_id` | `TEXT` | | Fernet-encrypted client ID |
| `api_key` | `TEXT` | | Fernet-encrypted API key |
| `api_secret` | `TEXT` | | Fernet-encrypted API secret |
| `totp_secret` | `TEXT` | | Fernet-encrypted TOTP (Angel One) |
| `is_configured` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | All required fields present |
| `pool_eligible` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | User opted into shared quote pool |
| `last_verified` | `TIMESTAMPTZ` | | Last successful API connection |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `idx_broker_creds_user` ON `(user_id)`

**Constraints**:
- `UNIQUE (user_id, broker_name)` — one credential per broker per user

---

### 3.6 `stock_universe`

Master list of tradeable securities (NSE/BSE).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(32)` | PK | e.g., `RELIANCE.NS` |
| `name` | `VARCHAR(200)` | NOT NULL DEFAULT `''` | Company name |
| `exchange` | `VARCHAR(10)` | NOT NULL DEFAULT `'NSE'` | `NSE` \| `BSE` |
| `sector` | `VARCHAR(100)` | NOT NULL DEFAULT `'Unknown'` | GICS sector |
| `industry` | `VARCHAR(100)` | NOT NULL DEFAULT `''` | GICS industry |
| `market_cap` | `BIGINT` | | Market cap in ₹ |
| `is_etf` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | Exchange-traded fund flag |
| `is_active` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Include in screener/signals |
| `in_nifty50` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | Index membership |
| `in_nifty500` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | Index membership |
| `logo_path` | `VARCHAR(300)` | | Path to company logo PNG |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `idx_universe_sector` ON `(sector)`
- `idx_universe_market_cap` ON `(market_cap DESC NULLS LAST)`
- `idx_universe_active` ON `(is_active)` WHERE `is_active = TRUE`

---

### 3.7 `paper_trades`

Virtual trading positions for practice and backtesting.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | FK → `users.id` CASCADE | |
| `symbol` | `VARCHAR(32)` | NOT NULL | e.g., `RELIANCE.NS` |
| `direction` | `VARCHAR(8)` | NOT NULL | `BUY` \| `SELL` |
| `qty` | `INTEGER` | NOT NULL | Number of shares |
| `entry_price` | `NUMERIC(14,4)` | NOT NULL | Execution price |
| `target_price` | `NUMERIC(14,4)` | | Take-profit level |
| `stop_loss` | `NUMERIC(14,4)` | | Stop-loss level |
| `exit_price` | `NUMERIC(14,4)` | | Actual exit price |
| `signal_id` | `UUID` | | Link to triggering signal |
| `status` | `VARCHAR(16)` | NOT NULL DEFAULT `'open'` | `open` \| `closed` \| `sl_hit` \| `target_hit` \| `cancelled` |
| `pnl` | `NUMERIC(14,4)` | | Absolute P&L in ₹ |
| `pnl_pct` | `NUMERIC(10,4)` | | Percentage P&L |
| `entry_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Position opened |
| `exit_at` | `TIMESTAMPTZ` | | Position closed |
| `notes` | `TEXT` | | User notes |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `ix_paper_trades_user_id` ON `(user_id)`
- `ix_paper_trades_symbol` ON `(symbol)`
- `ix_paper_trades_status` ON `(user_id, status)`
- `ix_paper_trades_entry_at` ON `(entry_at DESC)`

---

### 3.8 `live_orders`

Real broker orders for live trading.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | FK → `users.id` CASCADE | |
| `broker_order_id` | `VARCHAR(64)` | | Broker's order reference |
| `symbol` | `VARCHAR(32)` | NOT NULL | e.g., `RELIANCE.NS` |
| `exchange` | `VARCHAR(8)` | NOT NULL DEFAULT `'NSE'` | `NSE` \| `BSE` |
| `direction` | `VARCHAR(4)` | NOT NULL | `BUY` \| `SELL` |
| `qty` | `INTEGER` | NOT NULL | Order quantity |
| `order_type` | `VARCHAR(16)` | NOT NULL DEFAULT `'MARKET'` | `MARKET` \| `LIMIT` \| `SL` \| `SL-M` |
| `product_type` | `VARCHAR(16)` | NOT NULL DEFAULT `'DELIVERY'` | `DELIVERY` \| `INTRADAY` \| `CNC` |
| `price` | `NUMERIC(12,4)` | NOT NULL DEFAULT `0` | Limit price (0 for MARKET) |
| `status` | `VARCHAR(16)` | NOT NULL DEFAULT `'PENDING'` | `PENDING` \| `OPEN` \| `FILLED` \| `REJECTED` \| `CANCELLED` |
| `broker_status` | `VARCHAR(32)` | | Raw status from broker |
| `filled_qty` | `INTEGER` | NOT NULL DEFAULT `0` | Executed quantity |
| `avg_fill_price` | `NUMERIC(12,4)` | NOT NULL DEFAULT `0` | Average execution price |
| `signal_id` | `UUID` | | Link to triggering signal |
| `placed_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Order submission time |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Last status update |

**Indexes**:
- `ix_live_orders_user_id` ON `(user_id, placed_at DESC)`
- `idx_live_orders_broker_oid` ON `(broker_order_id)`

---

### 3.9 `ml_models`

Trained model registry with versioning and metrics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `model_type` | `VARCHAR(32)` | NOT NULL | `lgbm` \| `lstm` \| `tft` |
| `version` | `VARCHAR(64)` | NOT NULL | Semantic version string |
| `artifact_path` | `TEXT` | NOT NULL | Path to model file |
| `mlflow_run_id` | `VARCHAR(64)` | | MLflow experiment run ID |
| `metrics` | `JSONB` | NOT NULL DEFAULT `'{}'` | `{"accuracy": 0.65, "f1": 0.62}` |
| `hyperparams` | `JSONB` | NOT NULL DEFAULT `'{}'` | Training hyperparameters |
| `feature_names` | `JSONB` | NOT NULL DEFAULT `'[]'` | Feature column names |
| `is_active` | `BOOLEAN` | NOT NULL DEFAULT `FALSE` | Currently in production |
| `trained_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Training completion time |
| `promoted_at` | `TIMESTAMPTZ` | | When promoted to production |
| `promoted_by` | `UUID` | FK → `users.id` SET NULL | Admin who promoted |
| `notes` | `TEXT` | | Version notes |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `ix_ml_models_type_active` ON `(model_type, is_active)`
- `ix_ml_models_trained_at` ON `(trained_at DESC)`

---

### 3.10 `expo_push_tokens`

Mobile push notification token management.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | FK → `users.id` CASCADE | |
| `token` | `VARCHAR(200)` | NOT NULL | Expo push token |
| `device_id` | `VARCHAR(100)` | NOT NULL | Device identifier |
| `platform` | `VARCHAR(10)` | NOT NULL | `ios` \| `android` |
| `is_active` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Token validity flag |
| `registered_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Token registration time |
| `last_used_at` | `TIMESTAMPTZ` | | Last successful push |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Indexes**:
- `uniq_expo_token_active` ON `(token)` WHERE `is_active = TRUE` — UNIQUE
- `uniq_expo_device_active` ON `(device_id)` WHERE `is_active = TRUE` — UNIQUE
- `idx_expo_tokens_user` ON `(user_id)` WHERE `is_active = TRUE`

---

### 3.11 `pipeline_task_status`

Background task execution status tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `task_name` | `VARCHAR(100)` | PK | Task identifier |
| `status` | `VARCHAR(20)` | NOT NULL DEFAULT `'idle'` | `idle` \| `running` \| `completed` \| `failed` |
| `message` | `TEXT` | NOT NULL DEFAULT `'Never run.'` | Human-readable status |
| `started_at` | `TIMESTAMPTZ` | | Task start time |
| `finished_at` | `TIMESTAMPTZ` | | Task completion time |
| `summary` | `JSONB` | NOT NULL DEFAULT `'{}'` | Task-specific metrics |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**Pre-seeded Tasks**:
- `universe_population`
- `broker_backfill`
- `bhavcopy`
- `backfill`
- `feature_engineering`
- `eod_ingest`
- `ml_training`
- `signal_generator`
- `news_sentiment`
- `broker_reconnect`

---

## 4. TimescaleDB Hypertables

Hypertables provide automatic time-based partitioning and compression for time-series data.

### 4.1 `ohlcv_daily`

Daily OHLCV bars for all securities.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(32)` | PK (composite) | Security symbol |
| `ts` | `TIMESTAMP` | PK (composite) | Trading date |
| `open` | `NUMERIC(12,4)` | NOT NULL | Opening price |
| `high` | `NUMERIC(12,4)` | NOT NULL | High price |
| `low` | `NUMERIC(12,4)` | NOT NULL | Low price |
| `close` | `NUMERIC(12,4)` | NOT NULL | Closing price |
| `volume` | `BIGINT` | NOT NULL DEFAULT `0` | Traded volume |
| `source` | `VARCHAR(20)` | NOT NULL DEFAULT `'nse'` | Data source |

**TimescaleDB Configuration**:
```sql
SELECT create_hypertable(
    'ohlcv_daily', 'ts',
    partitioning_column => 'symbol',
    number_partitions => 4
);

-- Compression after 90 days
ALTER TABLE ohlcv_daily SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol'
);
SELECT add_compression_policy('ohlcv_daily', INTERVAL '90 days');
```

**Indexes**:
- `idx_ohlcv_daily_symbol_ts` ON `(symbol, ts DESC)`

---

### 4.2 `ohlcv_1min`

Minute-level OHLCV bars for intraday analysis.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(32)` | PK (composite) | Security symbol |
| `ts` | `TIMESTAMP` | PK (composite) | Minute timestamp |
| `open` | `NUMERIC(12,4)` | NOT NULL | Opening price |
| `high` | `NUMERIC(12,4)` | NOT NULL | High price |
| `low` | `NUMERIC(12,4)` | NOT NULL | Low price |
| `close` | `NUMERIC(12,4)` | NOT NULL | Closing price |
| `volume` | `BIGINT` | NOT NULL DEFAULT `0` | Traded volume |
| `source` | `VARCHAR(20)` | NOT NULL DEFAULT `'angel_one'` | Data source |

**TimescaleDB Configuration**:
```sql
SELECT create_hypertable(
    'ohlcv_1min', 'ts',
    partitioning_column => 'symbol',
    number_partitions => 4
);

-- Compression after 7 days
ALTER TABLE ohlcv_1min SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol'
);
SELECT add_compression_policy('ohlcv_1min', INTERVAL '7 days');
```

**Indexes**:
- `idx_ohlcv_1min_symbol_ts` ON `(symbol, ts DESC)`

---

### 4.3 `signals`

AI-generated trading signals.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK (composite) | Signal ID |
| `ts` | `TIMESTAMP` | PK (composite) | Signal generation time |
| `symbol` | `VARCHAR(32)` | NOT NULL | Security symbol |
| `signal_type` | `VARCHAR(10)` | NOT NULL | `BUY` \| `SELL` \| `HOLD` |
| `confidence` | `NUMERIC(5,4)` | NOT NULL DEFAULT `0` | Model confidence [0.0-1.0] |
| `entry_price` | `NUMERIC(12,4)` | | Suggested entry price |
| `target_price` | `NUMERIC(12,4)` | | Take-profit target |
| `stop_loss` | `NUMERIC(12,4)` | | Stop-loss level |
| `model_version` | `VARCHAR(50)` | NOT NULL DEFAULT `'v0'` | Model version string |
| `features` | `JSONB` | | Feature values used |
| `is_active` | `BOOLEAN` | NOT NULL DEFAULT `TRUE` | Signal validity |
| `explanation` | `VARCHAR(1000)` | | LLM-generated explanation |
| `created_at` | `TIMESTAMP` | NOT NULL DEFAULT `NOW()` | |

**TimescaleDB Configuration**:
```sql
SELECT create_hypertable('signals', 'ts');
```

**Indexes**:
- `idx_signals_symbol_ts` ON `(symbol, ts DESC)`
- `idx_signals_active` ON `(is_active)` WHERE `is_active = TRUE`

---

### 4.4 `news_sentiment`

Financial news articles with sentiment analysis.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK (composite) | Article ID |
| `published_at` | `TIMESTAMPTZ` | PK (composite) | Publication time |
| `symbol` | `VARCHAR(32)` | NOT NULL | Mapped security symbol |
| `headline` | `TEXT` | NOT NULL | Article headline |
| `summary` | `TEXT` | | Article summary |
| `source` | `VARCHAR(64)` | NOT NULL | News source (e.g., `google_news`) |
| `url` | `TEXT` | | Article URL |
| `sentiment` | `VARCHAR(8)` | NOT NULL | `positive` \| `negative` \| `neutral` |
| `score` | `REAL` | NOT NULL | Positive-class probability [0-1] |
| `confidence` | `REAL` | NOT NULL | Max-class probability [0-1] |
| `dedup_hash` | `VARCHAR(64)` | UNIQUE | SHA-256 for deduplication |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | |

**TimescaleDB Configuration**:
```sql
SELECT create_hypertable(
    'news_sentiment', 'published_at',
    chunk_time_interval => INTERVAL '1 day'
);
```

**Indexes**:
- `ix_news_sentiment_symbol_ts` ON `(symbol, published_at DESC)`
- `ix_news_sentiment_source` ON `(source, published_at DESC)`
- `ix_news_sentiment_url` ON `(url, published_at DESC)`
- `uq_news_sentiment_dedup_hash` ON `(dedup_hash)` — UNIQUE

---

### 4.5 `model_predictions`

Raw model predictions for analysis and debugging.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK (composite) | Prediction ID |
| `ts` | `TIMESTAMPTZ` | PK (composite) | Prediction time |
| `symbol` | `VARCHAR(32)` | NOT NULL | Security symbol |
| `model_id` | `UUID` | FK → `ml_models.id` CASCADE | Model reference |
| `direction` | `VARCHAR(8)` | NOT NULL | `up` \| `down` |
| `probability` | `REAL` | NOT NULL | Predicted probability |
| `sentiment_score` | `REAL` | | News sentiment input |
| `features_used` | `JSONB` | NOT NULL DEFAULT `'{}'` | Input feature values |

**TimescaleDB Configuration**:
```sql
SELECT create_hypertable(
    'model_predictions', 'ts',
    chunk_time_interval => INTERVAL '7 days'
);
```

**Indexes**:
- `ix_model_predictions_symbol_ts` ON `(symbol, ts DESC)`

---

## 5. Indexes

### Complete Index Reference

| Table | Index Name | Columns | Type |
|-------|------------|---------|------|
| `users` | `idx_users_email` | `(email)` | B-tree |
| `user_invites` | `idx_user_invites_email` | `(email)` | B-tree |
| `refresh_tokens` | `idx_refresh_tokens_user_id` | `(user_id)` | B-tree |
| `refresh_tokens` | `idx_refresh_tokens_jti` | `(jti)` | B-tree |
| `broker_credentials` | `idx_broker_creds_user` | `(user_id)` | B-tree |
| `stock_universe` | `idx_universe_sector` | `(sector)` | B-tree |
| `stock_universe` | `idx_universe_market_cap` | `(market_cap DESC)` | B-tree |
| `stock_universe` | `idx_universe_active` | `(is_active)` | Partial (WHERE is_active) |
| `ohlcv_daily` | `idx_ohlcv_daily_symbol_ts` | `(symbol, ts DESC)` | B-tree |
| `ohlcv_1min` | `idx_ohlcv_1min_symbol_ts` | `(symbol, ts DESC)` | B-tree |
| `signals` | `idx_signals_symbol_ts` | `(symbol, ts DESC)` | B-tree |
| `signals` | `idx_signals_active` | `(is_active)` | Partial (WHERE is_active) |
| `news_sentiment` | `ix_news_sentiment_symbol_ts` | `(symbol, published_at DESC)` | B-tree |
| `news_sentiment` | `ix_news_sentiment_source` | `(source, published_at DESC)` | B-tree |
| `news_sentiment` | `ix_news_sentiment_url` | `(url, published_at DESC)` | B-tree |
| `paper_trades` | `ix_paper_trades_user_id` | `(user_id)` | B-tree |
| `paper_trades` | `ix_paper_trades_symbol` | `(symbol)` | B-tree |
| `paper_trades` | `ix_paper_trades_status` | `(user_id, status)` | B-tree |
| `paper_trades` | `ix_paper_trades_entry_at` | `(entry_at DESC)` | B-tree |
| `live_orders` | `ix_live_orders_user_id` | `(user_id, placed_at DESC)` | B-tree |
| `live_orders` | `idx_live_orders_broker_oid` | `(broker_order_id)` | B-tree |
| `ml_models` | `ix_ml_models_type_active` | `(model_type, is_active)` | B-tree |
| `ml_models` | `ix_ml_models_trained_at` | `(trained_at DESC)` | B-tree |
| `model_predictions` | `ix_model_predictions_symbol_ts` | `(symbol, ts DESC)` | B-tree |
| `expo_push_tokens` | `uniq_expo_token_active` | `(token)` | Unique Partial |
| `expo_push_tokens` | `uniq_expo_device_active` | `(device_id)` | Unique Partial |
| `expo_push_tokens` | `idx_expo_tokens_user` | `(user_id)` | Partial (WHERE is_active) |

---

## 6. Triggers & Functions

### `fn_set_tbl_last_dt()`

Automatically updates `tbl_last_dt` column on every UPDATE.

```sql
CREATE OR REPLACE FUNCTION fn_set_tbl_last_dt()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.tbl_last_dt = NOW();
    RETURN NEW;
END;
$$;
```

### Applied Triggers

| Table | Trigger Name | Event |
|-------|--------------|-------|
| `users` | `trg_users_tbl_last_dt` | BEFORE UPDATE |
| `user_settings` | `trg_user_settings_tbl_last_dt` | BEFORE UPDATE |
| `user_invites` | `trg_user_invites_tbl_last_dt` | BEFORE UPDATE |
| `refresh_tokens` | `trg_refresh_tokens_tbl_last_dt` | BEFORE UPDATE |
| `broker_credentials` | `trg_broker_creds_tbl_last_dt` | BEFORE UPDATE |
| `stock_universe` | `trg_universe_tbl_last_dt` | BEFORE UPDATE |

---

## 7. Redis Keys

Redis is used for caching and rate limiting. Key patterns:

| Pattern | TTL | Purpose |
|---------|-----|---------|
| `blocklist:{jti}` | 7 days | Revoked JWT access tokens |
| `broker:session:{user_id}:{broker}` | 23 hours | Cached broker JWT sessions |
| `shared:quote:{symbol}` | 60 seconds | Shared quote cache for WebSocket |
| `screener_quotes:{broker}:{symbols_hash}` | 30 seconds | Screener batch quote cache |
| `news:sentiment:{symbol}` | 5 minutes | Symbol sentiment cache |
| `user:settings:{user_id}` | 5 minutes | User settings cache |
| `idem:paper:{user_id}:{key}` | 5 minutes | Paper trade idempotency |
| `live_enable_otp:{user_id}` | 10 minutes | Live trading OTP |
| `live_enable_attempts:{user_id}` | 15 minutes | OTP attempt counter |
| `backfill:progress` | None | Backfill progress percentage |
| `pool:degraded:{credential_id}` | 5 minutes | Degraded credential cooldown |
| `pipeline_task_status:{task}` | None | Redundant task status (DB is source of truth) |

---

## 8. Maintenance

### Compression Management

View compression status:
```sql
SELECT 
    hypertable_name,
    chunk_name,
    compressed_total_bytes,
    uncompressed_total_bytes
FROM timescaledb_information.compressed_chunk_stats
ORDER BY hypertable_name, chunk_name;
```

Manual compression:
```sql
SELECT compress_chunk(c.chunk_schema || '.' || c.chunk_name)
FROM timescaledb_information.chunks c
WHERE c.hypertable_name = 'ohlcv_daily'
  AND NOT c.is_compressed
  AND c.range_end < NOW() - INTERVAL '90 days';
```

### Data Retention

Add retention policy (if needed):
```sql
-- Delete ohlcv_1min data older than 90 days
SELECT add_retention_policy('ohlcv_1min', INTERVAL '90 days');

-- Delete news_sentiment older than 180 days
SELECT add_retention_policy('news_sentiment', INTERVAL '180 days');
```

### Useful Queries

**Check table sizes**:
```sql
SELECT
    relname AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 10;
```

**Check hypertable statistics**:
```sql
SELECT 
    hypertable_name,
    num_chunks,
    compressed_chunk_count,
    pg_size_pretty(before_compression_total_bytes) AS before,
    pg_size_pretty(after_compression_total_bytes) AS after
FROM timescaledb_information.hypertable_compression_stats;
```

**Find missing OHLCV data**:
```sql
WITH date_series AS (
    SELECT generate_series(
        CURRENT_DATE - INTERVAL '30 days',
        CURRENT_DATE - INTERVAL '1 day',
        '1 day'
    )::date AS trading_date
),
symbols AS (
    SELECT symbol FROM stock_universe WHERE is_active LIMIT 10
)
SELECT d.trading_date, s.symbol
FROM date_series d
CROSS JOIN symbols s
WHERE EXTRACT(DOW FROM d.trading_date) BETWEEN 1 AND 5
  AND NOT EXISTS (
    SELECT 1 FROM ohlcv_daily o
    WHERE o.symbol = s.symbol AND o.ts::date = d.trading_date
);
```

### Backup & Recovery

**Backup with pg_dump**:
```bash
docker compose exec postgres pg_dump -U aitrader -d aitrader -Fc > backup.dump
```

**Restore**:
```bash
cat backup.dump | docker compose exec -i postgres pg_restore -U aitrader -d aitrader
```

---

## Appendix: Alembic Migrations

| Revision | Description |
|----------|-------------|
| `0001` | Initial schema — all tables, hypertables, indexes, triggers |
| `0002` | Add audit columns (created_at, updated_at, tbl_last_dt) |
| `0003` | Add explanation column to signals |
| `0004` | Add dedup_hash to news_sentiment, live_orders broker_order_id index |
| `0005` | Phase 8: pool_eligible flag, expo_push_tokens table |

Run migrations:
```bash
docker compose exec backend alembic upgrade head
```

Check current revision:
```bash
docker compose exec backend alembic current
```
