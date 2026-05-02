# AI-Trader Codebase Audit Report (Final Review)

**Date:** Saturday, 2 May 2026
**Status:** ALL ISSUES RESOLVED ✅

---

## 1. [CRITICAL] Broken Realised P&L — **FIXED ✅**
**File:** `backend/app/services/live_trade_service.py` → `_enforce_daily_loss_limit()`

### Fix Applied
P&L now uses actual fill values:
```sql
COALESCE(SUM(CASE WHEN direction = 'SELL' THEN avg_fill_price * qty ELSE 0 END), 0)
- COALESCE(SUM(CASE WHEN direction = 'BUY'  THEN avg_fill_price * qty ELSE 0 END), 0)
AS day_net_pnl
```
- Uses `avg_fill_price * qty` (actual traded value), not `price` (limit price)
- Boundary uses IST midnight

---

## 2. [HIGH] Portfolio Value "Denominator Problem" — **FIXED ✅**
**File:** `backend/app/services/live_trade_service.py` → `_enforce_daily_loss_limit()`

### Fix Applied
Portfolio denominator replaced with **gross BUY capital ever deployed**:
```sql
SELECT COALESCE(SUM(avg_fill_price * qty), 0) AS gross_buy_capital
FROM live_orders WHERE direction = 'BUY' AND status = 'COMPLETE'
```
- Stable — never collapses to zero after withdrawals or profitable exits

---

## 3. [HIGH] Missing Forecast Persistence — **FULLY IMPLEMENTED ✅**

### What Was Built

**Migration** `alembic/versions/0010_forecast_history.py`:
- New `forecast_history` TimescaleDB table
- Stores `predicted_prices[]`, `actual_prices[]`, `rmse`, `mae`, `directional_acc` per (symbol, model_version, forecast_date)
- `UNIQUE (symbol, model_version, forecast_date)` — fully idempotent
- 3 indexes for symbol/date lookups and unevaluated row scanning

**`app/tasks/forecast_tasks.py`** — two Celery tasks:
- `persist_daily_forecasts` — runs 16:00 IST Mon–Fri, saves forecasts for all active symbols after EOD bar is confirmed
- `evaluate_forecast_accuracy` — runs 06:30 IST Mon–Fri, fills `actual_prices` from `ohlcv_daily` and computes RMSE/MAE/directional accuracy for any forecast whose full 5-day horizon has passed

**`app/api/v1/forecasts.py`** updates:
- `GET /forecasts/{symbol}` now fire-and-forgets a background thread persist on every API call (ensures accumulation even without the beat task)
- `GET /forecasts/{symbol}/history` — new endpoint returning the last N rows with full accuracy metrics
- `ForecastAccuracyRow` Pydantic schema added

**`app/tasks/celery_app.py`** updates:
- `forecast-persist-daily` beat entry at 16:00 IST
- `forecast-evaluate-daily` beat entry at 06:30 IST

---

## 4. [MEDIUM] Lookahead Bias Guard — **IMPROVED ✅**
**File:** `backend/app/services/feature_engineer.py` → `build_features_for_symbol()`

### Fix Applied
During 09:15–15:30 IST Mon–Fri, today's partial OHLCV bar is excluded:
```python
if is_market_hours:
    # Exclude today's partial bar (ts < today_midnight)
    rows = query_with_date_filter(today_midnight)
```
- Prevents indicator drift from live intraday prices vs. EOD-trained model
- Returns features tagged with `_live_market_hours = 1.0`

---

## Summary

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | P&L formula measures slippage not trading loss | Critical | ✅ Fixed |
| 2 | MTM denominator collapses to zero | High | ✅ Fixed |
| 3 | `forecast_history` + RMSE evaluation task | High | ✅ Implemented |
| 4 | Lookahead bias in feature builder during market hours | Medium | ✅ Improved |

**All audit items resolved.** Next planned work: MTM (mark-to-market) real-time valuation and ARF (Adaptive Random Forest) online training.
