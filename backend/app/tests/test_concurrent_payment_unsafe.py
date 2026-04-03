"""
Тесты для демонстрации ПРОБЛЕМЫ race condition.

Эти тесты должны проходить, подтверждая, что pay_order_unsafe()
может записать две оплаты одного и того же заказа.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.payment_service import PaymentService


DATABASE_URL = os.getenv(
    "CONCURRENT_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/marketplace",
)


async def _create_test_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, uuid.UUID]:
    user_id = uuid.uuid4()
    order_id = uuid.uuid4()

    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO users (id, email, name, created_at)
                VALUES (:id, :email, :name, NOW())
                """
            ),
            {
                "id": user_id,
                "email": f"unsafe_{user_id.hex[:12]}@example.com",
                "name": "Unsafe Test User",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO orders (id, user_id, status, total_amount, created_at)
                VALUES (:id, :user_id, 'created', 0, NOW())
                """
            ),
            {"id": order_id, "user_id": user_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO order_status_history (id, order_id, status, changed_at)
                VALUES (:id, :order_id, 'created', NOW())
                """
            ),
            {"id": uuid.uuid4(), "order_id": order_id},
        )
        await session.commit()

    return {"user_id": user_id, "order_id": order_id}


async def _cleanup_test_order(
    session_factory: async_sessionmaker[AsyncSession],
    payload: dict[str, uuid.UUID],
) -> None:
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM order_status_history WHERE order_id = :order_id"),
            {"order_id": payload["order_id"]},
        )
        await session.execute(
            text("DELETE FROM order_items WHERE order_id = :order_id"),
            {"order_id": payload["order_id"]},
        )
        await session.execute(
            text("DELETE FROM orders WHERE id = :order_id"),
            {"order_id": payload["order_id"]},
        )
        await session.execute(
            text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": payload["user_id"]},
        )
        await session.commit()


@pytest.fixture(scope="session")
async def session_factory():
    engine = create_async_engine(DATABASE_URL, echo=False)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL недоступен для concurrent tests: {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def test_order(session_factory):
    payload = await _create_test_order(session_factory)
    try:
        yield payload["order_id"]
    finally:
        await _cleanup_test_order(session_factory, payload)


@pytest.mark.asyncio
async def test_concurrent_payment_unsafe_demonstrates_race_condition(
    session_factory,
    test_order,
):
    order_id = test_order

    async def payment_attempt():
        async with session_factory() as session:
            service = PaymentService(session, unsafe_delay=0.35)
            return await service.pay_order_unsafe(order_id)

    results = await asyncio.gather(
        payment_attempt(),
        payment_attempt(),
        return_exceptions=True,
    )

    async with session_factory() as session:
        history = await PaymentService(session, unsafe_delay=0.0).get_payment_history(
            order_id
        )

    assert all(not isinstance(result, Exception) for result in results)
    assert len(history) == 2, "Ожидалось 2 записи об оплате (RACE CONDITION)"

    print("⚠️ RACE CONDITION DETECTED!")
    print(f"Order {order_id} was paid TWICE:")
    for record in history:
        print(f"  - {record['changed_at']}: status = {record['status']}")


@pytest.mark.asyncio
async def test_concurrent_payment_unsafe_both_succeed(session_factory, test_order):
    order_id = test_order

    async def payment_attempt():
        async with session_factory() as session:
            service = PaymentService(session, unsafe_delay=0.35)
            return await service.pay_order_unsafe(order_id)

    results = await asyncio.gather(
        payment_attempt(),
        payment_attempt(),
        return_exceptions=True,
    )

    success_count = sum(1 for result in results if not isinstance(result, Exception))
    assert success_count == 2

    async with session_factory() as session:
        history = await PaymentService(session, unsafe_delay=0.0).get_payment_history(
            order_id
        )

    assert len(history) == 2
