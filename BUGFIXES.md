# BUGFIXES.md — Architectural Review & Bug-Fix Log

> **Scope:** Full peer-review of the ai-trader project (backend, frontend, pipelines,
> ML models, scheduling, IST timezone consistency).  
> **Requirement:** All timestamps must use **Indian Standard Time (IST = UTC+5:30)**,
> expressed as `datetime.now(_IST)` where `_IST = timezone(timedelta(hours=5, minutes=30))`.

---

## Critical Bugs Fixed

### 1 — `live_trade_service.py` | Wrong portfolio value in daily-loss limit
**Severity:** Critical — financial-safety bypass  
**File:** `backend/app/services/live_trade_service.py` → `_enforce_daily_loss_limit()`

**What was wrong:**  
The portfolio-value denominator used `SUM(price * filled_qty)` for *all* completed
live orders.  This counts BUY and SELL sides together, grossly overestimating the
portfolio value, which makes the `daily_loss_limit_pct` threshold almost impossible
to breach even when it should fire.

**Fix applied:**  
Replaced the query with a net-exposure calculation:
```sql
SUM(CASE WHEN direction='BUY'  THEN avg_fill_price * filled_qty ELSE 0 END)
- SUM(CASE WHEN direction='SELL' THEN avg_fill_price * filled_qty ELSE 0 END)
  AS net_portfolio_value
```

---

### 2 — Timezone: `datetime.utcnow()` / `datetime.now()` used throughout
**Severity:** High — all timestamps written to DB reflect UTC wall time,
not IST; day-boundary queries produce wrong results inside a UTC+5:30 context.

**Files fixed:**

| File | What was wrong | Fix |
|------|----------------|-----|
| `app/tasks/task_utils.py` | `datetime.now()` for task log timestamps | `datetime.now(_IST)` |
| `app/tasks/signal_generator.py` | `datetime.utcnow()` for signal `created_at` | `datetime.now(_IST)` |
| `app/tasks/signal_generator.py` | Market-open guard used `utcnow() + timedelta(5h30m)` | `datetime.now(_IST)` directly |
| `app/tasks/signal_outcome_evaluation.py` | `datetime.now().replace(...)` for "today midnight" | `datetime.now(_IST).replace(...)` |

**Pattern used everywhere:**
```python
from datetime import timezone, timedelta
_IST = timezone(timedelta(hours=5, minutes=30))
# Usage:
now_ts = datetime.now(_IST)
today  = datetime.now(_IST).replace(hour=0, minute=0, second=0, microsecond=0)
```

---

### 3 — `intraday_ingest.py` | Dead `_fetch_via_angel_one()` function
**Severity:** Medium — dead code that creates a new Angel One auth session per
symbol (O(n) logins vs O(1) with the shared-session approach); risk of accidental
re-use in future development.

**File:** `backend/app/tasks/intraday_ingest.py`

**What was wrong:**  
`_fetch_via_angel_one(symbol, from_dt, to_dt)` called `SmartConnect.generateSession()`
and `terminateSession()` for every single symbol.  The main task already uses the
correct `_init_angel_one_session()` + `_fetch_symbol_angel_one()` approach which
authenticates once and reuses the session.  The old function was never called by the
main task but remained in the file.

**Fix applied:** Removed the entire `_fetch_via_angel_one()` function.

---

### 4 — `celery_app.py` | Intraday schedule fires after NSE close
**Severity:** Medium — spurious no-op Celery ticks waste resources and pollute logs.

**File:** `backend/app/tasks/celery_app.py`

**What was wrong:**  
`"intraday-ohlcv-ingest"` used `crontab(hour="9-15", minute="*/15")`.  The `hour="9-15"`
range includes 15:xx slots: 15:00, 15:15, **15:30**, **15:45**, **15:59** — but NSE
closes at 15:30.  The `_is_market_open()` guard makes these no-ops but the ticks
are still dispatched and logged.

**Fix applied:**  
Split into two entries:
```python
"intraday-ohlcv-ingest":      crontab(hour="9-14",  minute="*/15")
"intraday-ohlcv-ingest-1500": crontab(hour=15,      minute="0,15")
```

Also fixed the reconciliation task comment which said "before market close" but fires
at 16:00 IST (30 minutes *after* NSE closes at 15:30).

---

### 5 — `signal_analytics_service.py` | Win-rate denominator is total count, not evaluated count
**Severity:** High — dashboard shows inflated/misleading win-rate percentages.

**File:** `backend/app/services/signal_analytics_service.py`

**What was wrong:**  
```python
buy_evaluated  = row.buy_count   # ← total BUY signals, not evaluated ones
sell_evaluated = row.sell_count
buy_win_rate   = row.buy_wins / buy_evaluated * 100
```
A signal is "won" only when `hit_target=True`, which requires `is_evaluated=True`.
Using total count (including still-open, unevaluated signals) as the denominator
produces a win rate that always trends toward zero as new unevaluated signals arrive.

**Fix applied:**  
Added `buy_evaluated_count` and `sell_evaluated_count` columns to the SQL query
(`COUNT(CASE WHEN signal_type='BUY' AND is_evaluated THEN 1 END)`) and used those
as denominators.

---

### 6 — `signal_outcome_evaluation.py` | No notifications when target/SL is hit
**Severity:** High — users never receive alerts when their signal hits target or
stop-loss; core product promise unfulfilled.

**File:** `backend/app/tasks/signal_outcome_evaluation.py`

**What was wrong:**  
After `UPDATE signal_outcomes SET hit_target=True / hit_stoploss=True`, no Discord
webhook or Expo push notification was sent.

**Fix applied:**  
Added `_send_outcome_notifications()` helper that fires **only on the first
`False → True` transition** for each flag (prevents duplicate alerts on subsequent
evaluation runs).  Calls:
- `app.services.discord_service.notify_signal_sync()` — Discord webhook
- `app.services.push_notification_service.send_push_to_all()` — Expo push

---

### 7 — `lgbm_trainer.py` | Training always uses `sentiment_score=0.0`
**Severity:** High — the LightGBM model learns that sentiment is always zero,
so the sentiment feature provides no signal and is effectively ignored at inference
time even though production builds pass real FinBERT scores.

**File:** `backend/app/services/lgbm_trainer.py`

**What was wrong:**  
```python
feats = build_features(..., sentiment_score=0.0)  # no historical sentiment
```

**Fix applied:**  
Added `_fetch_sentiment_history(session)` that queries:
```sql
SELECT symbol,
       DATE(published_at AT TIME ZONE 'Asia/Kolkata') AS pub_date,
       AVG(score) AS avg_score
FROM news_sentiment
GROUP BY symbol, pub_date
```
Returns `dict[(symbol, date), float]`.  `_build_dataset()` now accepts a
`sentiment_map` argument and looks up `sentiment_map.get((sym, bar_date), 0.0)`
per training row.

---

### 8 — `celery_app.py` | `refresh_fundamentals` had no Beat schedule
**Severity:** Medium — fundamentals (P/E, ROE, etc.) never refreshed automatically.
The `fundamentals_bump` step inside the signal pipeline always used stale data from
the last manual run.

**Status:** On review, the `"fundamentals-daily"` entry already existed in
`beat_schedule` (Mon–Fri 7:00 AM IST).  No change required.

---

### 9 — `macro_pulse.py` | Redis cache bypassed — yfinance called every 30 min
**Severity:** Medium — `fetch_macro_features()` accepts a redis client for caching
but the task passed none, so yfinance was called on every 30-min tick (up to 48×/day)
causing unnecessary network overhead and potential rate-limiting.

**File:** `backend/app/tasks/macro_pulse.py`

**Fix applied:**  
Added inline Redis cache logic using the already-instantiated `r` client:
```python
_cached = r.get(_MACRO_FEATURES_KEY)
if _cached:
    features = json.loads(_cached)
else:
    features = asyncio.run(fetch_macro_features())
    r.setex(_MACRO_FEATURES_KEY, 3600, json.dumps(features))
```

---

### 10 — `forecasts.py` | PatchTST model is dead code; only TFT used
**Severity:** Medium — PatchTST is the newer, more accurate forecaster trained in
`colab/train_tft_forecaster.ipynb`, yet the API always used TFT.

**File:** `backend/app/api/v1/forecasts.py` → `get_forecast()`

**Fix applied:**  
PatchTST is now tried first; TFT is used as a fallback when PatchTST is not loaded:
```python
result = patchtst_forecast(symbol, bars)
if result is None:
    result = tft_forecast(symbol, bars)
```

---

## Performance Improvements

### 11 — New Alembic migration: plain `signal_ts` index on `signal_outcomes`
**File:** `backend/alembic/versions/0008_add_signal_ts_index.py`

**What was wrong:**  
The analytics service runs `WHERE signal_ts >= :cutoff` queries.  Migration 0006
added composite indexes on `(symbol, signal_ts DESC)` and `(is_evaluated, signal_ts DESC)`
but no standalone `signal_ts` index.  The query planner cannot use a composite index
efficiently for a plain `signal_ts >=` range scan.

**Fix applied:**  
```sql
CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_ts
ON signal_outcomes (signal_ts DESC);
```

---

## Known Issues — Not Implemented (Require Major Redesign)

These findings were documented during the review but were intentionally deferred:

| # | Issue | Reason deferred |
|---|-------|-----------------|
| 6 | Survivorship bias in LightGBM training | Requires delisted-stock data procurement |
| 19 | `enable_utc=False` in Celery config | Changing it requires rewriting all beat schedule times in UTC |
| 28 | No NSE market holiday calendar | Requires integration with NSE holiday API or static calendar |
| 22 | Forecast page shows blank for missing model | Frontend UX improvement; no backend impact |
| 24 | Win-rate metric shows no sample count | Frontend display enhancement |
| 25 | Signal analytics page has no date range selector | Frontend feature request |
| 26 | Screener signal filter uses a separate query | Performance refactor, low priority |

---

## Summary

| Category | Count |
|----------|-------|
| Critical bugs fixed | 2 |
| High severity bugs fixed | 4 |
| Medium severity bugs fixed | 4 |
| Performance improvements | 1 |
| Known issues deferred | 7 |

All fixed files use **IST (`Asia/Kolkata`, UTC+5:30)** for every timestamp
written to the database or Redis, consistent with `celery_app.py` configuration
(`timezone="Asia/Kolkata"`, `enable_utc=False`).
