"""
LAB 04: Сравнение подходов
1) FOR UPDATE (решение из lab_02)
2) Idempotency-Key + middleware (lab_04)
"""

import pytest


@pytest.mark.asyncio
async def test_compare_for_update_and_idempotency_behaviour(
    api_client,
    create_order,
    get_paid_events,
):
    for_update_order = await create_order(email_prefix="compare_for_update")
    idempotent_order = await create_order(email_prefix="compare_idem")

    for_update_payload = {
        "order_id": str(for_update_order["order_id"]),
        "mode": "for_update",
    }
    idempotent_payload = {
        "order_id": str(idempotent_order["order_id"]),
        "mode": "unsafe",
    }

    for_update_first = await api_client.post(
        "/api/payments/retry-demo",
        json=for_update_payload,
    )
    for_update_second = await api_client.post(
        "/api/payments/retry-demo",
        json=for_update_payload,
    )

    idempotent_first = await api_client.post(
        "/api/payments/retry-demo",
        json=idempotent_payload,
        headers={"Idempotency-Key": "compare-key-789"},
    )
    idempotent_second = await api_client.post(
        "/api/payments/retry-demo",
        json=idempotent_payload,
        headers={"Idempotency-Key": "compare-key-789"},
    )

    for_update_paid_events = await get_paid_events(for_update_order["order_id"])
    idempotent_paid_events = await get_paid_events(idempotent_order["order_id"])

    assert for_update_first.status_code == 200
    assert for_update_second.status_code == 200
    assert for_update_first.json()["success"] is True
    assert for_update_second.json()["success"] is False
    assert len(for_update_paid_events) == 1

    assert idempotent_first.status_code == 200
    assert idempotent_second.status_code == 200
    assert idempotent_first.json()["success"] is True
    assert idempotent_second.json() == idempotent_first.json()
    assert idempotent_second.headers["X-Idempotency-Replayed"] == "true"
    assert len(idempotent_paid_events) == 1

    print("FOR UPDATE vs Idempotency-Key:")
    print("  FOR UPDATE:")
    print(f"    first={for_update_first.json()}")
    print(f"    second={for_update_second.json()}")
    print(f"    paid_events={len(for_update_paid_events)}")
    print("  Idempotency-Key:")
    print(f"    first={idempotent_first.json()}")
    print(f"    second={idempotent_second.json()}")
    print(f"    replayed={idempotent_second.headers['X-Idempotency-Replayed']}")
    print(f"    paid_events={len(idempotent_paid_events)}")
    print("  Difference: FOR UPDATE protects the database state, while")
    print("  Idempotency-Key protects the API contract and lets the client")
    print("  receive the same successful response on retry.")
