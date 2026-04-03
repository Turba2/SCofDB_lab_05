"""
LAB 05: Проверка починки через событийную инвалидацию.
"""

import pytest

from app.infrastructure.cache_keys import catalog_key, order_card_key


@pytest.mark.asyncio
async def test_order_card_is_fresh_after_event_invalidation(
    api_client,
    create_order,
    redis_client,
):
    payload = await create_order(
        email_prefix="fresh_card",
        total_amount=80.0,
        items=[{"product_name": "Mouse", "price": 40.0, "quantity": 2}],
    )
    order_id = payload["order_id"]
    order_key = order_card_key(str(order_id))
    catalog_cache_key = catalog_key()

    warm_card = await api_client.get(
        f"/api/cache-demo/orders/{order_id}/card",
        params={"use_cache": "true"},
    )
    warm_catalog = await api_client.get(
        "/api/cache-demo/catalog",
        params={"use_cache": "true"},
    )

    assert warm_card.status_code == 200
    assert warm_catalog.status_code == 200
    assert await redis_client.exists(order_key) == 1
    assert await redis_client.exists(catalog_cache_key) == 1

    mutate_response = await api_client.post(
        f"/api/cache-demo/orders/{order_id}/mutate-with-event-invalidation",
        json={"new_total_amount": 1234.0},
    )
    assert mutate_response.status_code == 200
    assert mutate_response.json()["cache_invalidated"] is True
    assert order_key in mutate_response.json()["invalidated_keys"]
    assert catalog_cache_key in mutate_response.json()["invalidated_keys"]
    assert await redis_client.exists(order_key) == 0
    assert await redis_client.exists(catalog_cache_key) == 0

    refreshed_response = await api_client.get(
        f"/api/cache-demo/orders/{order_id}/card",
        params={"use_cache": "true"},
    )
    cached_again_response = await api_client.get(
        f"/api/cache-demo/orders/{order_id}/card",
        params={"use_cache": "true"},
    )

    assert refreshed_response.status_code == 200
    assert refreshed_response.json()["source"] == "db"
    assert refreshed_response.json()["order"]["total_amount"] == 1234.0

    assert cached_again_response.status_code == 200
    assert cached_again_response.json()["source"] == "cache"
    assert cached_again_response.json()["order"]["total_amount"] == 1234.0
