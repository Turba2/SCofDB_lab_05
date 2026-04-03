"""Реализация репозиториев с использованием SQLAlchemy."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user import User
from app.domain.order import Order, OrderItem, OrderStatus, OrderStatusChange


def _db_uuid(session: AsyncSession, value: uuid.UUID) -> Any:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return str(value)
    return value


def _to_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _db_decimal(session: AsyncSession, value: Decimal) -> Any:
    decimal_value = _to_decimal(value)
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return float(decimal_value)
    return decimal_value


def _db_datetime(session: AsyncSession, value: datetime) -> Any:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return value.isoformat()
    return value


def _row_to_user(row: Mapping[str, Any]) -> User:
    return User(
        id=_to_uuid(row["id"]),
        email=row["email"],
        name=row["name"],
        created_at=_to_datetime(row["created_at"]),
    )


def _row_to_order_item(row: Mapping[str, Any]) -> OrderItem:
    return OrderItem(
        id=_to_uuid(row["id"]),
        order_id=_to_uuid(row["order_id"]),
        product_name=row["product_name"],
        price=_to_decimal(row["price"]),
        quantity=row["quantity"],
    )


def _row_to_status_change(row: Mapping[str, Any]) -> OrderStatusChange:
    return OrderStatusChange(
        id=_to_uuid(row["id"]),
        order_id=_to_uuid(row["order_id"]),
        status=OrderStatus(row["status"]),
        changed_at=_to_datetime(row["changed_at"]),
    )


class UserRepository:
    """Репозиторий для User."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, user: User) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO users (id, email, name, created_at)
                VALUES (:id, :email, :name, :created_at)
                ON CONFLICT(id) DO UPDATE
                SET email = excluded.email,
                    name = excluded.name,
                    created_at = excluded.created_at
                """
            ),
            {
                "id": _db_uuid(self.session, user.id),
                "email": user.email,
                "name": user.name,
                "created_at": _db_datetime(self.session, user.created_at),
            },
        )

    async def find_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.session.execute(
            text(
                """
                SELECT id, email, name, created_at
                FROM users
                WHERE id = :id
                """
            ),
            {"id": _db_uuid(self.session, user_id)},
        )
        row = result.mappings().first()
        return _row_to_user(row) if row is not None else None

    async def find_by_email(self, email: str) -> Optional[User]:
        result = await self.session.execute(
            text(
                """
                SELECT id, email, name, created_at
                FROM users
                WHERE email = :email
                """
            ),
            {"email": email.strip()},
        )
        row = result.mappings().first()
        return _row_to_user(row) if row is not None else None

    async def find_all(self) -> List[User]:
        result = await self.session.execute(
            text(
                """
                SELECT id, email, name, created_at
                FROM users
                ORDER BY created_at, id
                """
            )
        )
        return [_row_to_user(row) for row in result.mappings().all()]


class OrderRepository:
    """Репозиторий для Order."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, order: Order) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO orders (id, user_id, status, total_amount, created_at)
                VALUES (:id, :user_id, :status, :total_amount, :created_at)
                ON CONFLICT(id) DO UPDATE
                SET user_id = excluded.user_id,
                    status = excluded.status,
                    total_amount = excluded.total_amount,
                    created_at = excluded.created_at
                """
            ),
            {
                "id": _db_uuid(self.session, order.id),
                "user_id": _db_uuid(self.session, order.user_id),
                "status": order.status.value,
                "total_amount": _db_decimal(self.session, order.total_amount),
                "created_at": _db_datetime(self.session, order.created_at),
            },
        )

        await self.session.execute(
            text("DELETE FROM order_items WHERE order_id = :order_id"),
            {"order_id": _db_uuid(self.session, order.id)},
        )
        for item in order.items:
            await self.session.execute(
                text(
                    """
                    INSERT INTO order_items (
                        id,
                        order_id,
                        product_name,
                        price,
                        quantity,
                        subtotal
                    )
                    VALUES (
                        :id,
                        :order_id,
                        :product_name,
                        :price,
                        :quantity,
                        :subtotal
                    )
                    """
                ),
                {
                    "id": _db_uuid(self.session, item.id),
                    "order_id": _db_uuid(self.session, order.id),
                    "product_name": item.product_name,
                    "price": _db_decimal(self.session, item.price),
                    "quantity": item.quantity,
                    "subtotal": _db_decimal(self.session, item.subtotal),
                },
            )

        await self.session.execute(
            text("DELETE FROM order_status_history WHERE order_id = :order_id"),
            {"order_id": _db_uuid(self.session, order.id)},
        )
        for change in order.status_history:
            await self.session.execute(
                text(
                    """
                    INSERT INTO order_status_history (id, order_id, status, changed_at)
                    VALUES (:id, :order_id, :status, :changed_at)
                    """
                ),
                {
                    "id": _db_uuid(self.session, change.id),
                    "order_id": _db_uuid(self.session, order.id),
                    "status": change.status.value,
                    "changed_at": _db_datetime(self.session, change.changed_at),
                },
            )

    async def find_by_id(self, order_id: uuid.UUID) -> Optional[Order]:
        order_result = await self.session.execute(
            text(
                """
                SELECT id, user_id, status, total_amount, created_at
                FROM orders
                WHERE id = :id
                """
            ),
            {"id": _db_uuid(self.session, order_id)},
        )
        order_row = order_result.mappings().first()
        if order_row is None:
            return None

        items_result = await self.session.execute(
            text(
                """
                SELECT id, order_id, product_name, price, quantity
                FROM order_items
                WHERE order_id = :order_id
                ORDER BY id
                """
            ),
            {"order_id": _db_uuid(self.session, order_id)},
        )
        history_result = await self.session.execute(
            text(
                """
                SELECT id, order_id, status, changed_at
                FROM order_status_history
                WHERE order_id = :order_id
                ORDER BY changed_at, id
                """
            ),
            {"order_id": _db_uuid(self.session, order_id)},
        )

        order = object.__new__(Order)
        order.id = _to_uuid(order_row["id"])
        order.user_id = _to_uuid(order_row["user_id"])
        order.status = OrderStatus(order_row["status"])
        order.total_amount = _to_decimal(order_row["total_amount"])
        order.created_at = _to_datetime(order_row["created_at"])
        order.items = [_row_to_order_item(row) for row in items_result.mappings().all()]
        order.status_history = [
            _row_to_status_change(row) for row in history_result.mappings().all()
        ]
        return order

    async def find_by_user(self, user_id: uuid.UUID) -> List[Order]:
        result = await self.session.execute(
            text(
                """
                SELECT id
                FROM orders
                WHERE user_id = :user_id
                ORDER BY created_at, id
                """
            ),
            {"user_id": _db_uuid(self.session, user_id)},
        )
        orders = []
        for row in result.mappings().all():
            order = await self.find_by_id(_to_uuid(row["id"]))
            if order is not None:
                orders.append(order)
        return orders

    async def find_all(self) -> List[Order]:
        result = await self.session.execute(
            text(
                """
                SELECT id
                FROM orders
                ORDER BY created_at, id
                """
            )
        )
        orders = []
        for row in result.mappings().all():
            order = await self.find_by_id(_to_uuid(row["id"]))
            if order is not None:
                orders.append(order)
        return orders
