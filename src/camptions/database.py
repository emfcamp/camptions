"""Database connection and session management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

log = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Lightweight "add column if missing" migrations. We don't run Alembic;
# create_all() only creates new tables, so columns added after a venue table
# already exists need a one-off ALTER. Each entry is run on startup and a
# duplicate-column OperationalError is swallowed.
_COLUMN_ADDS: list[tuple[str, str, str]] = [
    ("venues", "transcription_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("venues", "stream_url", "TEXT"),
]


async def init_db() -> None:
    """Initialize database tables and apply lightweight migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, column, ddl in _COLUMN_ADDS:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                log.info("Added column %s.%s", table, column)
            except OperationalError as e:
                # SQLite raises this when the column already exists; harmless.
                if "duplicate column" not in str(e).lower():
                    raise


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions (for use outside FastAPI)."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
