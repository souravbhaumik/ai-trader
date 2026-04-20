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

- **Real-time quotes** via Angel One and Upstox APIs
- **Historical OHLCV** data with TimescaleDB compression
- **Intraday 15-min candles** in `ohlcv_intraday` (5-day rolling, Angel One → Upstox hybrid)
- **NSE Bhavcopy** daily ingest for EOD data
- **Stock screener** with pagination, search, and signal filters

### 🤖 AI-Powered Signals

- **LightGBM classifier** for BUY/SELL signal generation
- **Technical indicators**: RSI, MACD, Bollinger Bands, ATR, OBV, ADX
- **Intraday signals** at 9:30 AM, 11:00 AM, and 1:00 PM using live 15-min candles
- **LSTM autoencoder** for market anomaly detection
- **TFT Transformer** for 5-day price forecasting
- **FinBERT** sentiment analysis on financial news

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

| Task              | Schedule                   | Purpose                 |
| ----------------- | -------------------------- | ----------------------- |
| Bhavcopy Ingest   | 7:30 PM IST Mon-Fri        | NSE EOD data            |
| Signal Generation | 4:45 PM IST Mon-Fri        | ML signal generation    |
| News Sentiment    | Every 15 min, 9 AM-3:45 PM | News scoring            |
| Model Training    | Saturday 2:00 AM           | Weekly LightGBM retrain |
| Broker Reconnect  | 8:00 AM IST Mon-Fri        | Session refresh         |

Monitor at http://localhost:5555 (Flower)

---

## Documentation

| Document                                 | Description                              |
| ---------------------------------------- | ---------------------------------------- |
| [DESIGN.md](DESIGN.md)                   | System architecture and technical design |
| [GETTING_STARTED.md](GETTING_STARTED.md) | Detailed setup and configuration guide   |
| [API.md](API.md)                         | Complete API reference                   |
| [DATABASE.md](DATABASE.md)               | Database schema documentation            |

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

### Cloudflare Tunnel Setup

For stable webhook URLs (required for broker postbacks):

```bash
# Install cloudflared
# Configure tunnel to proxy to backend:8000
cloudflared tunnel create ai-trader
cloudflared tunnel route dns ai-trader your-domain.com
```

### Environment Adjustments

```bash
ENVIRONMENT=production
ALLOWED_ORIGINS=https://your-domain.com
FRONTEND_URL=https://your-domain.com
```

### Docker Production

```bash
docker compose -f docker-compose.prod.yml up -d
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
