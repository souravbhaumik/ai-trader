# AI Trader — Database Schema & Relationships

> PostgreSQL 16 + TimescaleDB 2  
> All UUIDs use `gen_random_uuid()`. All timestamps are `TIMESTAMPTZ` (UTC stored, IST displayed in UI).

---

## Table of Contents

1. [Entity Relationship Overview](#1-entity-relationship-overview)
2. [Relational Tables](#2-relational-tables)
   - 2.1 [users](#21-users)
   - 2.2 [user_settings](#22-user_settings)
   - 2.3 [user_broker_config](#23-user_broker_config)
   - 2.4 [tickers](#24-tickers)
   - 2.5 [portfolio](#25-portfolio)
   - 2.6 [orders](#26-orders)
   - 2.7 [trades](#27-trades)
   - 2.8 [watchlist](#28-watchlist)
   - 2.9 [model_runs](#29-model_runs)
   - 2.10 [ensemble_config](#210-ensemble_config)
   - 2.11 [expo_push_tokens](#211-expo_push_tokens)
   - 2.12 [pipeline_task_status](#212-pipeline_task_status)
3. [TimescaleDB Hypertables](#3-timescaledb-hypertables)
   - 3.1 [price_1min](#31-price_1min)
   - 3.2 [price_1day](#32-price_1day)
   - 3.3 [corporate_actions](#33-corporate_actions)
   - 3.4 [news_sentiment](#34-news_sentiment)
   - 3.5 [signals](#35-signals)
4. [Relationship Summary](#4-relationship-summary)
5. [Indexes](#5-indexes)
6. [Constraints & Enums](#6-constraints--enums)
7. [TBL_LAST_DT — Auto-Update Trigger](#7-tbl_last_dt--auto-update-trigger)

---

## 1. Entity Relationship Overview

```
users ──────────────────────────────────────────────────────────┐
  │  (1:1)  user_settings                                        │
  │  (1:N)  user_broker_config                                   │
  │  (1:N)  portfolio  ──────── (N:1) tickers                   │
  │  (1:N)  orders     ──────── (N:1) tickers                   │
  │    │                                                          │
  │    └─── (1:N) trades                                         │
  │  (1:N)  watchlist  ──────── (N:1) tickers                   │
  │                                                               │
  └── (1:N) ensemble_config.updated_by  [admin only]            │
                                                                  │
tickers ─────────────────────────────────────────────────────────┘
  │  (1:N)  price_1min          [hypertable]
  │  (1:N)  price_1day          [hypertable]
  │  (1:N)  corporate_actions   [hypertable]
  │  (1:N)  news_sentiment      [hypertable]
  └─ (1:N)  signals             [hypertable]

model_runs     — standalone (mirrors MLflow, no FK to users)
ensemble_config — single-row config table, FK to users (updated_by)
expo_push_tokens — mobile push token lifecycle, FK to users
```

---

## 2. Relational Tables

### 2.1 `users`

Primary identity table. Every other user-scoped table FK references this.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK, DEFAULT `gen_random_uuid()` | Surrogate key |
| `email` | `VARCHAR(255)` | UNIQUE NOT NULL | Login credential |
| `hashed_password` | `VARCHAR(255)` | NOT NULL | bcrypt (cost 12) |
| `full_name` | `VARCHAR(100)` | | Display name |
| `role` | `VARCHAR(20)` | NOT NULL DEFAULT `'trader'` | `admin` \| `trader` \| `viewer` |
| `is_active` | `BOOL` | NOT NULL DEFAULT `true` | Deactivated users cannot log in |
| `is_email_verified` | `BOOL` | NOT NULL DEFAULT `false` | Must be `true` before trading |
| `is_live_trading_enabled` | `BOOL` | NOT NULL DEFAULT `false` | Set via email OTP; admin can override |
| `totp_secret` | `TEXT` | | Fernet-encrypted TOTP secret; NOT NULL enforced at app layer for `admin` role |
| `is_totp_configured` | `BOOL` | NOT NULL DEFAULT `false` | `true` after user completes TOTP setup |
| `is_totp_verified` | `BOOL` | NOT NULL DEFAULT `false` | Session-level flag; reset on logout |
| `invite_token_hash` | `VARCHAR(255)` | | **Deprecated — kept for backward compat only; new invites use `user_invites` table.** SHA256 of registration token; cleared after activation. |
| `invited_by` | `UUID` | FK → `users.id` SET NULL | Which admin created this invite |
| `last_login_at` | `TIMESTAMPTZ` | | Updated on every successful login |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

**Relationships:**
- `users` → `user_settings` : **one-to-one** (row created automatically on user activation)
- `users` → `user_broker_config` : **one-to-many** (one row per broker connected)
- `users` → `portfolio` : **one-to-many**
- `users` → `orders` : **one-to-many**
- `users` → `trades` : **one-to-many**
- `users` → `watchlist` : **one-to-many**
- `users` → `user_invites` : **one-to-many** (as invitee — normally one pending invite at a time)
- `users` → `users` (self) : `invited_by` self-referencing FK (admin who created the invite)

---

### 2.1a `user_invites`

Dedicated invite state table. Extracted from `users` to cleanly support multiple invite attempts per email (re-invite after expiry/revoke) and full invite lifecycle tracking without dirtying the `users` row.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `email` | `VARCHAR(255)` | NOT NULL | Email address being invited (may not yet have a `users` row if not yet activated) |
| `token_hash` | `VARCHAR(255)` | NOT NULL | SHA256 of the magic-link token (never stored in plaintext) |
| `status` | `VARCHAR(20)` | NOT NULL DEFAULT `'pending'` | `pending` \| `used` \| `expired` \| `revoked` |
| `invited_by` | `UUID` | NOT NULL, FK → `users.id` | Admin who generated this invite |
| `user_id` | `UUID` | FK → `users.id` SET NULL | Populated when the invite is accepted and the user row is created |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL | `created_at + 24 hours`; checked on every registration attempt |
| `used_at` | `TIMESTAMPTZ` | | Set when the user clicks the link and activates the account |
| `revoked_at` | `TIMESTAMPTZ` | | Set by admin on explicit revoke |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | Auto-updated by `fn_set_tbl_last_dt` trigger |

```sql
CREATE INDEX idx_invites_email_status ON user_invites (email, status);
CREATE INDEX idx_invites_token_hash ON user_invites (token_hash);  -- lookup on link click

CREATE TRIGGER trg_user_invites_tbl_last_dt
    BEFORE UPDATE ON user_invites
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();
```

**State transitions:**
```
pending  →  used     (user activates account before expiry)
pending  →  expired  (set by Celery cleanup task or checked on link click)
pending  →  revoked  (admin clicks Revoke in admin panel)
revoked  →  pending  (admin re-invites: new row inserted, NOT updated)
```

Each re-invite creates a **new row** rather than updating the old one. This preserves the full audit trail. The admin invite list view queries `user_invites` directly.

**Relationship:** `user_invites.invited_by` → `users.id` (MANY-TO-ONE); `user_invites.user_id` → `users.id` (ONE-TO-ONE once used, nullable until then)

---

### 2.1b `refresh_tokens`

Dedicated table for 7-day refresh token state. JWTs are stateless by design but refresh tokens must be revocable (logout, deactivation, admin force-logout). Storing hashes here — not on the `users` row — allows one user to hold multiple concurrent sessions (e.g., phone + desktop) and revoke them individually.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK, DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` ON DELETE CASCADE | Owner of this session |
| `token_hash` | `VARCHAR(255)` | UNIQUE NOT NULL | SHA256 of the raw refresh token string (never stored in plaintext) |
| `jti` | `UUID` | UNIQUE NOT NULL | JWT ID — matches the `jti` claim in the issued token; used to cross-reference the Redis `jti` blocklist |
| `issued_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL | `issued_at + 7 days`; checked on every `/auth/refresh` call |
| `revoked_at` | `TIMESTAMPTZ` | | Set on logout, forced revocation, or password change |
| `user_agent` | `TEXT` | | Browser/client string for session list display in admin panel |
| `ip_address` | `INET` | | IP at time of issue (for audit trail) |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | Auto-updated by `fn_set_tbl_last_dt` trigger |

```sql
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens (user_id);
CREATE INDEX idx_refresh_tokens_jti ON refresh_tokens (jti);

CREATE TRIGGER trg_refresh_tokens_tbl_last_dt
    BEFORE UPDATE ON refresh_tokens
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- Cleanup: Celery task deletes expired/revoked rows older than 30 days
-- DELETE FROM refresh_tokens WHERE expires_at < now() - INTERVAL '30 days';
```

**Token lifecycle on `/auth/refresh`:**
```
1. Client sends refresh token in httpOnly cookie
2. SHA256(cookie_value) → look up token_hash in refresh_tokens
3. Check: row exists AND revoked_at IS NULL AND expires_at > now()
4. Check: jti NOT IN Redis blocklist (belt-and-suspenders)
5. Issue new 15-min access JWT → return to client
   (refresh token is NOT rotated on every use — rotation optional)
6. On logout: SET revoked_at = now(); add jti to Redis blocklist (EX = remaining TTL)
```

**Revocation cascade:** when an admin deactivates a user, all `refresh_tokens` rows for that `user_id` are revoked in a single `UPDATE` statement; corresponding `jti` values are pushed to the Redis blocklist in a pipeline.

**Relationship:** `refresh_tokens.user_id` → `users.id` (MANY-TO-ONE, CASCADE DELETE)

---

### 2.2 `user_settings`

One-to-one extension of `users`. Holds per-user trading preferences. Row is created with defaults on user activation.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `user_id` | `UUID` | PK, FK → `users.id` ON DELETE CASCADE | Shared PK enforces 1:1 |
| `trading_mode` | `VARCHAR(10)` | NOT NULL DEFAULT `'paper'` | `paper` \| `live` |
| `paper_balance` | `DECIMAL(15,2)` | NOT NULL DEFAULT `1000000.00` | Virtual cash balance (₹) |
| `max_position_pct` | `DECIMAL(5,2)` | NOT NULL DEFAULT `10.00` | Max % of portfolio per single position |
| `daily_loss_limit_pct` | `DECIMAL(5,2)` | NOT NULL DEFAULT `5.00` | Auto-halt trading when daily loss exceeds this |
| `notification_signals` | `BOOL` | NOT NULL DEFAULT `true` | Push/Discord alert on new signal |
| `notification_orders` | `BOOL` | NOT NULL DEFAULT `true` | Push alert on order status change |
| `preferred_broker` | `VARCHAR(20)` | DEFAULT `NULL` | `angel_one` \| `upstox` \| `nse_fallback` |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

**Relationship:** `user_settings.user_id` → `users.id` (ONE-TO-ONE, CASCADE DELETE)

---

### 2.3 `user_broker_config`

One user can connect multiple brokers (Angel One + Upstox simultaneously). Only one row per user can have `is_primary = true`.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` ON DELETE CASCADE | |
| `broker` | `VARCHAR(20)` | NOT NULL | `angel_one` \| `upstox` \| `nse_fallback` |
| `is_primary` | `BOOL` | NOT NULL DEFAULT `false` | Enforced: at most one `true` per `user_id` via partial unique index |
| `encrypted_api_key` | `TEXT` | | Fernet-encrypted; NULL for `nse_fallback` |
| `encrypted_api_secret` | `TEXT` | | Fernet-encrypted; NULL for `nse_fallback` |
| `encrypted_totp_key` | `TEXT` | | Angel One requires TOTP for session refresh; stored encrypted |
| `access_token` | `TEXT` | | Short-lived bearer token (refreshed daily by Celery task) |
| `token_expires_at` | `TIMESTAMPTZ` | | |
| `last_connected_at` | `TIMESTAMPTZ` | | Last successful API authentication |
| `connection_status` | `VARCHAR(20)` | DEFAULT `'not_configured'` | `connected` \| `error` \| `expired` \| `not_configured` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

**Constraints:**
```sql
UNIQUE (user_id, broker)
-- Partial unique index to enforce single primary:
CREATE UNIQUE INDEX uniq_primary_broker ON user_broker_config (user_id)
  WHERE is_primary = true;
```

**Relationship:** `user_broker_config.user_id` → `users.id` (MANY-TO-ONE)

---

### 2.4 `tickers`

Master ticker universe. All price, signal, and news tables reference `symbol` from this table.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(20)` | PK | NSE symbol e.g. `RELIANCE`, `TCS` |
| `name` | `VARCHAR(200)` | NOT NULL | Company full name |
| `sector` | `VARCHAR(100)` | | GICS sector |
| `industry` | `VARCHAR(100)` | | GICS industry |
| `isin` | `VARCHAR(12)` | UNIQUE | ISIN code (used for corporate action matching) |
| `exchange` | `VARCHAR(10)` | NOT NULL DEFAULT `'NSE'` | `NSE` \| `BSE` |
| `nse_token` | `VARCHAR(20)` | | Angel One / Upstox instrument token (for WebSocket subscription) |
| `lot_size` | `INTEGER` | NOT NULL DEFAULT `1` | NSE lot size (relevant for F&O later) |
| `is_active` | `BOOL` | NOT NULL DEFAULT `true` | Inactive tickers excluded from signal runs |
| `is_in_nifty50` | `BOOL` | NOT NULL DEFAULT `false` | Used for macro feature engineering |
| `added_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

**Relationships:**
- `tickers.symbol` ← FK referenced by: `portfolio`, `orders`, `trades`, `watchlist`, `price_1min`, `price_1day`, `corporate_actions`, `news_sentiment`, `signals`

---

### 2.5 `portfolio`

Current holdings per user per trading mode. Updated on every fill. One row per (user, symbol, mode) — the `UNIQUE` constraint enforces this.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` ON DELETE CASCADE | |
| `symbol` | `VARCHAR(20)` | NOT NULL, FK → `tickers.symbol` | |
| `quantity` | `INTEGER` | NOT NULL CHECK (quantity >= 0) | 0 = position closed (row kept for history) |
| `avg_buy_price` | `DECIMAL(12,2)` | NOT NULL | Weighted average cost basis |
| `trading_mode` | `VARCHAR(10)` | NOT NULL | `paper` \| `live` |
| `last_updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

```sql
UNIQUE (user_id, symbol, trading_mode)
```

**Relationships:**
- `portfolio.user_id` → `users.id` (MANY-TO-ONE)
- `portfolio.symbol` → `tickers.symbol` (MANY-TO-ONE)

---

### 2.6 `orders`

Every order attempt, paper or live. Broker fills update this row via webhook postback.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | Internal order ID |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` | |
| `symbol` | `VARCHAR(20)` | NOT NULL, FK → `tickers.symbol` | |
| `trading_mode` | `VARCHAR(10)` | NOT NULL | `paper` \| `live` |
| `broker` | `VARCHAR(20)` | | `angel_one` \| `upstox` \| `paper_engine` |
| `broker_order_id` | `VARCHAR(50)` | | Returned by broker; NULL for paper orders |
| `idempotency_key` | `UUID` | UNIQUE WHERE NOT NULL | Client-supplied UUID for duplicate prevention |
| `order_type` | `VARCHAR(10)` | NOT NULL | `MARKET` \| `LIMIT` \| `SL` \| `SL-M` |
| `transaction_type` | `VARCHAR(5)` | NOT NULL | `BUY` \| `SELL` |
| `quantity` | `INTEGER` | NOT NULL CHECK (quantity > 0) | Requested quantity |
| `price` | `DECIMAL(12,2)` | | NULL for MARKET orders |
| `trigger_price` | `DECIMAL(12,2)` | | For SL / SL-M orders |
| `status` | `VARCHAR(20)` | NOT NULL DEFAULT `'PENDING'` | `PENDING` \| `OPEN` \| `COMPLETE` \| `REJECTED` \| `CANCELLED` |
| `rejection_reason` | `VARCHAR(50)` | | `PAPER_ORDER_TOO_LARGE` \| `ORDERBOOK_STALE` \| broker error code |
| `filled_quantity` | `INTEGER` | NOT NULL DEFAULT `0` | |
| `avg_fill_price` | `DECIMAL(12,2)` | | |
| `signal_id` | `(symbol, timestamp)` | soft FK → `signals (symbol, timestamp)` | Which signal triggered this order (nullable — manual orders have no signal). **Not enforced at DB level** (signals is a hypertable; TimescaleDB does not support FK constraints on partitioned tables). Enforced at application layer: `order_service.py` validates signal existence on create; the signals purge path in `maintenance_tasks.py` must call `_check_no_referencing_orders(symbol, timestamp)` and raise before deleting any signal row that has referenced orders. |
| `placed_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

**Relationships:**
- `orders.user_id` → `users.id` (MANY-TO-ONE)
- `orders.symbol` → `tickers.symbol` (MANY-TO-ONE)
- `orders` → `trades` (ONE-TO-MANY — a single order can produce multiple partial fills)
- `orders.signal_id` → `signals (symbol, timestamp)` (MANY-TO-ONE, nullable)

---

### 2.7 `trades`

Immutable record of every fill event. Never updated, only inserted.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `order_id` | `UUID` | NOT NULL, FK → `orders.id` | Parent order |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` | Denormalized for fast per-user queries |
| `symbol` | `VARCHAR(20)` | NOT NULL | Denormalized for fast per-symbol queries |
| `transaction_type` | `VARCHAR(5)` | NOT NULL | `BUY` \| `SELL` |
| `quantity` | `INTEGER` | NOT NULL CHECK (quantity > 0) | Fill quantity (may be partial) |
| `price` | `DECIMAL(12,2)` | NOT NULL | Actual fill price |
| `brokerage` | `DECIMAL(10,4)` | NOT NULL DEFAULT `0` | Simulated (paper: 0.03%) or actual (live: from broker) |
| `remaining_qty` | `INTEGER` | NOT NULL | For BUY trades: quantity not yet matched by a subsequent SELL (for FIFO lot tracking). Set to `quantity` on insert; decremented by FIFO P&L calculator on each sell. For SELL trades: always `0`. |
| `pnl` | `DECIMAL(12,2)` | | Realized P&L for SELL trades. **Must be calculated using FIFO lot matching** — see note below. |
| `trading_mode` | `VARCHAR(10)` | NOT NULL | `paper` \| `live` |
| `traded_at` | `TIMESTAMPTZ` | NOT NULL | Broker fill timestamp |

> **FIFO P&L rule (Indian equity mandate):** SEBI requires First-In-First-Out cost matching for equity realized P&L. `portfolio.avg_buy_price` must NOT be used for P&L calculation on partial sells — it is a blended average that loses per-lot cost information after multiple buy tranches.
>
> Correct approach in `pnl_service.calculate_realized_pnl(user_id, symbol, sell_qty, sell_price, trading_mode)`:
> ```
> 1. Query trades WHERE user_id = ? AND symbol = ? AND transaction_type = 'BUY'
>    AND trading_mode = ? ORDER BY traded_at ASC   ← oldest lot first
> 2. Walk through BUY lots, consuming sell_qty from oldest to newest:
>    for each lot: matched_qty = min(lot.remaining_qty, remaining_sell_qty)
>                  lot_pnl = matched_qty × (sell_price − lot.price)
> 3. Sum all lot_pnl values → total realized P&L for this sell
> ```
> Each BUY trade row must carry a `remaining_qty` field so partial consumption across multiple sells can be tracked (see column added below).

**Relationships:**
- `trades.order_id` → `orders.id` (MANY-TO-ONE)
- `trades.user_id` → `users.id` (MANY-TO-ONE, denormalized)

---

### 2.8 `watchlist`

Per-user ticker watchlist with optional price alert threshold.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` ON DELETE CASCADE | |
| `symbol` | `VARCHAR(20)` | NOT NULL, FK → `tickers.symbol` | |
| `alert_price` | `DECIMAL(12,2)` | | If set, notification fires when LTP crosses this |
| `alert_direction` | `VARCHAR(5)` | | `ABOVE` \| `BELOW` — direction of the alert |
| `alert_fired_at` | `TIMESTAMPTZ` | | Set when alert fires; cleared when user resets |
| `added_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

```sql
UNIQUE (user_id, symbol)
```

**Relationships:**
- `watchlist.user_id` → `users.id` (MANY-TO-ONE)
- `watchlist.symbol` → `tickers.symbol` (MANY-TO-ONE)

---

### 2.9 `model_runs`

Mirrors MLflow for fast admin UI access without hitting the MLflow server on every page load.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK | |
| `mlflow_run_id` | `VARCHAR(50)` | UNIQUE NOT NULL | MLflow run identifier |
| `model_name` | `VARCHAR(50)` | NOT NULL | `lightgbm` \| `tft` \| `lstm_autoencoder` |
| `version` | `INTEGER` | NOT NULL | MLflow model version number |
| `stage` | `VARCHAR(20)` | NOT NULL | `staging` \| `production` \| `archived` |
| `accuracy` | `DECIMAL(6,4)` | | Directional accuracy on test set |
| `precision_score` | `DECIMAL(6,4)` | | Precision (BUY class) |
| `recall_score` | `DECIMAL(6,4)` | | Recall (BUY class) |
| `f1_score` | `DECIMAL(6,4)` | | |
| `sharpe_ratio` | `DECIMAL(8,4)` | | Simulated Sharpe on test period |
| `train_start_date` | `DATE` | | Training data window start |
| `train_end_date` | `DATE` | | Training data window end |
| `trained_by` | `UUID` | FK → `users.id` SET NULL | Which admin triggered the run; NULL = scheduled |
| `metrics_json` | `JSONB` | | Full MLflow metrics dict for detailed admin view |
| `trained_at` | `TIMESTAMPTZ` | NOT NULL | |

**Relationships:**
- `model_runs.trained_by` → `users.id` (MANY-TO-ONE, nullable)
- No FK to other tables — standalone audit record

---

### 2.10 `ensemble_config`

Single-row config table (enforced by `id = 1` PK). Weights are editable from the admin panel; changes take effect on next `generate_signals` run.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `INTEGER` | PK DEFAULT `1` CHECK (id = 1) | Singleton row |
| `lgbm_weight` | `DECIMAL(4,3)` | NOT NULL DEFAULT `0.350` | Must sum to 1.0 with other weights (enforced at app layer) |
| `tft_weight` | `DECIMAL(4,3)` | NOT NULL DEFAULT `0.350` | |
| `finbert_weight` | `DECIMAL(4,3)` | NOT NULL DEFAULT `0.250` | |
| `anomaly_weight` | `DECIMAL(4,3)` | NOT NULL DEFAULT `0.050` | |
| `buy_threshold` | `DECIMAL(4,3)` | NOT NULL DEFAULT `0.600` | Score above this → BUY |
| `sell_threshold` | `DECIMAL(4,3)` | NOT NULL DEFAULT `0.400` | Score below this → SELL |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `updated_by` | `UUID` | FK → `users.id` SET NULL | Admin who last changed weights |

---

### 2.11 `expo_push_tokens`

Device-scoped Expo Push Notification tokens. One user may have multiple active tokens (phone + tablet). Tokens rotate on app reinstall or OS token refresh — the `device_id` column is the dedup key.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | PK, DEFAULT `gen_random_uuid()` | |
| `user_id` | `UUID` | NOT NULL, FK → `users.id` ON DELETE CASCADE | |
| `token` | `VARCHAR(200)` | NOT NULL | Expo push token: `ExponentPushToken[...]` |
| `device_id` | `VARCHAR(100)` | | Expo `deviceId` — used for UPSERT dedup |
| `platform` | `VARCHAR(10)` | NOT NULL | `ios` \| `android` |
| `is_active` | `BOOL` | NOT NULL DEFAULT `true` | Set to `false` when `DeviceNotRegistered` error returned by Expo |
| `registered_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |
| `last_used_at` | `TIMESTAMPTZ` | | Updated on each successful delivery attempt |
| `tbl_last_dt` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | Auto-updated by `fn_set_tbl_last_dt` trigger |

```sql
-- Only one active token per Expo device ID
CREATE UNIQUE INDEX uniq_expo_token_active ON expo_push_tokens (token)
    WHERE is_active = true;
CREATE INDEX idx_expo_tokens_user ON expo_push_tokens (user_id)
    WHERE is_active = true;
CREATE INDEX idx_expo_tokens_device ON expo_push_tokens (device_id);
```

**UPSERT pattern on token registration:**
```sql
INSERT INTO expo_push_tokens (user_id, token, device_id, platform)
    VALUES (:user_id, :token, :device_id, :platform)
ON CONFLICT (device_id) DO UPDATE
    SET token        = EXCLUDED.token,
        is_active    = true,
        registered_at = now();
-- Then mark any previous token rows for this device_id that were NOT just upserted as inactive:
UPDATE expo_push_tokens
    SET is_active = false
    WHERE device_id = :device_id
      AND token != :token
      AND is_active = true;
```

**Delivery failure handling:** When Expo's push service returns `DeviceNotRegistered`, `notification_service.py` sets `is_active = false` on the matching token row. No retry.

**Relationships:**
- `expo_push_tokens.user_id` → `users.id` (MANY-TO-ONE, CASCADE DELETE)

---

### 2.12 `pipeline_task_status`

One row per Celery pipeline task. Upserted on every status transition — replaces the previous Redis-based status keys which were prone to stale `running` state after worker crashes.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `task_name` | `VARCHAR(100)` | PK | Unique task identifier (e.g. `broker_backfill`, `eod_ingest`) |
| `status` | `VARCHAR(20)` | NOT NULL DEFAULT `'idle'` | `idle` \| `running` \| `done` \| `error` \| `unknown` |
| `message` | `TEXT` | NOT NULL DEFAULT `'Never run.'` | Latest human-readable status message |
| `started_at` | `TIMESTAMPTZ` | | When the current/last run started |
| `finished_at` | `TIMESTAMPTZ` | | When the current/last run finished |
| `summary` | `JSONB` | NOT NULL DEFAULT `'{}'` | Task-specific metrics (rows inserted, symbols processed, etc.) |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `NOW()` | Refreshed on every upsert — acts as a heartbeat for running tasks |

**Status lifecycle:**
```
idle → running  (task starts)
running → done  (task completes successfully)
running → error (task raises an exception)
running → unknown  (backend restarted while task was in-flight)
```

**Crash detection:** On every FastAPI backend startup, any row with `status = 'running'` is immediately updated to `status = 'unknown'` with the message *"Status unknown — application restarted while task was running."* This is the only persistent record of an interrupted run; no time-based heuristics are used.

**Task log lines** (ephemeral): Per-task structured log lines are stored in Redis as `pipeline:logs:{task_name}` lists (capped at 500 entries, 7-day TTL). These are separate from this table — they are volatile and only needed for live log viewing in the Admin panel.

```sql
-- Seeded at table creation; safe to re-run
INSERT INTO pipeline_task_status (task_name) VALUES
    ('universe_population'), ('broker_backfill'), ('bhavcopy'),
    ('backfill'), ('eod_ingest'), ('ml_training'),
    ('signal_generator'), ('news_sentiment')
ON CONFLICT (task_name) DO NOTHING;
```

**No relationships** — this table is standalone. It is created by `db_init/02_pipeline_task_status.sql` (not Alembic, since it is infrastructure-level, not application-level).

---

## 3. TimescaleDB Hypertables

All hypertables partition on `timestamp` (or `date`). Relational FKs to `tickers.symbol` are **not** enforced at DB level on hypertables (TimescaleDB limitation with partitioned tables) — referential integrity is enforced at the application layer.

---

### 3.1 `price_1min`

Intraday 1-minute OHLCV bars built from raw broker WebSocket ticks.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(20)` | NOT NULL | References `tickers.symbol` (app-level) |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL | Bar open time (IST bar boundary, stored UTC) |
| `open` | `DECIMAL(12,2)` | | |
| `high` | `DECIMAL(12,2)` | | |
| `low` | `DECIMAL(12,2)` | | |
| `close` | `DECIMAL(12,2)` | | |
| `volume` | `BIGINT` | | |

```sql
PRIMARY KEY (symbol, timestamp)
SELECT create_hypertable('price_1min', 'timestamp',
  chunk_time_interval => INTERVAL '1 week');
-- Compress chunks older than 7 days
-- Retain chunks for 1 year; drop older
```

**Notes:**
- Never cross a split ex-date in a 1-min window for ML features (see corporate_actions rules)
- Raw tick data deleted after 7 days by `cleanup_old_ticks` Celery task
- `price_1min` is for live timing signals only — **not** used as training data (use `price_1day.adj_close` for training)

---

### 3.2 `price_1day`

EOD OHLCV from NSE Bhavcopy. The canonical price series for all ML training and inference.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(20)` | NOT NULL | |
| `date` | `DATE` | NOT NULL | Trading date |
| `open` | `DECIMAL(12,2)` | | |
| `high` | `DECIMAL(12,2)` | | |
| `low` | `DECIMAL(12,2)` | | |
| `close` | `DECIMAL(12,2)` | | Unadjusted close |
| `adj_close` | `DECIMAL(12,2)` | | Split/bonus/dividend adjusted close — updated retroactively by `apply_corporate_actions` task |
| `is_adjusted` | `BOOL` | NOT NULL DEFAULT `false` | `true` once corporate action adjustment has been applied |
| `volume` | `BIGINT` | | |
| `delivery_pct` | `DECIMAL(5,2)` | | Delivery percentage from Bhavcopy |

```sql
PRIMARY KEY (symbol, date)
SELECT create_hypertable('price_1day', 'date',
  chunk_time_interval => INTERVAL '1 year');
```

---

### 3.3 `corporate_actions`

Split, bonus, and dividend events. Drives retroactive adjustment of `price_1day.adj_close`.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | DEFAULT `gen_random_uuid()` | |
| `symbol` | `VARCHAR(20)` | NOT NULL | |
| `ex_date` | `DATE` | NOT NULL | Date the action takes effect |
| `action_type` | `VARCHAR(20)` | NOT NULL | `SPLIT` \| `BONUS` \| `DIVIDEND` |
| `ratio` | `DECIMAL(10,5)` | | e.g. `2.00000` for 2:1 split |
| `dividend_amount` | `DECIMAL(12,4)` | | ₹ per share; for `DIVIDEND` type |
| `is_applied` | `BOOL` | NOT NULL DEFAULT `false` | `true` after historical prices retroactively adjusted |
| `source` | `VARCHAR(30)` | | `bhavcopy` \| `nse_rss` \| `manual` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` | |

```sql
PRIMARY KEY (id)
CREATE INDEX ON corporate_actions (symbol, ex_date);
```

---

### 3.4 `news_sentiment`

Every FinBERT-scored article headline. Source of rolling sentiment fed into the signal ensemble.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `UUID` | DEFAULT `gen_random_uuid()` | |
| `symbol` | `VARCHAR(20)` | NOT NULL | NSE ticker mentioned in the article |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL | When the article was fetched/scored |
| `source` | `VARCHAR(50)` | | `economic_times` \| `moneycontrol` \| `reddit` \| `stocktwits` \| etc. |
| `headline` | `TEXT` | NOT NULL | Article headline |
| `url` | `TEXT` | | Article URL |
| `content_hash` | `VARCHAR(64)` | | `SHA256(url + headline)` — used for deduplication check against Redis |
| `sentiment_score` | `DECIMAL(4,3)` | | FinBERT output: -1.0 (bearish) to +1.0 (bullish) |
| `finbert_model_version` | `VARCHAR(30)` | | Model version that scored this article |

```sql
PRIMARY KEY (symbol, timestamp, id)
SELECT create_hypertable('news_sentiment', 'timestamp',
  chunk_time_interval => INTERVAL '1 month');
-- Retain 90 days; older articles dropped
```

---

### 3.5 `signals`

Every generated ensemble signal with all component scores. The self-labelling feedback loop lives here — `actual_return_*` and `outcome_label` are backfilled by the nightly EOD job.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `symbol` | `VARCHAR(20)` | NOT NULL | |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL | When signal was generated |
| `signal` | `VARCHAR(5)` | NOT NULL | `BUY` \| `SELL` \| `HOLD` |
| `confidence` | `DECIMAL(4,3)` | | Final ensemble score (0.0 – 1.0) |
| `lgbm_score` | `DECIMAL(4,3)` | | LightGBM directional probability |
| `tft_score` | `DECIMAL(4,3)` | | TFT-derived score (normalised) |
| `finbert_score` | `DECIMAL(4,3)` | | Rolling 24h sentiment (-1 to +1 rescaled to 0–1) |
| `anomaly_score` | `DECIMAL(4,3)` | | LSTM AE anomaly score (0 = normal, 1 = extreme) |
| `features_snapshot` | `JSONB` | | All feature values at inference time — raw numerics AND discrete derived strings (e.g. `macd_cross_direction: "bullish_cross"`) |
| `model_version` | `JSONB` | | `{"lgbm": "v3", "tft": "v2", ...}` |
| `explanation` | `TEXT` | | LLM-generated human-readable summary (nullable; written async by `explain_signal` task) |
| `actual_return_1d` | `DECIMAL(8,4)` | | % price change on the next trading day; backfilled by EOD job |
| `actual_return_5d` | `DECIMAL(8,4)` | | % price change over 5 trading days |
| `outcome_label` | `VARCHAR(20)` | | `correct` \| `incorrect` \| `hold_neutral`; set by EOD job once `actual_return_1d` is known |

```sql
PRIMARY KEY (symbol, timestamp)
SELECT create_hypertable('signals', 'timestamp',
  chunk_time_interval => INTERVAL '1 month');
-- Retain indefinitely — this is training data for future retraining runs
```

**Cross-table reference (soft FK, not enforced at DB level):**
- `orders.signal_id` references `(signals.symbol, signals.timestamp)` — identifies which signal triggered a user order (nullable for manual orders)

---

## 4. Relationship Summary

| Relationship | Type | Parent → Child | On Delete |
|---|---|---|---|
| `users` → `user_settings` | 1:1 | `user_settings.user_id → users.id` | CASCADE |
| `users` → `user_broker_config` | 1:N | `user_broker_config.user_id → users.id` | CASCADE |
| `users` → `portfolio` | 1:N | `portfolio.user_id → users.id` | CASCADE |
| `users` → `orders` | 1:N | `orders.user_id → users.id` | RESTRICT |
| `users` → `trades` | 1:N | `trades.user_id → users.id` | RESTRICT |
| `users` → `watchlist` | 1:N | `watchlist.user_id → users.id` | CASCADE |
| `users` → `users` (self) | 1:N | `users.invited_by → users.id` | SET NULL |
| `users` → `expo_push_tokens` | 1:N | `expo_push_tokens.user_id → users.id` | CASCADE |
| `users` → `ensemble_config` | 1:1 (audit) | `ensemble_config.updated_by → users.id` | SET NULL |
| `users` → `model_runs` | 1:N (audit) | `model_runs.trained_by → users.id` | SET NULL |
| `tickers` → `portfolio` | 1:N | `portfolio.symbol → tickers.symbol` | RESTRICT |
| `tickers` → `orders` | 1:N | `orders.symbol → tickers.symbol` | RESTRICT |
| `tickers` → `watchlist` | 1:N | `watchlist.symbol → tickers.symbol` | RESTRICT |
| `orders` → `trades` | 1:N | `trades.order_id → orders.id` | RESTRICT |
| `signals` → `orders` | 1:N (soft) | `orders.signal_id → signals(symbol,ts)` | SET NULL (app-level) |
| `tickers` → `price_1min` | 1:N (app-level) | symbol reference | — |
| `tickers` → `price_1day` | 1:N (app-level) | symbol reference | — |
| `tickers` → `corporate_actions` | 1:N (app-level) | symbol reference | — |
| `tickers` → `news_sentiment` | 1:N (app-level) | symbol reference | — |
| `tickers` → `signals` | 1:N (app-level) | symbol reference | — |

> **Hypertable FK note:** TimescaleDB does not support FK constraints on partitioned hypertable columns as the referenced or referencing side. All `tickers.symbol` references from hypertables are enforced at the SQLModel/application layer, not at the DB constraint level.

---

## 5. Indexes

```sql
-- Auth & session hot path
CREATE INDEX idx_users_email ON users (email);
CREATE INDEX idx_users_role ON users (role) WHERE is_active = true;

-- Broker config lookup (on every request that needs broker adapter)
CREATE INDEX idx_broker_config_user ON user_broker_config (user_id, is_primary);

-- Portfolio fast lookup
CREATE INDEX idx_portfolio_user_mode ON portfolio (user_id, trading_mode);

-- Orders: per-user history + status filtering
CREATE INDEX idx_orders_user_status ON orders (user_id, status, placed_at DESC);
CREATE INDEX idx_orders_broker_id ON orders (broker_order_id) WHERE broker_order_id IS NOT NULL;

-- Trades: P&L queries
CREATE INDEX idx_trades_user_mode ON trades (user_id, trading_mode, traded_at DESC);

-- Watchlist alerts (checked every tick)
CREATE INDEX idx_watchlist_alert ON watchlist (symbol) WHERE alert_price IS NOT NULL;

-- Signals: most recent signal per symbol (screener + dashboard)
CREATE INDEX idx_signals_symbol_ts ON signals (symbol, timestamp DESC);
-- Signals: outcome backfill job (nightly)
CREATE INDEX idx_signals_unlabelled ON signals (timestamp) WHERE outcome_label IS NULL;

-- News: per-ticker recent sentiment
CREATE INDEX idx_news_symbol_ts ON news_sentiment (symbol, timestamp DESC);

-- Price history: training window queries
CREATE INDEX idx_price1day_symbol_date ON price_1day (symbol, date DESC);

-- Corporate actions: pending adjustments
CREATE INDEX idx_corp_actions_pending ON corporate_actions (symbol, ex_date)
  WHERE is_applied = false;
```

---

## 6. Constraints & Enums

Rather than PostgreSQL `ENUM` types (which are hard to ALTER without downtime), all enum-like columns use `VARCHAR` with `CHECK` constraints and are validated at the SQLModel layer:

```sql
-- users.role
ALTER TABLE users ADD CONSTRAINT chk_role
  CHECK (role IN ('admin', 'trader', 'viewer'));

-- user_settings.trading_mode
ALTER TABLE user_settings ADD CONSTRAINT chk_trading_mode
  CHECK (trading_mode IN ('paper', 'live'));

-- orders.status
ALTER TABLE orders ADD CONSTRAINT chk_order_status
  CHECK (status IN ('PENDING', 'OPEN', 'COMPLETE', 'REJECTED', 'CANCELLED'));

-- orders.order_type
ALTER TABLE orders ADD CONSTRAINT chk_order_type
  CHECK (order_type IN ('MARKET', 'LIMIT', 'SL', 'SL-M'));

-- orders.transaction_type / trades.transaction_type
ALTER TABLE orders ADD CONSTRAINT chk_txn_type
  CHECK (transaction_type IN ('BUY', 'SELL'));

-- signals.signal
ALTER TABLE signals ADD CONSTRAINT chk_signal_type
  CHECK (signal IN ('BUY', 'SELL', 'HOLD'));

-- corporate_actions.action_type
ALTER TABLE corporate_actions ADD CONSTRAINT chk_action_type
  CHECK (action_type IN ('SPLIT', 'BONUS', 'DIVIDEND'));

-- ensemble_config singleton
ALTER TABLE ensemble_config ADD CONSTRAINT singleton CHECK (id = 1);

-- ensemble_config weights must sum to 1.0 (± 0.001 float tolerance)
ALTER TABLE ensemble_config ADD CONSTRAINT chk_weights_sum
  CHECK (ABS((lgbm_weight + tft_weight + finbert_weight + anomaly_weight) - 1.0) < 0.001);

---

## 7. TBL_LAST_DT — Auto-Update Trigger

`tbl_last_dt` is a standardised audit column present on every table. It is **automatically set to the current UTC timestamp whenever any row is updated** — no application code needed. On INSERT, the column is set to `NOW()` as the default. On every subsequent UPDATE, the trigger overwrites it with the current timestamp.

This gives a single, reliable answer to: *"when was this row last touched?"* — useful for debugging, admin auditing, and the nightly EOD backfill job.

---

### 7.1 Shared Trigger Function

One reusable function handles all tables. Create this once before the per-table setup below:

```sql
CREATE OR REPLACE FUNCTION fn_set_tbl_last_dt()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Overwrite tbl_last_dt with the current UTC timestamp on every UPDATE
    NEW.tbl_last_dt = NOW();
    RETURN NEW;
END;
$$;
```

---

### 7.2 Per-Table Column & Trigger

The pattern is identical for every table:
1. Add the column with `DEFAULT NOW()` — so INSERT populates it automatically without a trigger
2. Create a `BEFORE UPDATE` row-level trigger that calls `fn_set_tbl_last_dt()`

> **Note on append-only tables** (`trades`, `price_1min`, `news_sentiment`): rows in these tables are never UPDATEd after insert. The column is still added (it reflects the insert time), but the trigger will never fire — this is correct behaviour. `corporate_actions` and `signals` do receive UPDATEs (backfill jobs set `is_applied` and `outcome_label`) so their triggers are active.
>
> **Trigger overhead on high-frequency hypertables:** PostgreSQL evaluates trigger hook existence on every DML operation, even when the trigger condition (`BEFORE UPDATE`) is never met. For relational tables this cost is negligible. For `price_1min` — which receives one row per ticker per minute across ~500 tickers (~8,000+ inserts/day, bursting heavily at market open/close) — this is a potential micro-optimization. The `BEFORE UPDATE` trigger is **safe to omit** on `price_1min` since the table is strictly append-only by design; the `tbl_last_dt` column still receives the insert time via `DEFAULT NOW()`, so no observability is lost.
>
> **Decision for now:** Keep the trigger on `price_1min` for migration uniformity. If tick ingestion ever shows as a bottleneck in profiling (e.g., `pg_stat_activity` showing high lock contention on the hypertable), drop the trigger with:
> ```sql
> DROP TRIGGER IF EXISTS trg_price_1min_tbl_last_dt ON price_1min;
> ```
> This is a zero-downtime, zero-data-loss operation. Same applies to `trades` and `news_sentiment` if they ever reach similar insert rates.

```sql
-- ─────────────────────────────────────────────
-- RELATIONAL TABLES
-- ─────────────────────────────────────────────

-- users
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_users_tbl_last_dt
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- user_settings
ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_user_settings_tbl_last_dt
    BEFORE UPDATE ON user_settings
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- user_broker_config
ALTER TABLE user_broker_config
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_user_broker_config_tbl_last_dt
    BEFORE UPDATE ON user_broker_config
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- tickers
ALTER TABLE tickers
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_tickers_tbl_last_dt
    BEFORE UPDATE ON tickers
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- portfolio
ALTER TABLE portfolio
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_portfolio_tbl_last_dt
    BEFORE UPDATE ON portfolio
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- orders
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_orders_tbl_last_dt
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- trades  (append-only — trigger added but will never fire)
ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_trades_tbl_last_dt
    BEFORE UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- watchlist
ALTER TABLE watchlist
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_watchlist_tbl_last_dt
    BEFORE UPDATE ON watchlist
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- model_runs
ALTER TABLE model_runs
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_model_runs_tbl_last_dt
    BEFORE UPDATE ON model_runs
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- ensemble_config
ALTER TABLE ensemble_config
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_ensemble_config_tbl_last_dt
    BEFORE UPDATE ON ensemble_config
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- expo_push_tokens
ALTER TABLE expo_push_tokens
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_expo_push_tokens_tbl_last_dt
    BEFORE UPDATE ON expo_push_tokens
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();


-- ─────────────────────────────────────────────
-- TIMESCALEDB HYPERTABLES
-- Triggers on hypertables work identically to regular tables in PostgreSQL/TimescaleDB.
-- The trigger fires on each individual row, not on the chunk — behaviour is transparent.
-- ─────────────────────────────────────────────

-- price_1min  (append-only — trigger added but will never fire)
ALTER TABLE price_1min
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_price_1min_tbl_last_dt
    BEFORE UPDATE ON price_1min
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- price_1day  (updated by apply_corporate_actions task — trigger is active)
ALTER TABLE price_1day
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_price_1day_tbl_last_dt
    BEFORE UPDATE ON price_1day
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- corporate_actions  (updated when is_applied flipped — trigger is active)
ALTER TABLE corporate_actions
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_corporate_actions_tbl_last_dt
    BEFORE UPDATE ON corporate_actions
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- news_sentiment  (append-only — trigger added but will never fire)
ALTER TABLE news_sentiment
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_news_sentiment_tbl_last_dt
    BEFORE UPDATE ON news_sentiment
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();

-- ─────────────────────────────────────────────

-- signals  (updated by EOD job — actual_return_*, outcome_label — trigger is active)
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TRIGGER trg_signals_tbl_last_dt
    BEFORE UPDATE ON signals
    FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt();
```

---

### 7.3 Verification Query

After applying the above, verify all triggers are registered:

```sql
SELECT
    event_object_table  AS table_name,
    trigger_name,
    event_manipulation  AS event,
    action_timing       AS timing
FROM information_schema.triggers
WHERE trigger_name LIKE '%tbl_last_dt%'
ORDER BY event_object_table;
```

Expected output — one `BEFORE UPDATE` trigger per table (16 rows total):

```
 table_name           | trigger_name                            | event  | timing
----------------------+-----------------------------------------+--------+--------
 corporate_actions    | trg_corporate_actions_tbl_last_dt       | UPDATE | BEFORE
 ensemble_config      | trg_ensemble_config_tbl_last_dt         | UPDATE | BEFORE
 expo_push_tokens     | trg_expo_push_tokens_tbl_last_dt        | UPDATE | BEFORE
 model_runs           | trg_model_runs_tbl_last_dt              | UPDATE | BEFORE
 news_sentiment       | trg_news_sentiment_tbl_last_dt          | UPDATE | BEFORE
 orders               | trg_orders_tbl_last_dt                  | UPDATE | BEFORE
 portfolio            | trg_portfolio_tbl_last_dt               | UPDATE | BEFORE
 price_1day           | trg_price_1day_tbl_last_dt              | UPDATE | BEFORE
 price_1min           | trg_price_1min_tbl_last_dt              | UPDATE | BEFORE
 signals              | trg_signals_tbl_last_dt                 | UPDATE | BEFORE
 tickers              | trg_tickers_tbl_last_dt                 | UPDATE | BEFORE
 trades               | trg_trades_tbl_last_dt                  | UPDATE | BEFORE
 user_broker_config   | trg_user_broker_config_tbl_last_dt      | UPDATE | BEFORE
 user_settings        | trg_user_settings_tbl_last_dt           | UPDATE | BEFORE
 users                | trg_users_tbl_last_dt                   | UPDATE | BEFORE
 watchlist            | trg_watchlist_tbl_last_dt               | UPDATE | BEFORE
```

---

### 7.4 Notes

| Point | Detail |
|-------|--------|
| **One function, 15 triggers** | `fn_set_tbl_last_dt()` is defined once and shared. Adding a new table in future = one `ALTER TABLE` + one `CREATE TRIGGER`. |

Note: 16 triggers total currently registered (15 original + `expo_push_tokens`).
| **INSERT behaviour** | `DEFAULT NOW()` on the column handles inserts — no INSERT trigger needed. |
| **Append-only tables** | `trades`, `price_1min`, `news_sentiment` never receive UPDATEs. `tbl_last_dt` equals `created_at` / `traded_at` for those rows — this is correct and expected. |
| **TimescaleDB compatibility** | Triggers on hypertables work identically to regular tables. They fire per-row on each chunk transparently. |
| **`BEFORE UPDATE` vs `AFTER UPDATE`** | `BEFORE` is used because it allows `NEW` to be modified before the row is written. `AFTER` triggers cannot modify the row. |
| **Idempotent `ALTER TABLE`** | `ADD COLUMN IF NOT EXISTS` means the migration is safe to re-run (e.g. in CI against a fresh test DB). |
| **Integration with Alembic** | The `ALTER TABLE` statements and `CREATE TRIGGER` calls belong in an Alembic migration version file — not in application code — so they run exactly once on schema upgrade. |
```
