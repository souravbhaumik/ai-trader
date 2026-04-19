# AI Trader — API Contract

> **Status**: Pre-implementation design reference  
> All endpoints are versioned under `/api/v1/`. Base URL in production: `https://<your-cloudflare-domain>/api/v1/`

---

## Table of Contents

1. [API Security Model](#1-api-security-model)
2. [Request / Response Conventions](#2-request--response-conventions)
3. [Error Codes](#3-error-codes)
4. [Authentication](#4-authentication)
5. [Users & Settings](#5-users--settings)
6. [Signals](#6-signals)
7. [Orders](#7-orders)
8. [Portfolio](#8-portfolio)
9. [Prices & Market Data](#9-prices--market-data)
10. [Screener](#10-screener)
11. [Watchlist](#11-watchlist)
12. [News & Sentiment](#12-news--sentiment)
13. [WebSocket Channels](#13-websocket-channels)
14. [Admin Endpoints](#14-admin-endpoints)
15. [Webhooks (Broker Postback)](#15-webhooks-broker-postback)
16. [Mobile — Push Tokens](#16-mobile--push-tokens)

---

## 1. API Security Model

### 1.1 Authentication Flow

```
POST /api/v1/auth/login
  → 200: { access_token } in JSON body
        refresh_token   in httpOnly SameSite=Strict cookie (7-day TTL)

Every subsequent request:
  Authorization: Bearer <access_token>   (15-min TTL)

On 401 (access token expired):
  POST /api/v1/auth/refresh (sends cookie automatically)
  → 200: { access_token }  (new 15-min token)

Logout:
  POST /api/v1/auth/logout
  → Deletes cookie; adds jti to Redis blocklist; marks refresh_tokens row revoked
```

### 1.2 JWT Structure

```json
{
  "sub": "<user_id>",
  "jti": "<uuid>",
  "role": "trader | admin | viewer",
  "exp": 1234567890,
  "iat": 1234567890
}
```

- `jti` is checked against a Redis blocklist on every request (`GET blocklist:{jti}`)
- Expired tokens are rejected by FastAPI's JWT dependency before any route handler runs
- Tokens are **never** stored in `localStorage` — access token lives in memory (React state / Zustand); refresh token is httpOnly cookie only

### 1.3 Rate Limiting

Rate limits are applied per `user_id` (authenticated routes) or per IP (auth routes). Limits are enforced by a FastAPI middleware reading `CF-Connecting-IP` (Cloudflare header).

| Route Group | Limit |
|-------------|-------|
| `POST /auth/login` | 10 req/min per IP |
| `POST /auth/refresh` | 30 req/min per user |
| `POST /orders` | 20 req/min per user |
| All other authenticated routes | 120 req/min per user |
| Admin routes | 60 req/min per admin user |

On limit exceeded: `429 Too Many Requests` with `Retry-After` header.

### 1.4 Role-Based Access Control

| Role | Access |
|------|--------|
| `viewer` | Read-only: signals, prices, portfolio, screener. No order placement. |
| `trader` | All viewer access + order placement (paper always; live if enabled). |
| `admin` | Full access including all `/admin/*` routes. TOTP mandatory. |

Routes that require a minimum role annotate it in the endpoint table below as `[trader]`, `[admin]`, etc.

### 1.5 Input Validation & Security Controls

- All request bodies validated via Pydantic v2 models; invalid payloads return `422 Unprocessable Entity`
- String fields are stripped and length-capped at the Pydantic layer; no raw SQL construction anywhere
- File uploads (if added in future): type whitelist enforced; stored in B2, never served from the API origin
- `Idempotency-Key` UUID header required on `POST /orders` — server deduplicates for 5 min in Redis
- Sensitive fields (`password`, `totp_secret`, broker keys) are never returned in any response body
- All timestamps returned as ISO 8601 UTC strings

### 1.6 CORS Policy

```python
allow_origins = [
    "https://<frontend-vercel-domain>",
    "http://localhost:3000",   # dev only; controlled by ALLOWED_ORIGINS env var
]
allow_credentials = True       # required for httpOnly cookie on refresh
allow_methods = ["GET", "POST", "PATCH", "DELETE"]
allow_headers = ["Authorization", "Content-Type", "Idempotency-Key"]
```

Wildcard `*` origins are **never** permitted — this would allow a malicious site to make credentialed requests with the user's cookie.

### 1.7 Webhook Security (Broker Postbacks)

`POST /api/v1/webhooks/order-update` does **not** use Bearer auth (brokers can't supply a user JWT). Instead:

- Angel One: `X-AngelOne-Signature: HMAC-SHA256(secret, body)`
- Upstox: `X-Upstox-Signature: HMAC-SHA256(secret, body)`

The webhook handler verifies the HMAC before any DB access. Requests with invalid or missing signatures return `403 Forbidden` immediately. Secrets are stored in `.env` — never the DB.

---

## 2. Request / Response Conventions

### Success Envelope

```json
{
  "success": true,
  "data": { ... }
}
```

For paginated lists:
```json
{
  "success": true,
  "data": [ ... ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total": 312,
    "next_cursor": "2024-03-15T06:00:00Z"
  }
}
```

### Error Envelope

```json
{
  "success": false,
  "error": {
    "code": "ORDER_REJECTED",
    "message": "Order size exceeds 25% of available liquidity",
    "details": { "max_quantity": 150, "requested_quantity": 600 }
  }
}
```

### Conventions

- **Pagination**: cursor-based for time-series endpoints (`?before=<ISO_timestamp>`); offset-based (`?page=&page_size=`) for list endpoints. Default `page_size=50`, max `200`.
- **Timestamps**: ISO 8601 UTC in all payloads. `2024-03-15T09:15:00Z`
- **JSON keys**: `snake_case` throughout
- **Currency**: all monetary values in Indian Rupees (₹), represented as `DECIMAL` strings to avoid floating-point rounding in JSON (e.g. `"price": "2450.50"`)
- **Symbols**: NSE symbols in UPPERCASE (e.g. `"RELIANCE"`, `"TCS"`)

---

## 3. Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `INVALID_CREDENTIALS` | 401 | Wrong email or password |
| `TOKEN_EXPIRED` | 401 | Access JWT expired — refresh |
| `TOKEN_REVOKED` | 401 | JWT `jti` is in the blocklist |
| `TOTP_REQUIRED` | 403 | Admin action requires valid TOTP verification |
| `INSUFFICIENT_ROLE` | 403 | Route requires higher role |
| `LIVE_TRADING_DISABLED` | 403 | User has not enabled live trading |
| `INVITE_INVALID` | 400 | Invite token not found, expired, used, or revoked |
| `INVITE_EXPIRED` | 400 | Invite token past 24h TTL |
| `VALIDATION_ERROR` | 422 | Pydantic validation failed (details in `details` field) |
| `ORDER_REJECTED` | 400 | Business rule rejection (see `details`) |
| `PAPER_ORDER_TOO_LARGE` | 400 | Order qty > 25% of available book depth |
| `ORDERBOOK_STALE` | 400 | Redis order book TTL expired; order not filled |
| `IDEMPOTENCY_CONFLICT` | 409 | Same `Idempotency-Key` already processed; cached response in `data` |
| `SIGNAL_NOT_FOUND` | 404 | Signal for given symbol/timestamp not found |
| `RATE_LIMITED` | 429 | Rate limit exceeded; see `Retry-After` header |
| `LOCK_HELD` | 202 | Signal generation currently running; see `retry_after_seconds` |
| `INTERNAL_ERROR` | 500 | Unexpected server error; correlation ID in `details.trace_id` |

---

## 4. Authentication

### `POST /auth/register`

Register with an invite token. No JWT required.

**Request**
```json
{
  "invite_token": "itsdangerous-signed-token",
  "full_name": "Rahul Sharma",
  "password": "MinLength12Chars!"
}
```

**Response** `201`
```json
{
  "success": true,
  "data": {
    "user_id": "uuid",
    "email": "rahul@example.com",
    "message": "Account created. Please log in."
  }
}
```

**Validation**:
- `invite_token`: must exist in `user_invites` with `status=pending` and `expires_at > now()`
- `password`: minimum 12 characters, at least 1 uppercase, 1 digit, 1 symbol
- Token is single-use; marked `status=used` and `used_at=now()` on success

---

### `POST /auth/login`

**Request**
```json
{
  "email": "rahul@example.com",
  "password": "MinLength12Chars!",
  "totp_code": "123456"    // required only if role=admin and is_totp_configured=true
}
```

**Response** `200`
```json
{
  "success": true,
  "data": {
    "access_token": "eyJ...",
    "token_type": "bearer",
    "expires_in": 900,
    "user": {
      "id": "uuid",
      "email": "rahul@example.com",
      "full_name": "Rahul Sharma",
      "role": "trader",
      "trading_mode": "paper",
      "is_live_trading_enabled": false
    }
  }
}
```

Sets `HttpOnly; SameSite=Strict; Secure` cookie: `refresh_token=<token>; Path=/api/v1/auth; Max-Age=604800`

---

### `POST /auth/refresh`

No body. Reads `refresh_token` cookie.

**Response** `200`
```json
{
  "success": true,
  "data": { "access_token": "eyJ...", "expires_in": 900 }
}
```

**Failure** `401` — `TOKEN_REVOKED` or `TOKEN_EXPIRED`

---

### `POST /auth/logout`

Requires: `Authorization: Bearer <token>`

Revokes the refresh token (DB row + Redis blocklist for both `jti`s). Clears cookie.

**Response** `200`
```json
{ "success": true, "data": { "message": "Logged out" } }
```

---

### `POST /auth/totp/setup` `[admin]`

Initiates TOTP setup. Returns a TOTP secret and QR code URI. Requires the admin to confirm with a valid TOTP code before the secret is activated.

**Response** `200`
```json
{
  "success": true,
  "data": {
    "secret": "BASE32SECRET",
    "otpauth_uri": "otpauth://totp/AITrader:admin@example.com?secret=...&issuer=AITrader",
    "qr_code_base64": "data:image/png;base64,..."
  }
}
```

### `POST /auth/totp/verify` `[admin]`

**Request** `{ "totp_code": "123456" }`

Sets `is_totp_configured = true` and `is_totp_verified = true` in session.

---

## 5. Users & Settings

### `GET /users/me`

Returns the authenticated user's profile.

**Response** `200`
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "email": "rahul@example.com",
    "full_name": "Rahul Sharma",
    "role": "trader",
    "is_active": true,
    "is_email_verified": true,
    "is_live_trading_enabled": false,
    "is_totp_configured": false,
    "last_login_at": "2024-03-15T09:00:00Z",
    "created_at": "2024-01-01T00:00:00Z"
  }
}
```

---

### `PATCH /users/me`

Update display name or password.

**Request** (all fields optional)
```json
{
  "full_name": "Rahul S",
  "current_password": "OldPass123!",
  "new_password": "NewPass456!"
}
```

Password change also revokes all existing refresh tokens for the user.

---

### `GET /users/me/settings`

**Response** `200`
```json
{
  "success": true,
  "data": {
    "trading_mode": "paper",
    "paper_balance": "1000000.00",
    "max_position_pct": "10.00",
    "daily_loss_limit_pct": "5.00",
    "notification_signals": true,
    "notification_orders": true,
    "preferred_broker": "angel_one"
  }
}
```

---

### `PATCH /users/me/settings`

**Request** (all fields optional)
```json
{
  "max_position_pct": "15.00",
  "daily_loss_limit_pct": "3.00",
  "notification_signals": false
}
```

`trading_mode` cannot be changed via this endpoint — use `POST /users/me/live-trading/enable`.

---

### `POST /users/me/live-trading/enable` `[trader]`

Initiates the live trading enablement flow. Sends an OTP to the user's registered email. The user must confirm with `POST /users/me/live-trading/confirm`.

**Response** `200`
```json
{ "success": true, "data": { "message": "OTP sent to rahul@example.com" } }
```

---

### `POST /users/me/live-trading/confirm` `[trader]`

**Request** `{ "otp": "847291" }`

Sets `is_live_trading_enabled = true`. OTP is single-use, 10-min TTL.

---

### `GET /users/me/sessions`

Lists active refresh token sessions for the current user.

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "id": "session-uuid",
      "issued_at": "2024-03-15T09:00:00Z",
      "expires_at": "2024-03-22T09:00:00Z",
      "user_agent": "Mozilla/5.0 ...",
      "ip_address": "103.x.x.x",
      "is_current": true
    }
  ]
}
```

---

### `DELETE /users/me/sessions/{session_id}`

Revokes a specific session (not the current one). Token `jti` added to Redis blocklist.

---

## 6. Signals

### `GET /signals`

Returns latest signals for all watched symbols. Signals older than 1 trading session are excluded by default.

**Query params**
| Param | Default | Description |
|-------|---------|-------------|
| `action` | (all) | Filter: `BUY`, `SELL`, `HOLD` |
| `min_confidence` | `0.0` | Filter by ensemble confidence score |
| `symbols` | (all) | Comma-separated: `RELIANCE,TCS` |
| `before` | (now) | Cursor for pagination (ISO timestamp) |
| `page_size` | `50` | Max `200` |

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "symbol": "RELIANCE",
      "timestamp": "2024-03-15T10:05:00Z",
      "action": "BUY",
      "confidence": 0.78,
      "lgbm_score": 0.81,
      "tft_score": 0.75,
      "anomaly_score": 0.12,
      "sentiment_score": 0.65,
      "explanation": "MACD golden cross confirmed with above-average volume surge...",
      "features_snapshot": {
        "rsi_14": 52.3,
        "macd_cross_direction": "bullish",
        "volume_ratio_20d": 1.8
      }
    }
  ],
  "meta": { "next_cursor": "2024-03-15T09:55:00Z", "total": 47 }
}
```

---

### `GET /signals/{symbol}`

Latest signal for a specific symbol.

**Response** `200` — same shape as single item from `GET /signals`

**Failure** `404` — `SIGNAL_NOT_FOUND`

---

### `GET /signals/status`

Returns current state of the signal generation pipeline. Used by the frontend to display the "Signals refreshing..." banner.

**Response** `200`
```json
{
  "success": true,
  "data": {
    "lock_held": true,
    "lock_ttl_seconds": 287,
    "last_run_at": "2024-03-15T10:00:00Z",
    "last_run_signal_count": 43,
    "last_run_duration_seconds": 112
  }
}
```

---

### `POST /signals/refresh` `[trader]`

Manual trigger. If the Redis `generate_signals` lock is currently held, returns `202` immediately — does **not** enqueue a parallel job.

**Response** `202` (lock held)
```json
{
  "success": true,
  "data": {
    "status": "in_progress",
    "reason": "signal_cycle_running",
    "retry_after_seconds": 287
  }
}
```

**Response** `200` (lock free — new cycle started)
```json
{ "success": true, "data": { "status": "started", "task_id": "celery-task-uuid" } }
```

---

## 7. Orders

### `GET /orders`

**Query params**: `status` (`PENDING|OPEN|COMPLETE|REJECTED|CANCELLED`), `trading_mode` (`paper|live`), `symbol`, `before`, `page_size`

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "symbol": "TCS",
      "order_type": "MARKET",
      "transaction_type": "BUY",
      "quantity": 10,
      "limit_price": null,
      "trigger_price": null,
      "status": "COMPLETE",
      "trading_mode": "paper",
      "broker_order_id": null,
      "signal_id": { "symbol": "TCS", "timestamp": "2024-03-15T10:05:00Z" },
      "idempotency_key": "uuid",
      "filled_quantity": 10,
      "average_price": "3720.50",
      "placed_at": "2024-03-15T10:06:00Z",
      "updated_at": "2024-03-15T10:06:02Z"
    }
  ]
}
```

---

### `POST /orders` `[trader]`

Place a paper or live order. Live orders require `is_live_trading_enabled = true`.

**Headers**: `Idempotency-Key: <uuid-v4>` (required)

**Request**
```json
{
  "symbol": "TCS",
  "order_type": "MARKET",
  "transaction_type": "BUY",
  "quantity": 10,
  "limit_price": null,
  "trigger_price": null,
  "signal_id": {
    "symbol": "TCS",
    "timestamp": "2024-03-15T10:05:00Z"
  }
}
```

**Validation**:
- `order_type`: `MARKET | LIMIT | SL | SL-M`
- `transaction_type`: `BUY | SELL`
- `quantity`: positive integer, ≥ 1
- `limit_price`: required if `order_type = LIMIT`
- `trigger_price`: required if `order_type IN (SL, SL-M)`
- `signal_id`: optional; if provided, validated against signals table (app-layer soft FK)

**Response** `201`
```json
{
  "success": true,
  "data": {
    "id": "order-uuid",
    "status": "PENDING",
    "trading_mode": "paper",
    "message": "Order placed successfully"
  }
}
```

**Failure codes**: `ORDER_REJECTED`, `PAPER_ORDER_TOO_LARGE`, `ORDERBOOK_STALE`, `LIVE_TRADING_DISABLED`, `IDEMPOTENCY_CONFLICT`

---

### `GET /orders/{order_id}`

**Response** `200` — single order object (same shape as list item)

---

### `DELETE /orders/{order_id}` `[trader]`

Cancel a `PENDING` or `OPEN` order. For live orders, calls broker cancel API first.

**Response** `200`
```json
{ "success": true, "data": { "id": "uuid", "status": "CANCELLED" } }
```

---

## 8. Portfolio

### `GET /portfolio`

**Query params**: `trading_mode` (`paper|live`, default `paper`)

**Response** `200`
```json
{
  "success": true,
  "data": {
    "trading_mode": "paper",
    "cash_balance": "850000.00",
    "total_invested": "150000.00",
    "current_value": "163500.00",
    "unrealized_pnl": "13500.00",
    "unrealized_pnl_pct": "9.00",
    "realized_pnl_today": "2300.00",
    "positions": [
      {
        "symbol": "RELIANCE",
        "quantity": 50,
        "avg_buy_price": "2420.00",
        "current_price": "2510.00",
        "current_value": "125500.00",
        "unrealized_pnl": "4500.00",
        "unrealized_pnl_pct": "3.72"
      }
    ]
  }
}
```

---

### `GET /portfolio/trades`

Trade history with FIFO-calculated realized P&L for SELL trades.

**Query params**: `trading_mode`, `symbol`, `before`, `page_size`

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "order_id": "uuid",
      "symbol": "TCS",
      "transaction_type": "SELL",
      "quantity": 5,
      "price": "3800.00",
      "pnl": "395.00",
      "brokerage": "5.70",
      "trading_mode": "paper",
      "traded_at": "2024-03-15T14:30:00Z"
    }
  ]
}
```

---

## 9. Prices & Market Data

### `GET /prices/{symbol}/history`

Daily OHLCV history from `price_1day` (adjusted close).

**Query params**
| Param | Default | Description |
|-------|---------|-------------|
| `from` | 1 year ago | ISO date |
| `to` | today | ISO date |
| `interval` | `1d` | `1d` only (1-min data not served via REST — use WebSocket) |
| `adjusted` | `true` | Use `adj_close` (split/bonus adjusted) |

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "date": "2024-03-15",
      "open": "2410.00",
      "high": "2530.00",
      "low": "2400.00",
      "close": "2510.00",
      "adj_close": "2510.00",
      "volume": 1840000
    }
  ]
}
```

---

### `GET /prices/{symbol}/quote`

Current price from Redis (live during market hours; last close outside hours).

**Response** `200`
```json
{
  "success": true,
  "data": {
    "symbol": "RELIANCE",
    "ltp": "2510.00",
    "change": "45.50",
    "change_pct": "1.84",
    "best_bid": "2509.80",
    "best_ask": "2510.20",
    "volume": 1840000,
    "timestamp": "2024-03-15T10:15:30Z",
    "is_stale": false
  }
}
```

`is_stale: true` when the Redis key has expired (outside market hours or ingestor disconnected).

---

## 10. Screener

### `GET /screener`

Filter tickers by technical and fundamental criteria. Results sourced from the latest signal run.

**Query params**
| Param | Type | Description |
|-------|------|-------------|
| `sector` | string | GICS sector name |
| `min_confidence` | float | Minimum ensemble confidence |
| `action` | string | `BUY\|SELL\|HOLD` |
| `min_rsi` | float | RSI lower bound |
| `max_rsi` | float | RSI upper bound |
| `min_volume_ratio` | float | Min volume vs 20d avg (e.g. `1.5` = 50% above avg) |
| `anomaly_detected` | bool | Only show tickers with anomaly score > 0.5 |
| `page` | int | Default `1` |
| `page_size` | int | Default `50` |

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "symbol": "INFY",
      "name": "Infosys Ltd",
      "sector": "Information Technology",
      "action": "BUY",
      "confidence": 0.82,
      "ltp": "1580.00",
      "change_pct": "2.10",
      "rsi_14": 58.4,
      "volume_ratio_20d": 1.9,
      "anomaly_score": 0.08,
      "signal_age_minutes": 3
    }
  ],
  "meta": { "page": 1, "page_size": 50, "total": 127 }
}
```

---

## 11. Watchlist

### `GET /watchlist`

**Response** `200`
```json
{
  "success": true,
  "data": [
    { "symbol": "RELIANCE", "name": "Reliance Industries", "added_at": "2024-01-15T00:00:00Z" }
  ]
}
```

---

### `POST /watchlist` `[trader]`

**Request** `{ "symbol": "HDFCBANK" }`

**Response** `201` — `{ "success": true, "data": { "symbol": "HDFCBANK", "added_at": "..." } }`

---

### `DELETE /watchlist/{symbol}` `[trader]`

**Response** `200` — `{ "success": true, "data": { "message": "Removed from watchlist" } }`

---

## 12. News & Sentiment

### `GET /news`

**Query params**: `symbol` (required or `?global=true`), `before`, `page_size`

**Sentiment label mapping** (calculated at application layer — `sentiment_label` is NOT stored in the DB; `news_sentiment.sentiment_score` is the raw FinBERT output):

| `sentiment_score` range | `sentiment_label` |
|-------------------------|-------------------|
| `> 0.25` | `positive` |
| `< -0.25` | `negative` |
| `-0.25` to `0.25` inclusive | `neutral` |

Thresholds are defined as constants in `services/news_aggregator.py` (`SENTIMENT_POSITIVE_THRESHOLD = 0.25`, `SENTIMENT_NEGATIVE_THRESHOLD = -0.25`) so frontend and backend derive identical labels from the same score. FinBERT outputs range from `-1.0` (strong bearish) to `+1.0` (strong bullish); the `±0.25` band covers typical noise around neutrality.

**Response** `200`
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "symbol": "TCS",
      "headline": "TCS reports record Q4 revenue...",
      "source": "Economic Times",
      "url": "https://economictimes.indiatimes.com/...",
      "sentiment_label": "positive",
      "sentiment_score": 0.87,
      "published_at": "2024-03-15T08:30:00Z"
    }
  ]
}
```

---

### `GET /news/sentiment-summary/{symbol}`

Rolling sentiment aggregation for a symbol (last 24h and last 7d).

**Response** `200`
```json
{
  "success": true,
  "data": {
    "symbol": "TCS",
    "articles_24h": 12,
    "avg_sentiment_24h": 0.72,
    "articles_7d": 47,
    "avg_sentiment_7d": 0.61,
    "dominant_label_24h": "positive"
  }
}
```

---

## 13. WebSocket Channels

WebSockets live outside the REST versioning but are authenticated the same way.

Authentication: `?token=<access_jwt>` query parameter. The `?token=` value is **stripped from all server logs** by `logging_middleware.py` before writing.

### `WS /ws/prices?token=<jwt>`

**Subscribe**
```json
{ "action": "subscribe", "symbols": ["RELIANCE", "TCS", "INFY"] }
```

**Unsubscribe**
```json
{ "action": "unsubscribe", "symbols": ["INFY"] }
```

**Server → Client messages**
```json
{ "type": "price_tick", "symbol": "RELIANCE", "ltp": "2510.50", "change_pct": "1.84", "best_bid": "2510.30", "best_ask": "2510.70", "timestamp": "2024-03-15T10:15:30Z" }
```

```json
{ "type": "ingestor_status", "status": "DISCONNECTED", "reason": "broker_timeout" }
```

On `ingestor_status: DISCONNECTED` — frontend must disable order buttons and show the "Connection lost" overlay. Re-enable when a `CONNECTED` message is received.

---

### `WS /ws/signals?token=<jwt>`

**Server → Client messages**

New signal:
```json
{ "type": "signal", "symbol": "TCS", "action": "BUY", "confidence": 0.74, "timestamp": "2024-03-15T10:05:00Z" }
```

Order status update:
```json
{ "type": "order_update", "order_id": "uuid", "status": "COMPLETE", "filled_quantity": 10, "average_price": "3720.50", "timestamp": "..." }
```

---

### Token Refresh on Open Connection

The 15-min access token will expire while a WebSocket is held open. The frontend re-authorizes without disconnecting:

```json
{ "action": "auth_refresh", "token": "<new_access_jwt>" }
```

Server validates inline. On failure: closes with code `4001`. Client must reconnect.

---

## 14. Admin Endpoints

All endpoints under `/admin/*` require `role = admin` AND `is_totp_verified = true` (verified in the current session).

### `GET /admin/users`

**Query params**: `role`, `trading_mode`, `is_active`, `page`, `page_size`

**Response** `200` — paginated list of user objects (full profile, not public shape)

---

### `POST /admin/users/invite`

Generate a signed magic-link invite (24h TTL, single-use).

**Request**
```json
{ "email": "newtrader@example.com" }
```

**Response** `201`
```json
{
  "success": true,
  "data": {
    "invite_id": "uuid",
    "email": "newtrader@example.com",
    "registration_link": "https://app.example.com/register?token=<signed-token>",
    "expires_at": "2024-03-16T10:00:00Z"
  }
}
```

The registration link is shown to the admin to share manually — no email server required.

---

### `PATCH /admin/users/{user_id}`

**Request** (all optional)
```json
{
  "is_active": false,
  "role": "viewer",
  "is_live_trading_enabled": false
}
```

Deactivating a user (`is_active: false`) triggers immediate revocation of all their refresh tokens + `jti` blocklisting.

---

### `POST /admin/users/{user_id}/sessions/revoke-all`

Revokes all active sessions for the specified user. Used for force-logout.

---

### `DELETE /admin/users/{user_id}/paper-reset`

Resets paper balance to default (₹10,00,000) and wipes all paper portfolio rows. Requires double-confirmation header: `X-Confirm: RESET-PAPER-{user_id}`.

---

### `GET /admin/invites`

Lists all invite records with status filter.

**Query params**: `status` (`pending|used|expired|revoked`), `page`, `page_size`

---

### `DELETE /admin/invites/{invite_id}`

Revoke a pending invite.

---

### `GET /admin/system/health`

All services health, Celery worker status, Redis memory, ingestor state.

**Response** `200`
```json
{
  "success": true,
  "data": {
    "postgres": { "status": "healthy", "latency_ms": 3 },
    "redis": { "status": "healthy", "used_memory_mb": 48, "max_memory_mb": 512 },
    "celery_workers": { "high_priority": 1, "default": 1, "low_priority": 1 },
    "ingestor": { "status": "CONNECTED", "last_tick_age_seconds": 12, "symbols_subscribed": 498 },
    "signal_lock": { "held": false, "last_run_at": "2024-03-15T10:00:00Z" }
  }
}
```

---

### `POST /admin/tasks/{task_name}/run`

Trigger a Celery task manually. Valid `task_name` values: `generate_signals`, `fetch_bhavcopy`, `apply_corporate_actions`, `fetch_news_sentiment`, `health_check`.

**Response** `202`
```json
{ "success": true, "data": { "task_id": "celery-uuid", "task_name": "fetch_bhavcopy" } }
```

---

### `GET /admin/tasks/{task_id}/status`

**Response** `200`
```json
{
  "success": true,
  "data": {
    "task_id": "celery-uuid",
    "status": "SUCCESS",
    "result": { "signals_generated": 43 },
    "started_at": "2024-03-15T10:00:00Z",
    "completed_at": "2024-03-15T10:01:52Z"
  }
}
```

---

### `GET /admin/models`

Lists MLflow model runs with metrics and current production/staging status.

---

### `POST /admin/models/{run_id}/promote`

Promotes an MLflow model run to `production` stage, demotes current production to `staging`.

---

### `PATCH /admin/ensemble/config`

Update ensemble model weights. Valid weights must sum to 1.0.

**Request**
```json
{
  "lgbm_weight": 0.40,
  "tft_weight": 0.35,
  "anomaly_weight": 0.10,
  "finbert_weight": 0.15
}
```

---

### `POST /admin/corporate-actions`

Manually add a corporate action for a listed ticker. Triggers the `apply_corporate_actions` Celery task immediately for the inserted row (price history adjustment + portfolio/order adjustment — see DESIGN.md Section 5.6).

**Request**
```json
{
  "symbol": "RELIANCE",
  "ex_date": "2024-04-18",
  "action_type": "SPLIT",
  "ratio": 2.0,
  "dividend_amount": null
}
```

- `action_type`: `SPLIT` | `BONUS` | `DIVIDEND`
- `ratio`: required for `SPLIT` and `BONUS` (e.g. `2.0` = 2:1 split); `null` for `DIVIDEND`
- `dividend_amount`: required for `DIVIDEND` (per-share amount in ₹); `null` for `SPLIT`/`BONUS`
- `symbol` must exist in `tickers`

**Response** `201`
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "symbol": "RELIANCE",
    "ex_date": "2024-04-18",
    "action_type": "SPLIT",
    "ratio": "2.00000",
    "is_applied": false,
    "task_id": "celery-task-uuid"
  }
}

---

### `GET /admin/backup/drills`

List restore drill history.

---

### `POST /admin/backup/drills`

Record outcome of a manual restore drill.

**Request**
```json
{
  "backup_date": "2024-03-15",
  "passed": true,
  "row_counts": { "users": 12, "tickers": 498, "price_1day": 1240000 },
  "notes": "All row counts matched within 1-day delta."
}
```

---

## 15. Webhooks (Broker Postback)

### `POST /webhooks/order-update`

**No Bearer auth** — this endpoint is called by the broker, not a user. No JWT required.

**Headers**
```
Content-Type: application/json
```

**Request** (Angel One postback field names — broker sends these exact keys)
```json
{
  "orderid":      "ANGL123456",
  "status":       "complete",
  "filledshares": "10",
  "averageprice": "3720.50"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `orderid` | `string` | Broker's order ID — matched against `live_orders.broker_order_id` |
| `status` | `string` | Raw broker status (`open`, `complete`, `rejected`, `cancelled`) |
| `filledshares` | `string \| null` | Shares filled so far; broker sends as string |
| `averageprice` | `string \| null` | Average fill price; broker sends as string |

**Response** `200` — always returned, even on race-condition retry-queue
```json
{ "received": true }
```
> Always 200 to prevent the broker from retrying the postback.

**Race-condition handling:** if `orderid` is not yet in `live_orders` (postback arrived before `placeOrder` INSERT committed), a Celery task is scheduled with `countdown=3 s` to retry the DB update up to 3 times. Genuine orphans (never committed) are reconciled at EOD.

---

## 16. Mobile — Push Tokens

### `POST /mobile/push-token`

Register or update an Expo Push Notification token for the authenticated user.

**Request**
```json
{
  "token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "device_id": "expo-device-uuid",
  "platform": "android"
}
```

Server performs `UPSERT ON CONFLICT (device_id)`: updates token if device known, inserts new row if not. Marks previous token for this device `is_active = false`.

**Response** `200`
```json
{ "success": true, "data": { "registered": true } }
```

---

### `DELETE /mobile/push-token`

Deregister all push tokens for the current user (called on logout from mobile).

**Response** `200`
```json
{ "success": true, "data": { "deregistered": 1 } }
```
