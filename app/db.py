"""Async SQLAlchemy engine + session factory.

When ``DATABASE_URL`` is set (local Docker compose, Neon, or Render), the app
uses Postgres-backed persistence. When it is empty, the app falls back to the
in-memory repositories so the app still boots and tests stay hermetic.

The engine is created lazily so importing this module never blocks on a
database that may not be running.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .config import settings
from .logging import get_logger


def _ssl_for_url(database_url: str) -> str | bool:
    """Pick the asyncpg ``ssl`` connect arg for a DATABASE_URL.

    An explicit ``ssl=disable`` / ``sslmode=disable`` in the URL (local dev
    Postgres has no TLS) disables SSL; anything else defaults to ``require``
    for Neon/Render, which need TLS.
    """
    q = dict(parse_qsl(urlsplit(database_url).query))
    mode = q.get("ssl", q.get("sslmode", "require"))
    return False if mode == "disable" else "require"


def _asyncpg_safe_url(database_url: str) -> str:
    """Convert a DATABASE_URL into an asyncpg-compatible SQLAlchemy URL.

    - Swaps the driver to ``postgresql+asyncpg``.
    - Drops ALL query params (Neon appends ``channel_binding`` and ``sslmode``,
      neither of which asyncpg's ``connect()`` accepts as a keyword).
    - TLS is configured via ``connect_args`` (see :func:`get_engine` /
      :func:`async_engine_for_url`), not the URL query string.
    """
    url = database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=""))


def async_engine_for_url(database_url: str) -> AsyncEngine:
    """Build an async engine from a DATABASE_URL, configuring TLS for asyncpg."""
    url = _asyncpg_safe_url(database_url)
    return create_async_engine(
        url, pool_pre_ping=True, connect_args={"ssl": _ssl_for_url(database_url)}
    )

logger = get_logger("db")

# Re-export Base so models can import it from a single place.
from .models.trade import Base  # noqa: F401  (declared by the model module)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine | None:
    """Return the shared async engine, creating it lazily if DATABASE_URL set."""
    global _engine, _session_factory
    if _engine is None and settings.database_url:
        _engine = async_engine_for_url(settings.database_url)
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
        logger.info("database engine disposed")
    _engine = None
    _session_factory = None


def db_enabled() -> bool:
    return bool(settings.database_url)
