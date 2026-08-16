"""
CONVOY — async database session management.

One engine per process, sessions created per unit of work. The engine holds the
connection pool; creating engines per request would open a new pool every time
and exhaust Postgres' connection limit within seconds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # a connection idle through a laptop sleep is dead;
                          # pre_ping detects and replaces it transparently
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,   # keeps objects usable after commit, which
                              # matters when we hand them to the WebSocket hub
    autoflush=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session


async def check_connection() -> tuple[bool, str]:
    """Preflight probe. Returns (ok, message).

    Called once at startup so an unreachable database produces ONE clear line
    of guidance instead of a stack trace per inbound message. A backend that
    cannot reach its database has nothing useful to do, and discovering that
    from the first line of output rather than the hundredth matters when you
    are standing in front of a projector.
    """
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def dispose_engine() -> None:
    await engine.dispose()
