"""Application settings loaded from environment variables."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ────────────────────────────────────────────────────────────
    environment: str = "development"

    # ── Database ───────────────────────────────────────────────────────────────
    db_user: str
    db_password: str
    db_name: str
    db_host: str = "localhost"
    db_port: int = 5432

    @property
    def database_url(self) -> str:
        """Async URL for SQLAlchemy (asyncpg driver) — used by FastAPI."""
        from urllib.parse import quote_plus
        return (
            f"postgresql+asyncpg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync URL for SQLAlchemy (psycopg2 driver) — used by Celery workers."""
        from urllib.parse import quote_plus
        return (
            f"postgresql+psycopg2://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str

    @property
    def redis_url(self) -> str:
        from urllib.parse import quote_plus
        return f"redis://:{quote_plus(self.redis_password)}@{self.redis_host}:{self.redis_port}/0"

    # ── Auth / JWT ─────────────────────────────────────────────────────────────
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    bcrypt_rounds: int = 12

    # ── Fernet key for TOTP secret encryption ────────────────────────────────
    fernet_key: str

    # ── Invite signing key (32-byte hex → used for HMAC invite tokens) ────────
    invite_signing_key: str

    # ── Discord optional webhook ────────────────────────────────────────────
    discord_webhook_url: str = ""

    # ── Broker API ───────────────────────────────────────────────────────────
    broker_name: str = ""          # "angel_one" | "upstox" | ""

    # Angel One (SmartAPI)
    angel_api_key: str = ""
    angel_api_secret: str = ""
    angel_client_id: str = ""
    angel_mpin: str = ""
    angel_totp_secret: str = ""

    # Upstox
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = ""

    # ── SMTP (optional — leave SMTP_HOST empty to disable email) ─────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "AI Trader <noreply@example.com>"

    # ── ML model artifacts (Google Drive File IDs — set after Colab training) ─
    lstm_gdrive_id: str = ""    # LSTM_GDRIVE_ID from .env
    tft_gdrive_id: str  = ""    # TFT_GDRIVE_ID  from .env

    # ── Logo.dev ───────────────────────────────────────────────────────────────
    logo_dev_token: str = ""    # pk_...  (public token for img.logo.dev)

    # ── LLM Explainability ────────────────────────────────────────────────────
    # Cascade: groq → gemini → local → disabled
    explainability_backend: str = "groq"   # "groq" | "gemini" | "local" | "disabled"
    groq_api_key: str = ""
    gemini_api_key: str = ""
    local_llm_path: str = ""               # path to GGUF model file for llama-cpp-python
    explainability_confidence_threshold: float = 0.60  # only explain signals above this

    # ── IP Rotator ─────────────────────────────────────────────────────────────
    ip_rotator_backend: str = "none"       # "proxy_list" | "none"
    ip_rotator_proxy_list: str = ""        # newline-separated proxy URIs
    ip_rotator_strategy: str = "round_robin"  # "round_robin" | "random"

    # ── Rate Limiting ──────────────────────────────────────────────────────────
    rate_limit_default: str = "60/minute"
    rate_limit_screener: str = "30/minute"
    rate_limit_prices: str = "120/minute"

    # ── CORS & URLs ───────────────────────────────────────────────────────────
    allowed_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.allowed_origins.split(",")]
        # In development, automatically allow all localhost Vite dev-server ports
        # so that the app works regardless of which port Vite auto-selects.
        if self.environment == "development":
            for port in range(3000, 3010):
                origins.append(f"http://localhost:{port}")
        return list(dict.fromkeys(origins))  # deduplicate, preserve order


settings = Settings()
