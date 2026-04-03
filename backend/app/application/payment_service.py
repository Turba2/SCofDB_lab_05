"""Сервис для демонстрации конкурентных оплат.

Этот модуль содержит два метода оплаты:
1. pay_order_unsafe() - небезопасная реализация (READ COMMITTED без блокировок)
2. pay_order_safe() - безопасная реализация (REPEATABLE READ + FOR UPDATE)
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import OrderAlreadyPaidError, OrderNotFoundError


class PaymentService:
    """Сервис для обработки платежей с разными уровнями изоляции."""

    def __init__(
        self,
        session: AsyncSession,
        unsafe_delay: float = 0.2,
        safe_delay: float = 0.0,
    ):
        self.session = session
        self.unsafe_delay = unsafe_delay
        self.safe_delay = safe_delay

    def _db_uuid(self, value: uuid.UUID) -> Any:
        if self.session.bind is not None and self.session.bind.dialect.name == "sqlite":
            return str(value)
        return value

    def _is_sqlite(self) -> bool:
        return self.session.bind is not None and self.session.bind.dialect.name == "sqlite"

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    async def pay_order_unsafe(self, order_id: uuid.UUID) -> dict:
        """
        НЕБЕЗОПАСНАЯ реализация оплаты заказа.
        
        Использует READ COMMITTED (по умолчанию) без блокировок.
        ЛОМАЕТСЯ при конкурентных запросах - может привести к двойной оплате!
        
        Args:
            order_id: ID заказа для оплаты
            
        Returns:
            dict с информацией о заказе после оплаты
            
        Raises:
            OrderNotFoundError: если заказ не найден
            OrderAlreadyPaidError: если заказ уже оплачен
        """
        try:
            result = await self.session.execute(
                text("SELECT status FROM orders WHERE id = :order_id"),
                {"order_id": self._db_uuid(order_id)},
            )
            status = result.scalar_one_or_none()

            if status is None:
                raise OrderNotFoundError(order_id)

            if status != "created":
                raise OrderAlreadyPaidError(order_id)

            # Специально расширяем окно гонки, чтобы воспроизвести проблему надежно.
            if self.unsafe_delay > 0:
                await asyncio.sleep(self.unsafe_delay)

            update_result = await self.session.execute(
                text(
                    """
                    UPDATE orders
                    SET status = 'paid'
                    WHERE id = :order_id AND status = 'created'
                    """
                ),
                {"order_id": self._db_uuid(order_id)},
            )

            await self.session.execute(
                text(
                    """
                    INSERT INTO order_status_history (id, order_id, status, changed_at)
                    VALUES (:id, :order_id, 'paid', :changed_at)
                    """
                ),
                {
                    "id": self._db_uuid(uuid.uuid4()),
                    "order_id": self._db_uuid(order_id),
                    "changed_at": self._utc_now(),
                },
            )

            await self.session.commit()
            return {
                "order_id": order_id,
                "status": "paid",
                "updated_rows": update_result.rowcount or 0,
                "mode": "unsafe",
            }
        except Exception:
            await self.session.rollback()
            raise

    async def pay_order_safe(self, order_id: uuid.UUID) -> dict:
        """
        БЕЗОПАСНАЯ реализация оплаты заказа.
        
        Использует REPEATABLE READ + FOR UPDATE для предотвращения race condition.
        Корректно работает при конкурентных запросах.
        
        Args:
            order_id: ID заказа для оплаты
            
        Returns:
            dict с информацией о заказе после оплаты
            
        Raises:
            OrderNotFoundError: если заказ не найден
            OrderAlreadyPaidError: если заказ уже оплачен
        """
        try:
            if not self._is_sqlite():
                await self.session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
                query = text(
                    """
                    SELECT status
                    FROM orders
                    WHERE id = :order_id
                    FOR UPDATE
                    """
                )
            else:
                # SQLite используется только в учебных тестах. Он не поддерживает
                # REPEATABLE READ + FOR UPDATE, поэтому оставляем совместимый
                # последовательный fallback для API/integration сценариев.
                query = text(
                    """
                    SELECT status
                    FROM orders
                    WHERE id = :order_id
                    """
                )

            result = await self.session.execute(
                query,
                {"order_id": self._db_uuid(order_id)},
            )
            status = result.scalar_one_or_none()

            if status is None:
                raise OrderNotFoundError(order_id)

            if status != "created":
                raise OrderAlreadyPaidError(order_id)

            if self.safe_delay > 0:
                await asyncio.sleep(self.safe_delay)

            update_result = await self.session.execute(
                text(
                    """
                    UPDATE orders
                    SET status = 'paid'
                    WHERE id = :order_id AND status = 'created'
                    """
                ),
                {"order_id": self._db_uuid(order_id)},
            )

            if (update_result.rowcount or 0) != 1:
                raise OrderAlreadyPaidError(order_id)

            await self.session.execute(
                text(
                    """
                    INSERT INTO order_status_history (id, order_id, status, changed_at)
                    VALUES (:id, :order_id, 'paid', :changed_at)
                    """
                ),
                {
                    "id": self._db_uuid(uuid.uuid4()),
                    "order_id": self._db_uuid(order_id),
                    "changed_at": self._utc_now(),
                },
            )

            await self.session.commit()
            return {
                "order_id": order_id,
                "status": "paid",
                "updated_rows": update_result.rowcount or 0,
                "mode": "safe",
            }
        except Exception as exc:
            await self.session.rollback()
            message = str(exc).lower()
            if "could not serialize access" in message or "concurrent update" in message:
                raise OrderAlreadyPaidError(order_id) from exc
            raise

    async def get_payment_history(self, order_id: uuid.UUID) -> list[dict]:
        """
        Получить историю оплат для заказа.
        
        Используется для проверки, сколько раз был оплачен заказ.
        
        Args:
            order_id: ID заказа
            
        Returns:
            Список словарей с записями об оплате
        """
        result = await self.session.execute(
            text(
                """
                SELECT id, order_id, status, changed_at
                FROM order_status_history
                WHERE order_id = :order_id AND status = 'paid'
                ORDER BY changed_at, id
                """
            ),
            {"order_id": self._db_uuid(order_id)},
        )

        history = []
        for row in result.mappings().all():
            history.append(
                {
                    "id": str(row["id"]),
                    "order_id": str(row["order_id"]),
                    "status": row["status"],
                    "changed_at": row["changed_at"],
                }
            )
        return history
