"""Database connection and session management."""

import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@db:5432/marketplace"
)

SQLITE_MEMORY_URL = "sqlite+aiosqlite:///:memory:"
SQLITE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        status TEXT NOT NULL,
        total_amount REAL NOT NULL,
        created_at TIMESTAMP NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        product_name TEXT NOT NULL,
        price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        subtotal REAL NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_status_history (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        status TEXT NOT NULL,
        changed_at TIMESTAMP NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL,
        request_method TEXT NOT NULL,
        request_path TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'processing',
        status_code INTEGER,
        response_body TEXT,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        UNIQUE (idempotency_key, request_method, request_path)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_idempotency_keys_lookup
    ON idempotency_keys (idempotency_key, request_method, request_path)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires_at
    ON idempotency_keys (expires_at)
    """,
)

engine_kwargs = {"echo": True}
if DATABASE_URL == SQLITE_MEMORY_URL:
    engine_kwargs["poolclass"] = StaticPool

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
_sqlite_initialized = False
_sqlite_init_lock = asyncio.Lock()


async def _ensure_sqlite_schema() -> None:
    global _sqlite_initialized

    if not DATABASE_URL.startswith("sqlite+aiosqlite"):
        return

    if _sqlite_initialized:
        return

    async with _sqlite_init_lock:
        if _sqlite_initialized:
            return

        async with engine.begin() as conn:
            for ddl in SQLITE_DDL:
                await conn.execute(text(ddl))

        _sqlite_initialized = True


async def get_db() -> AsyncSession:
    """Dependency for getting database session."""
    await _ensure_sqlite_schema()
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
