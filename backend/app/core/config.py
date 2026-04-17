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
        """Async URL for SQLAlchemy (asyncpg driver)."""
        from urllib.parse import quote_plus
        return (
            f"postgresql+asyncpg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
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
