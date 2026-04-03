"""Pytest configuration and fixtures."""

import os
import asyncio
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

# Set test database URL BEFORE any app imports.
TEST_DB_PATH = Path("/tmp/scofdb_lab_05_test.sqlite3")
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
if "REDIS_URL" not in os.environ:
    os.environ["REDIS_URL"] = "memory://lab05-tests"

from app.infrastructure.db import DATABASE_URL, SessionLocal
from app.infrastructure.redis_client import get_redis, reset_redis_client
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    """Create test database engine."""
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
    )
    
    # Create tables
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                total_amount REAL NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS order_items (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                subtotal REAL NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS order_status_history (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                changed_at TIMESTAMP NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """))
        await conn.execute(text("""
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
        """))
    
    yield engine
    await engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture(scope="session")
async def test_session_factory(test_engine):
    """Create test session factory."""
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def db_session(test_session_factory):
    """Create a database session for tests."""
    async with test_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def sample_user_id():
    """Create a sample user ID."""
    return uuid.uuid4()


@pytest.fixture(autouse=True)
async def clear_database(test_engine):
    """Keep the shared SQLite test database isolated between tests."""

    async def _truncate() -> None:
        async with SessionLocal() as session:
            await session.execute(text("DELETE FROM idempotency_keys"))
            await session.execute(text("DELETE FROM order_status_history"))
            await session.execute(text("DELETE FROM order_items"))
            await session.execute(text("DELETE FROM orders"))
            await session.execute(text("DELETE FROM users"))
            await session.commit()
        await get_redis().flushdb()

    await _truncate()
    yield
    await _truncate()


@pytest.fixture
async def api_client(test_engine):
    """HTTP client bound to the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture
def create_order():
    """Factory for quickly preparing an order in created status."""

    async def _create_order(
        *,
        email_prefix: str = "retry",
        total_amount: float = 100.0,
        items: list[dict] | None = None,
    ) -> dict[str, uuid.UUID]:
        user_id = uuid.uuid4()
        order_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        items = items or [
            {
                "product_name": "Demo Product",
                "price": Decimal("25.00"),
                "quantity": 2,
            }
        ]

        async with SessionLocal() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO users (id, email, name, created_at)
                    VALUES (:id, :email, :name, :created_at)
                    """
                ),
                {
                    "id": str(user_id),
                    "email": f"{email_prefix}_{user_id.hex[:12]}@example.com",
                    "name": f"{email_prefix.title()} User",
                    "created_at": now,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO orders (id, user_id, status, total_amount, created_at)
                    VALUES (:id, :user_id, 'created', :total_amount, :created_at)
                    """
                ),
                {
                    "id": str(order_id),
                    "user_id": str(user_id),
                    "total_amount": total_amount,
                    "created_at": now,
                },
            )
            for item in items:
                price = Decimal(str(item["price"]))
                quantity = int(item["quantity"])
                subtotal = price * quantity
                await session.execute(
                    text(
                        """
                        INSERT INTO order_items (
                            id,
                            order_id,
                            product_name,
                            price,
                            quantity,
                            subtotal
                        )
                        VALUES (
                            :id,
                            :order_id,
                            :product_name,
                            :price,
                            :quantity,
                            :subtotal
                        )
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "order_id": str(order_id),
                        "product_name": item["product_name"],
                        "price": float(price),
                        "quantity": quantity,
                        "subtotal": float(subtotal),
                    },
                )
            await session.execute(
                text(
                    """
                    INSERT INTO order_status_history (id, order_id, status, changed_at)
                    VALUES (:id, :order_id, 'created', :changed_at)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "order_id": str(order_id),
                    "changed_at": now,
                },
            )
            await session.commit()

        return {"user_id": user_id, "order_id": order_id, "items": items}

    return _create_order


@pytest.fixture
def get_paid_events():
    """Factory for reading paid events from history."""

    async def _get_paid_events(order_id: uuid.UUID) -> list[dict]:
        async with SessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, order_id, status, changed_at
                    FROM order_status_history
                    WHERE order_id = :order_id AND status = 'paid'
                    ORDER BY changed_at, id
                    """
                ),
                {"order_id": str(order_id)},
            )
            return [
                {
                    "id": row.id,
                    "order_id": row.order_id,
                    "status": row.status,
                    "changed_at": row.changed_at,
                }
                for row in result.mappings()
            ]

    return _get_paid_events


@pytest.fixture
def get_idempotency_record():
    """Factory for inspecting cached idempotency entries."""

    async def _get_record(key: str, path: str = "/api/payments/retry-demo") -> dict | None:
        async with SessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        idempotency_key,
                        request_method,
                        request_path,
                        request_hash,
                        status,
                        status_code,
                        response_body,
                        created_at,
                        updated_at,
                        expires_at
                    FROM idempotency_keys
                    WHERE idempotency_key = :key
                      AND request_method = 'POST'
                      AND request_path = :path
                    """
                ),
                {"key": key, "path": path},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    return _get_record


@pytest.fixture(scope="session", autouse=True)
def reset_redis_singleton():
    """Ensure test suite uses a fresh cached Redis client."""
    reset_redis_client()
    yield
    reset_redis_client()


@pytest.fixture
async def redis_client():
    """Access the Redis-compatible client used by the application."""
    client = get_redis()
    await client.flushdb()
    yield client
    await client.flushdb()


@pytest.fixture
def get_order_row():
    """Factory for reading raw order state from the database."""

    async def _get_order_row(order_id: uuid.UUID) -> dict | None:
        async with SessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, user_id, status, total_amount, created_at
                    FROM orders
                    WHERE id = :order_id
                    """
                ),
                {"order_id": str(order_id)},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    return _get_order_row
