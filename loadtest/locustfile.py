"""
Locust template for LAB 05 RPS measurements.

Run:
locust -f loadtest/locustfile.py --host=http://localhost:8082
"""

import os

from locust import HttpUser, task, between


class CacheUser(HttpUser):
    wait_time = between(0.1, 0.5)
    order_id = os.getenv("ORDER_ID", "PUT_REAL_ORDER_ID_HERE")
    use_cache = os.getenv("USE_CACHE", "true").lower()

    @task(3)
    def get_catalog(self):
        self.client.get(f"/api/cache-demo/catalog?use_cache={self.use_cache}")

    @task(2)
    def get_order_card(self):
        self.client.get(
            f"/api/cache-demo/orders/{self.order_id}/card?use_cache={self.use_cache}"
        )
