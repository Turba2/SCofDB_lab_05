"""Event-driven cache invalidation for LAB 05."""

from dataclasses import dataclass

from app.application.cache_service import CacheService


@dataclass
class OrderUpdatedEvent:
    """Событие изменения заказа."""

    order_id: str
    invalidate_catalog: bool = True


class CacheInvalidationEventBus:
    """Tiny event bus that invalidates cache keys on order updates."""

    def __init__(self, cache_service: CacheService) -> None:
        self.cache_service = cache_service

    async def publish_order_updated(self, event: OrderUpdatedEvent) -> dict[str, list[str]]:
        invalidated = []

        await self.cache_service.invalidate_order_card(event.order_id)
        invalidated.append(f"order_card:v1:{event.order_id}")

        if event.invalidate_catalog:
            await self.cache_service.invalidate_catalog()
            invalidated.append("catalog:v1")

        return {"invalidated_keys": invalidated}
