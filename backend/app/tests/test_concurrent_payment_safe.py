"""
Тесты для демонстрации РЕШЕНИЯ race condition.

Эти тесты подтверждают, что pay_order_safe() с REPEATABLE READ
и FOR UPDATE допускает только одну успешную оплату.
"""

import asyncio
import os
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.payment_service import PaymentService
from app.domain.exceptions import OrderAlreadyPaidError


DATABASE_URL = os.getenv(
    "CONCURRENT_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/marketplace",
)


async def _create_test_order(
    session_factory: async_sessionmaker[AsyncSession],
    label: str,
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
                "email": f"{label}_{user_id.hex[:12]}@example.com",
                "name": f"{label.title()} Test User",
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
    payload = await _create_test_order(session_factory, "safe")
    try:
        yield payload["order_id"]
    finally:
        await _cleanup_test_order(session_factory, payload)


@pytest.mark.asyncio
async def test_concurrent_payment_safe_prevents_race_condition(
    session_factory,
    test_order,
):
    order_id = test_order

    async def payment_attempt():
        async with session_factory() as session:
            service = PaymentService(session, unsafe_delay=0.0, safe_delay=0.0)
            return await service.pay_order_safe(order_id)

    results = await asyncio.gather(
        payment_attempt(),
        payment_attempt(),
        return_exceptions=True,
    )

    success_results = [result for result in results if not isinstance(result, Exception)]
    errors = [result for result in results if isinstance(result, Exception)]

    assert len(success_results) == 1, "Ожидалась одна успешная оплата"
    assert len(errors) == 1, "Ожидалась одна неудачная попытка"
    assert isinstance(errors[0], OrderAlreadyPaidError)

    async with session_factory() as session:
        history = await PaymentService(session, unsafe_delay=0.0).get_payment_history(
            order_id
        )

    assert len(history) == 1, "Ожидалась 1 запись об оплате"

    print("✅ RACE CONDITION PREVENTED!")
    print(f"Order {order_id} was paid only ONCE:")
    print(f"  - {history[0]['changed_at']}: status = {history[0]['status']}")
    print(f"Second attempt was rejected: {errors[0]}")


@pytest.mark.asyncio
async def test_concurrent_payment_safe_with_explicit_timing(session_factory, test_order):
    order_id = test_order
    timestamps: dict[str, float] = {}

    async def first_transaction():
        timestamps["first_start"] = time.perf_counter()
        async with session_factory() as session:
            service = PaymentService(session, unsafe_delay=0.0, safe_delay=1.0)
            result = await service.pay_order_safe(order_id)
        timestamps["first_end"] = time.perf_counter()
        return result

    async def second_transaction():
        await asyncio.sleep(0.1)
        timestamps["second_start"] = time.perf_counter()
        try:
            async with session_factory() as session:
                service = PaymentService(session, unsafe_delay=0.0, safe_delay=0.0)
                return await service.pay_order_safe(order_id)
        finally:
            timestamps["second_end"] = time.perf_counter()

    first_result, second_result = await asyncio.gather(
        first_transaction(),
        second_transaction(),
        return_exceptions=True,
    )

    assert not isinstance(first_result, Exception)
    assert isinstance(second_result, OrderAlreadyPaidError)
    assert timestamps["second_end"] >= timestamps["first_end"]
    assert timestamps["second_end"] - timestamps["second_start"] >= 0.75

    async with session_factory() as session:
        history = await PaymentService(session, unsafe_delay=0.0).get_payment_history(
            order_id
        )

    assert len(history) == 1


@pytest.mark.asyncio
async def test_concurrent_payment_safe_multiple_orders(session_factory):
    payload_1 = await _create_test_order(session_factory, "safe_multi_1")
    payload_2 = await _create_test_order(session_factory, "safe_multi_2")

    try:
        async def pay_one(order_id: uuid.UUID):
            async with session_factory() as session:
                service = PaymentService(session, unsafe_delay=0.0, safe_delay=0.4)
                return await service.pay_order_safe(order_id)

        results = await asyncio.gather(
            pay_one(payload_1["order_id"]),
            pay_one(payload_2["order_id"]),
        )

        assert len(results) == 2

        async with session_factory() as session:
            service = PaymentService(session, unsafe_delay=0.0)
            history_1 = await service.get_payment_history(payload_1["order_id"])
            history_2 = await service.get_payment_history(payload_2["order_id"])

        assert len(history_1) == 1
        assert len(history_2) == 1
    finally:
        await _cleanup_test_order(session_factory, payload_1)
        await _cleanup_test_order(session_factory, payload_2)
