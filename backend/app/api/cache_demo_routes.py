"""Cache consistency demo endpoints for LAB 05."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.cache_events import CacheInvalidationEventBus, OrderUpdatedEvent
from app.application.cache_service import CacheService
from app.domain.exceptions import OrderNotFoundError
from app.infrastructure.db import get_db


router = APIRouter(prefix="/api/cache-demo", tags=["cache-demo"])


class UpdateOrderRequest(BaseModel):
    """Payload для изменения заказа в demo-сценариях."""

    new_total_amount: float


def get_cache_service(db: AsyncSession = Depends(get_db)) -> CacheService:
    return CacheService(db)


@router.get("/catalog")
async def get_catalog(
    use_cache: bool = True,
    service: CacheService = Depends(get_cache_service),
) -> Any:
    return await service.get_catalog(use_cache=use_cache)


@router.get("/orders/{order_id}/card")
async def get_order_card(
    order_id: uuid.UUID,
    use_cache: bool = True,
    service: CacheService = Depends(get_cache_service),
) -> Any:
    try:
        return await service.get_order_card(str(order_id), use_cache=use_cache)
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/orders/{order_id}/mutate-without-invalidation")
async def mutate_without_invalidation(
    order_id: uuid.UUID,
    payload: UpdateOrderRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    updated_rows = await _update_order_total_amount(db, order_id, payload.new_total_amount)
    if updated_rows == 0:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

    return {
        "success": True,
        "order_id": str(order_id),
        "new_total_amount": payload.new_total_amount,
        "cache_invalidated": False,
        "message": "Order updated in DB without cache invalidation",
    }


@router.post("/orders/{order_id}/mutate-with-event-invalidation")
async def mutate_with_event_invalidation(
    order_id: uuid.UUID,
    payload: UpdateOrderRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    updated_rows = await _update_order_total_amount(db, order_id, payload.new_total_amount)
    if updated_rows == 0:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

    event_bus = CacheInvalidationEventBus(CacheService(db))
    result = await event_bus.publish_order_updated(
        OrderUpdatedEvent(order_id=str(order_id), invalidate_catalog=True)
    )

    return {
        "success": True,
        "order_id": str(order_id),
        "new_total_amount": payload.new_total_amount,
        "cache_invalidated": True,
        "invalidated_keys": result["invalidated_keys"],
        "message": "Order updated and related cache keys invalidated",
    }


async def _update_order_total_amount(
    db: AsyncSession,
    order_id: uuid.UUID,
    new_total_amount: float,
) -> int:
    result = await db.execute(
        text(
            """
            UPDATE orders
            SET total_amount = :new_total_amount
            WHERE id = :order_id
            """
        ),
        {
            "new_total_amount": new_total_amount,
            "order_id": str(order_id),
        },
    )
    await db.commit()
    return result.rowcount or 0
