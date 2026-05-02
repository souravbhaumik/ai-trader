# AI Trader

> **Algorithmic Trading Platform for Indian Equity Markets**

AI Trader is a full-stack automated trading system for NSE/BSE markets. It combines technical analysis, machine learning signal generation, news sentiment analysis, and multi-broker integration to provide both paper and live trading capabilities.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)
![React](https://img.shields.io/badge/React-18+-61DAFB)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

### 📊 Market Data

- **Real-time quotes** via NSE India unofficial API (3 concurrent bulk index calls — Nifty 50, Next 50, Midcap 100)
- **Historical OHLCV** data with TimescaleDB compression
- **Intraday 15-min candles** in `ohlcv_intraday` (5-day rolling, Angel One → Upstox hybrid)
- **NSE Bhavcopy** daily ingest for EOD data
- **Stock screener** with pagination, search, and signal filters

### 🤖 AI-Powered Signals

- **LightGBM classifier** for BUY/SELL signal generation
- **Technical indicators**: RSI, MACD, Bollinger Bands, ATR, OBV, ADX
- **Intraday signals** every 15 min during market hours using live 15-min candles
- **LSTM autoencoder** for market anomaly detection
- **PatchTST Transformer** for 5-day price forecasting (TFT as fallback)
- **Forecast self-evaluation**: RMSE/MAE/directional accuracy tracked in `forecast_history`
- **FinBERT** sentiment analysis with zero-centred polarity scoring (P(pos) − P(neg))

### 📰 News & Sentiment

- Multi-source aggregation: Google News, Yahoo Finance, RSS feeds
- spaCy NER for news → symbol mapping
- Real-time sentiment scoring and caching
- On-demand live analysis endpoint

### 💼 Trading

- **Paper trading** with ₹10,00,000 virtual capital and 0.03% brokerage
- **Live trading** via Angel One SmartAPI and Upstox REST API
- **Risk management**: Daily loss limits, position sizing caps
- **Order webhooks** for real-time fill notifications

### 🔒 Security

- JWT authentication with httpOnly refresh tokens
- TOTP two-factor authentication (required for admins)
- Fernet encryption for broker credentials
- Rate limiting and brute-force protection
- Email OTP gate for live trading enablement

### 📱 Notifications

- Discord webhook integration for signals and trades
- Expo push notifications for mobile app
- WebSocket streaming for live prices and signals

### 🛠 Operations

- Admin dashboard for user management
- Pipeline monitoring with task status tracking
- MLflow experiment tracking
- Flower-based Celery monitoring

---

## Tech Stack

| Layer              | Technologies                                      |
| ------------------ | ------------------------------------------------- |
| **Backend**        | Python 3.11, FastAPI, SQLModel, Celery, Redis     |
| **Database**       | PostgreSQL 16, TimescaleDB 2                      |
| **ML/AI**          | LightGBM, PyTorch (LSTM, TFT), FinBERT, spaCy     |
| **Frontend**       | React 18, TypeScript, Vite, Zustand, Recharts     |
| **Infrastructure** | Docker Compose, Cloudflare Tunnel, MLflow, Flower |

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Git

### 1. Clone & Configure

```bash
git clone https://github.com/yourusername/ai-trader.git
cd ai-trader

# Copy example environment file
cp .env.example .env

# Edit .env with your settings (database passwords, JWT secret, etc.)
nano .env
```

### 2. Generate Security Keys

```bash
# JWT Secret (64-character hex)
python -c "import secrets; print(secrets.token_hex(32))"

# Fernet Key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Invite Signing Key (32-byte hex)
python -c "import secrets; print(secrets.token_hex(16))"
```

### 3. Start Services

```bash
docker compose up -d
```

This starts:

- **PostgreSQL + TimescaleDB** on port 5433
- **Redis** on port 6379
- **FastAPI Backend** on port 8000
- **Vite Frontend** on port 3000
- **Celery Worker + Beat** (background tasks)
- **Flower** on port 5555
- **MLflow** on port 5001

### 4. Create Admin User

```bash
docker compose exec backend python scripts/seed_admin_user.py
```

### 5. Access the Application

| Service     | URL                        |
| ----------- | -------------------------- |
| Frontend    | http://localhost:3000      |
| Backend API | http://localhost:8000      |
| API Docs    | http://localhost:8000/docs |
| Flower      | http://localhost:5555      |
| MLflow      | http://localhost:5001      |

---

## Project Structure

```
ai-trader/
├── backend/                 # FastAPI application
│   ├── app/
│   │   ├── api/v1/         # REST endpoints
│   │   ├── brokers/        # Angel One, Upstox, yfinance
│   │   ├── core/           # Config, database, security
│   │   ├── lib/            # IP rotator, utilities
│   │   ├── models/         # SQLModel ORM models
│   │   ├── services/       # Business logic
│   │   └── tasks/          # Celery background tasks
│   ├── alembic/            # Database migrations
│   └── requirements.txt
├── frontend/               # React application
│   ├── src/
│   │   ├── pages/          # Route components
│   │   ├── components/     # Reusable UI
│   │   └── store/          # Zustand state
│   └── package.json
├── colab/                  # ML training notebooks
├── docker-compose.yml      # Service orchestration
└── .env.example            # Environment template
```

---

## Configuration

### Required Environment Variables

```bash
# Database
DB_USER=aitrader
DB_PASSWORD=<strong_password>
DB_NAME=aitrader

# Redis
REDIS_PASSWORD=<strong_password>

# Security
JWT_SECRET_KEY=<64_char_hex>
FERNET_KEY=<base64_fernet_key>
INVITE_SIGNING_KEY=<32_byte_hex>
```

### Optional: Broker Integration

```bash
# Angel One
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_MPIN=your_mpin
ANGEL_TOTP_SECRET=your_totp_base32

# Upstox
UPSTOX_API_KEY=your_api_key
UPSTOX_API_SECRET=your_secret
```

### Optional: Notifications

```bash
# Discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Email (SMTP)
SMTP_HOST=smtp.gmail.com
SMTP_USER=your@email.com
SMTP_PASSWORD=app_password
```

See [GETTING_STARTED.md](GETTING_STARTED.md) for complete configuration guide.

---

## API Overview

### Authentication

```
POST /api/v1/auth/login          # Login → access_token + refresh cookie
POST /api/v1/auth/refresh        # Refresh access token
POST /api/v1/auth/logout         # Logout + invalidate tokens
POST /api/v1/auth/register       # Register with invite token
```

### Market Data

```
GET  /api/v1/prices/indices      # Nifty 50, Sensex, etc.
GET  /api/v1/prices/{symbol}/quote
GET  /api/v1/prices/{symbol}/history
GET  /api/v1/screener            # Paginated stock list
```

### Signals & Trading

```
GET  /api/v1/signals             # AI-generated signals
POST /api/v1/portfolio/paper/orders    # Paper trade
POST /api/v1/portfolio/live/orders     # Live order (requires OTP enablement)
```

### WebSocket

```
ws://host/api/v1/ws/prices?token=<jwt>&symbols=RELIANCE.NS,TCS.NS
ws://host/api/v1/ws/signals?token=<jwt>
```

Full API documentation: http://localhost:8000/docs

---

## Scheduled Tasks

| Task | Schedule | Purpose |
| --- | --- | --- |
| Bhavcopy Ingest | 7:30 PM IST Mon–Fri | NSE EOD data |
| EOD Ingest | 4:30 PM IST Mon–Fri | EOD summary (Angel One / NSE) |
| Pre-market Signals | 8:30 AM IST Mon–Fri | ML signal generation |
| Post-market Signals | 4:45 PM IST Mon–Fri | EOD signal generation |
| Intraday OHLCV | Every 15 min, 9:15–15:30 IST | Hybrid candle ingest |
| Intraday Signals | Every 15 min, 9:30–15:15 IST | Live intraday signals |
| News Sentiment | Every 15 min, 9 AM–3:45 PM | FinBERT scoring |
| Breaking News | Every 2 min, 9 AM–4 PM | Fast-path news scanner |
| Forecast Persist | 4:00 PM IST Mon–Fri | Save PatchTST/TFT forecasts to DB |
| Forecast Evaluate | 6:30 AM IST Mon–Fri | Compute RMSE/MAE on matured forecasts |
| Signal Outcomes | 5:00 PM IST Mon–Fri | EOD signal accuracy evaluation |
| Model Training | Saturday 2:00 AM | Weekly LightGBM retrain |
| Broker Reconnect | 8:00 AM IST Mon–Fri | Angel One / Upstox session refresh |
| Macro Pulse | Every 30 min, 9 AM–4 PM | Macro regime detection |
| EOD Reconciliation | 4:00 PM IST Mon–Fri | Live order sync from broker |

Monitor at http://localhost:5555 (Flower)

---

## Documentation

| Document               | Description                              |
| ---------------------- | ---------------------------------------- |
| [DESIGN.md](DESIGN.md) | System architecture, pipeline design, roadmap & bug log |
| [API.md](API.md)       | Complete API reference                   |
| [DATABASE.md](DATABASE.md) | Database schema documentation        |

---

## Detailed Setup Guide

### Prerequisites

| Software | Version | Purpose |
| --- | --- | --- |
| Docker | 20.10+ | Containerization |
| Docker Compose | 2.0+ | Service orchestration |
| Git | 2.30+ | Version control |

**System requirements**: 4 GB RAM minimum (8 GB recommended), 10 GB storage.

### Environment Configuration

Edit `.env` after copying from `.env.example`:

**Security keys** (generate once):
```bash
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('INVITE_SIGNING_KEY=' + secrets.token_hex(16))"
```

**Required `.env` entries:**
```bash
DB_USER=aitrader
DB_PASSWORD=<strong_password>
DB_NAME=aitrader
REDIS_PASSWORD=<strong_password>
JWT_SECRET_KEY=<64_char_hex>
FERNET_KEY=<base64_fernet_key>
INVITE_SIGNING_KEY=<32_byte_hex>
ALLOWED_ORIGINS=http://localhost:3000
FRONTEND_URL=http://localhost:3000
ENVIRONMENT=development
```

### Broker Configuration

#### Angel One (Primary broker + live trading)

1. Sign up at https://www.angelone.in/ and complete KYC
2. Create API app at https://smartapi.angelone.in/
3. Enable TOTP in the Angel One app → Settings → TOTP → note the base32 secret

```bash
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_MPIN=your_4digit_pin
ANGEL_TOTP_SECRET=your_totp_base32
```

#### Upstox (Intraday candle fallback)

1. Create API app at https://account.upstox.com/developer/apps
2. Set **Redirect URI**: `http://localhost:8000/api/v1/broker-credentials/upstox/callback`

```bash
UPSTOX_API_KEY=your_api_key
UPSTOX_API_SECRET=your_api_secret
UPSTOX_REDIRECT_URI=http://localhost:8000/api/v1/broker-credentials/upstox/callback
```

One-time OAuth: call `GET /api/v1/broker-credentials/upstox/authorize`, open the returned URL in a browser, log in. Token auto-stored. Expires midnight IST; system notifies at 7:30 AM if re-auth needed.

### Initial Setup

```bash
# 1. Start all services
docker compose up -d

# 2. Verify all containers running
docker compose ps

# 3. Create admin user
docker compose exec backend python scripts/seed_admin_user.py
# Default: admin@aitrader.local / admin123 — CHANGE IMMEDIATELY

# 4. Populate stock universe (Admin → Pipeline → Populate Universe, or:)
docker compose exec backend python scripts/populate_universe.py
```

### ML Model Setup

Ensure ≥200 days of OHLCV data, then train LightGBM:

```bash
# Via Admin UI: Admin → Pipeline → Train Model
# Or via CLI:
docker compose exec celery-worker celery -A app.tasks.celery_app call app.tasks.ml_training.train_model
```

For deep learning models (LSTM/TFT) trained on Google Colab:
```bash
LSTM_GDRIVE_ID=your_lstm_file_id
TFT_GDRIVE_ID=your_tft_file_id
# Then: Admin → Models → Download from Drive
```

LLM signal explainability:
```bash
EXPLAINABILITY_BACKEND=groq   # groq|gemini|local|disabled
GROQ_API_KEY=your_groq_api_key
```

### Notification Setup

```bash
# Discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxx/yyy

# Email (Gmail App Password — not account password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your.email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=AI Trader <your.email@gmail.com>
```

### IP Rotation (Optional — for rate limit bypass)

```bash
IP_ROTATOR_BACKEND=proxy_list
IP_ROTATOR_STRATEGY=round_robin  # or random
IP_ROTATOR_PROXY_LIST="socks5://user:pass@proxy1.example.com:1080
http://user:pass@proxy2.example.com:8080"
```

---

## Development

### Backend Development

```bash
# Install dependencies
cd backend
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start dev server
uvicorn app.main:app --reload --port 8000

# Run Celery worker
celery -A app.tasks.celery_app worker --loglevel=info
```

### Frontend Development

```bash
cd frontend
npm install
npm run dev
```

### Running Tests

```bash
# Backend
cd backend
pytest

# Frontend
cd frontend
npm test
```

---

## Production Deployment

### Security Checklist

- [ ] Change all default passwords (DB, Redis, admin user)
- [ ] Use strong, unique secrets for JWT/Fernet/Invite keys
- [ ] Enable HTTPS via Cloudflare Tunnel or reverse proxy
- [ ] Set `ENVIRONMENT=production`
- [ ] Update `ALLOWED_ORIGINS` with production domain

### Cloudflare Tunnel Setup

For stable webhook URLs (required for broker postbacks):

```bash
# Install cloudflared (Linux)
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

# Authenticate and create tunnel
cloudflared tunnel login
cloudflared tunnel create ai-trader

# Configure (~/.cloudflared/config.yml)
# tunnel: <tunnel-id>
# ingress:
#   - hostname: api.yourdomain.com
#     service: http://localhost:8000
#   - hostname: app.yourdomain.com
#     service: http://localhost:3000
#   - service: http_status:404

cloudflared tunnel route dns ai-trader api.yourdomain.com
cloudflared tunnel route dns ai-trader app.yourdomain.com
cloudflared tunnel run ai-trader
```

### Production Environment Variables

```bash
ENVIRONMENT=production
ALLOWED_ORIGINS=https://app.yourdomain.com
FRONTEND_URL=https://app.yourdomain.com
RATE_LIMIT_DEFAULT=30/minute
RATE_LIMIT_SCREENER=15/minute
RATE_LIMIT_PRICES=60/minute
```

### Register Broker Webhooks

After Cloudflare Tunnel is running, register with brokers:
- **Angel One Developer Portal** → postback URL: `https://api.yourdomain.com/api/v1/webhooks/broker/order-update`
- **Upstox Developer Portal** → similar webhook registration

---

## Troubleshooting

### Common Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `connection refused to postgres:5432` | DB not ready | Wait or check `docker compose logs postgres` |
| `NOAUTH Authentication required` (Redis) | Wrong Redis password | Check `REDIS_PASSWORD` in `.env` |
| `kombu.exceptions.OperationalError` (Celery) | Redis not running | `docker compose logs redis` |
| `Network Error / CORS` (frontend) | Backend unreachable | Verify `ALLOWED_ORIGINS` includes frontend URL |
| `Invalid credentials or TOTP` (broker) | Wrong creds or clock skew | Verify base32 TOTP secret; sync system clock |

### Log Commands

```bash
docker compose logs -f                        # all services
docker compose logs -f --timestamps backend   # backend with timestamps
docker compose logs --tail=100 celery-worker  # last 100 lines of worker
```

### Service Management

```bash
docker compose restart backend          # restart single service
docker compose down && docker compose up -d --build  # full rebuild
```

### Database Operations

```bash
# Connect to PostgreSQL
docker compose exec postgres psql -U aitrader -d aitrader

# Run migrations manually
docker compose exec backend alembic upgrade head

# Reset database (CAUTION: destroys all data)
docker compose down -v && docker compose up -d
```

### Cache Management

```bash
# Flush all Redis keys (CAUTION)
docker compose exec redis redis-cli -a $REDIS_PASSWORD FLUSHALL

# Clear specific pattern
docker compose exec redis redis-cli -a $REDIS_PASSWORD KEYS "screener*" | xargs redis-cli -a $REDIS_PASSWORD DEL
```

### Verify Data Pipeline

```bash
# Check OHLCV data in DB
docker compose exec postgres psql -U aitrader -d aitrader -c \
  "SELECT symbol, COUNT(*) FROM ohlcv_daily GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 10;"

# Check Redis sentiment cache
docker compose exec redis redis-cli -a $REDIS_PASSWORD KEYS "sentiment:*" | wc -l
# Expected: >50 keys if news task ran successfully

# Trigger news task manually
docker compose exec celery-worker celery -A app.tasks.celery_app call \
  app.tasks.news_sentiment.fetch_news_sentiment
```

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Disclaimer

This software is for educational purposes only. Algorithmic trading involves significant financial risk. The authors are not responsible for any financial losses incurred through the use of this software. Always test thoroughly with paper trading before using real capital.
