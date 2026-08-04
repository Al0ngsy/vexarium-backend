"""Async SQLAlchemy engine + session factory.

When ``DATABASE_URL`` is set (local Docker compose, Neon, or Render), the app
uses Postgres-backed persistence. When it is empty, the app falls back to the
in-memory repositories so the app still boots and tests stay hermetic.

The engine is created lazily so importing this module never blocks on a
database that may not be running.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

logger = logging.getLogger("vexarium.db")

# Re-export Base so models can import it from a single place.
from .models.trade import Base  # noqa: F401  (declared by the model module)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine | None:
    """Return the shared async engine, creating it lazily if DATABASE_URL set."""
    global _engine, _session_factory
    if _engine is None and settings.database_url:
        # Ensure the URL uses the async driver (asyncpg) for SQLAlchemy.
        url = settings.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        _engine = create_async_engine(url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        logger.info("SQLAlchemy async engine initialized")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    get_engine()
    return _session_factory


async def init_db() -> None:
    """Create tables if they don't exist (dev convenience). Production uses Alembic."""
    engine = get_engine()
    if engine is None:
        return
    from .models.trade import Base  # noqa: F401  ensure models imported

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ensured")


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def db_enabled() -> bool:
    return bool(settings.database_url)
