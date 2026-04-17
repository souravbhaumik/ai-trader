"""Alembic environment configuration.

Uses psycopg2 (sync) for migrations; the FastAPI app uses asyncpg (async).
"""
from __future__ import annotations

import os
from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine, pool

# Import all models so Alembic autogenerate can see the metadata
from app.models.user import User  # noqa: F401
from app.models.user_settings import UserSettings  # noqa: F401
from app.models.user_invites import UserInvite  # noqa: F401
from app.models.refresh_tokens import RefreshToken  # noqa: F401
from sqlmodel import SQLModel

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Build the sync URL directly — bypasses configparser's % interpolation issue
_DB_URL = (
    f"postgresql+psycopg2://{quote_plus(os.environ['DB_USER'])}:"
    f"{quote_plus(os.environ['DB_PASSWORD'])}@"
    f"{os.environ.get('DB_HOST', 'localhost')}:"
    f"{os.environ.get('DB_PORT', '5432')}/"
    f"{os.environ['DB_NAME']}"
)


def run_migrations_offline() -> None:
    context.configure(
        url=_DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_DB_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

