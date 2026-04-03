"""
LAB 04: Демонстрация проблемы retry без идемпотентности.

Сценарий:
1) Клиент отправил запрос на оплату.
2) До получения ответа "сеть оборвалась".
3) Клиент повторил тот же запрос БЕЗ Idempotency-Key.
4) В unsafe-режиме повтор может привести к двойной оплате.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_retry_without_idempotency_can_double_pay(create_order, get_paid_events):
    order_payload = await create_order(email_prefix="retry_no_key")
    order_id = order_payload["order_id"]
    request_payload = {"order_id": str(order_id), "mode": "unsafe"}

    async def payment_attempt():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.post("/api/payments/retry-demo", json=request_payload)

    first_response, second_response = await asyncio.gather(
        payment_attempt(),
        payment_attempt(),
    )

    paid_events = await get_paid_events(order_id)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(paid_events) >= 2

    print("Retry without idempotency:")
    print(f"  order_id={order_id}")
    print(f"  attempts=2")
    print(f"  first_response={first_response.json()}")
    print(f"  second_response={second_response.json()}")
    print(f"  paid_events={len(paid_events)}")
    print("  Problem: one client intention was processed more than once.")
