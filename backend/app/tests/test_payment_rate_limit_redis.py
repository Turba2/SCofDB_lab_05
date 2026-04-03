"""
LAB 05: Rate limiting endpoint оплаты через Redis.
"""

import pytest


@pytest.mark.asyncio
async def test_payment_endpoint_rate_limit(api_client, create_order):
    orders = [
        await create_order(
            email_prefix=f"rate_limit_{index}",
            total_amount=30.0,
            items=[{"product_name": f"Item {index}", "price": 30.0, "quantity": 1}],
        )
        for index in range(6)
    ]

    responses = []
    for payload in orders:
        response = await api_client.post(
            "/api/payments/retry-demo",
            json={"order_id": str(payload["order_id"]), "mode": "unsafe"},
            headers={"X-Test-Client": "rate-limit-user-1"},
        )
        responses.append(response)

    allowed = responses[:5]
    blocked = responses[5]

    assert all(response.status_code == 200 for response in allowed)
    assert blocked.status_code == 429

    assert allowed[0].headers["X-RateLimit-Limit"] == "5"
    assert allowed[0].headers["X-RateLimit-Remaining"] == "4"
    assert allowed[-1].headers["X-RateLimit-Remaining"] == "0"

    assert blocked.headers["X-RateLimit-Limit"] == "5"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in blocked.headers
    assert "rate limit exceeded" in blocked.json()["detail"].lower()
