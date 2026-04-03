"""
LAB 05: Демонстрация неконсистентности кэша.
"""

import pytest

from app.infrastructure.cache_keys import order_card_key


@pytest.mark.asyncio
async def test_stale_order_card_when_db_updated_without_invalidation(
    api_client,
    create_order,
    redis_client,
):
    payload = await create_order(
        email_prefix="stale_card",
        total_amount=50.0,
        items=[{"product_name": "Keyboard", "price": 25.0, "quantity": 2}],
    )
    order_id = payload["order_id"]
    cache_key = order_card_key(str(order_id))

    warm_response = await api_client.get(
        f"/api/cache-demo/orders/{order_id}/card",
        params={"use_cache": "true"},
    )
    assert warm_response.status_code == 200
    assert warm_response.json()["source"] == "db"
    assert warm_response.json()["order"]["total_amount"] == 50.0
    assert await redis_client.exists(cache_key) == 1

    mutate_response = await api_client.post(
        f"/api/cache-demo/orders/{order_id}/mutate-without-invalidation",
        json={"new_total_amount": 999.0},
    )
    assert mutate_response.status_code == 200
    assert mutate_response.json()["cache_invalidated"] is False

    stale_response = await api_client.get(
        f"/api/cache-demo/orders/{order_id}/card",
        params={"use_cache": "true"},
    )
    fresh_response = await api_client.get(
        f"/api/cache-demo/orders/{order_id}/card",
        params={"use_cache": "false"},
    )

    assert stale_response.status_code == 200
    assert stale_response.json()["source"] == "cache"
    assert stale_response.json()["order"]["total_amount"] == 50.0

    assert fresh_response.status_code == 200
    assert fresh_response.json()["source"] == "db"
    assert fresh_response.json()["order"]["total_amount"] == 999.0
