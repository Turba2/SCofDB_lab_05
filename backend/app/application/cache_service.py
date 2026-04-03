"""Cache service for LAB 05."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import OrderNotFoundError
from app.infrastructure.cache_keys import catalog_key, order_card_key
from app.infrastructure.redis_client import get_redis


class CacheService:
    """Redis-backed caching for catalog and order card endpoints."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        catalog_ttl_seconds: int = 60,
        order_card_ttl_seconds: int = 60,
    ) -> None:
        self.session = session
        self.redis = get_redis()
        self.catalog_ttl_seconds = catalog_ttl_seconds
        self.order_card_ttl_seconds = order_card_ttl_seconds

    async def get_catalog(self, *, use_cache: bool = True) -> dict[str, Any]:
        key = catalog_key()
        if use_cache:
            cached = await self.redis.get(key)
            if cached is not None:
                return {
                    "source": "cache",
                    "cache_key": key,
                    "use_cache": True,
                    "items": json.loads(cached),
                }

        items = await self._load_catalog_from_db()
        if use_cache:
            await self.redis.set(key, self._serialize(items), ex=self.catalog_ttl_seconds)

        return {
            "source": "db",
            "cache_key": key,
            "use_cache": use_cache,
            "items": items,
        }

    async def get_order_card(
        self,
        order_id: str,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        key = order_card_key(order_id)
        if use_cache:
            cached = await self.redis.get(key)
            if cached is not None:
                return {
                    "source": "cache",
                    "cache_key": key,
                    "use_cache": True,
                    "order": json.loads(cached),
                }

        order = await self._load_order_card_from_db(order_id)
        if use_cache:
            await self.redis.set(key, self._serialize(order), ex=self.order_card_ttl_seconds)

        return {
            "source": "db",
            "cache_key": key,
            "use_cache": use_cache,
            "order": order,
        }

    async def invalidate_order_card(self, order_id: str) -> None:
        await self.redis.delete(order_card_key(order_id))

    async def invalidate_catalog(self) -> None:
        await self.redis.delete(catalog_key())

    async def _load_catalog_from_db(self) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text(
                """
                SELECT
                    product_name,
                    COUNT(*) AS rows_count,
                    COALESCE(SUM(quantity), 0) AS units_sold,
                    COALESCE(SUM(subtotal), 0) AS revenue
                FROM order_items
                GROUP BY product_name
                ORDER BY product_name
                """
            )
        )
        items: list[dict[str, Any]] = []
        for row in result.mappings():
            items.append(
                {
                    "product_name": row["product_name"],
                    "rows_count": int(row["rows_count"]),
                    "units_sold": int(row["units_sold"] or 0),
                    "revenue": float(row["revenue"] or 0),
                }
            )
        return items

    async def _load_order_card_from_db(self, order_id: str) -> dict[str, Any]:
        order_result = await self.session.execute(
            text(
                """
                SELECT id, user_id, status, total_amount, created_at
                FROM orders
                WHERE id = :order_id
                """
            ),
            {"order_id": order_id},
        )
        order_row = order_result.mappings().first()
        if order_row is None:
            raise OrderNotFoundError(order_id)

        items_result = await self.session.execute(
            text(
                """
                SELECT id, product_name, price, quantity, subtotal
                FROM order_items
                WHERE order_id = :order_id
                ORDER BY product_name, id
                """
            ),
            {"order_id": order_id},
        )

        items = []
        for row in items_result.mappings():
            items.append(
                {
                    "id": str(row["id"]),
                    "product_name": row["product_name"],
                    "price": float(row["price"] or 0),
                    "quantity": int(row["quantity"] or 0),
                    "subtotal": float(row["subtotal"] or 0),
                }
            )

        return {
            "id": str(order_row["id"]),
            "user_id": str(order_row["user_id"]),
            "status": order_row["status"],
            "total_amount": float(order_row["total_amount"] or 0),
            "created_at": self._serialize_scalar(order_row["created_at"]),
            "items": items,
        }

    @classmethod
    def _serialize(cls, payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, default=cls._serialize_scalar)

    @staticmethod
    def _serialize_scalar(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value
