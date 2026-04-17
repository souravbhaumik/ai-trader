"""SQLAlchemy engines and session factories.

Two engines share the same models:
  * ``engine``     — async (asyncpg) for FastAPI request handlers
  * ``sync_engine`` — sync (psycopg2) for Celery workers

Do NOT use psycopg2 directly in tasks; always acquire a session through
``get_sync_session()`` so SQLAlchemy manages the connection pool.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlmodel import SQLModel

from app.core.config import settings

# ── Async engine (FastAPI) ─────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal: sessionmaker = sessionmaker(  # type: ignore[call-overload]
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Sync engine (Celery workers) ──────────────────────────────────────────
sync_engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SyncSessionLocal: sessionmaker = sessionmaker(  # type: ignore[call-overload]
    bind=sync_engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """Context manager that yields a synchronous SQLAlchemy Session.

    Usage inside a Celery task::

        with get_sync_session() as session:
            session.execute(text(sql), params)
            session.commit()
    """
    session: Session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
