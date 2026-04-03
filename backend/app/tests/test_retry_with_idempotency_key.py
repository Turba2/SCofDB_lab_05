"""
LAB 04: Проверка идемпотентного повтора запроса.

Цель:
При повторном запросе с тем же Idempotency-Key вернуть
кэшированный результат без повторного списания.
"""

import json

import pytest


@pytest.mark.asyncio
async def test_retry_with_same_key_returns_cached_response(
    api_client,
    create_order,
    get_paid_events,
    get_idempotency_record,
):
    order_payload = await create_order(email_prefix="idem_same_key")
    order_id = order_payload["order_id"]
    request_payload = {"order_id": str(order_id), "mode": "unsafe"}
    headers = {"Idempotency-Key": "fixed-key-123"}

    first_response = await api_client.post(
        "/api/payments/retry-demo",
        json=request_payload,
        headers=headers,
    )
    second_response = await api_client.post(
        "/api/payments/retry-demo",
        json=request_payload,
        headers=headers,
    )

    paid_events = await get_paid_events(order_id)
    record = await get_idempotency_record("fixed-key-123")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.headers["X-Idempotency-Replayed"] == "false"
    assert second_response.headers["X-Idempotency-Replayed"] == "true"
    assert second_response.json() == first_response.json()
    assert len(paid_events) == 1

    assert record is not None
    assert record["status"] == "completed"
    assert record["status_code"] == 200
    assert json.loads(record["response_body"]) == first_response.json()


@pytest.mark.asyncio
async def test_same_key_different_payload_returns_conflict(
    api_client,
    create_order,
    get_paid_events,
):
    first_order = await create_order(email_prefix="idem_conflict_1")
    second_order = await create_order(email_prefix="idem_conflict_2")
    headers = {"Idempotency-Key": "conflict-key-456"}

    first_response = await api_client.post(
        "/api/payments/retry-demo",
        json={"order_id": str(first_order["order_id"]), "mode": "unsafe"},
        headers=headers,
    )
    conflict_response = await api_client.post(
        "/api/payments/retry-demo",
        json={"order_id": str(second_order["order_id"]), "mode": "unsafe"},
        headers=headers,
    )

    first_order_paid_events = await get_paid_events(first_order["order_id"])
    second_order_paid_events = await get_paid_events(second_order["order_id"])

    assert first_response.status_code == 200
    assert conflict_response.status_code == 409
    assert "different payload" in conflict_response.json()["detail"].lower()
    assert len(first_order_paid_events) == 1
    assert len(second_order_paid_events) == 0
