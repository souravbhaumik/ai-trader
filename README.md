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

## Coming in Phase 2

- Market data ingestion (TimescaleDB hypertables, WebSocket price streaming)
- ML inference pipeline (LightGBM, TFT, FinBERT, LSTM Autoencoder)
- Trade execution (paper + live via Angel One / Upstox)
- Celery workers + Redis task queue
- TOTP setup endpoint for admin
- Discord webhook alerts
