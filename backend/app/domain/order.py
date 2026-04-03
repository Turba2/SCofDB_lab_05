"""Доменные сущности заказа."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from .exceptions import (
    OrderAlreadyPaidError,
    OrderCancelledError,
    InvalidQuantityError,
    InvalidPriceError,
    InvalidAmountError,
)


class OrderStatus(str, Enum):
    """Возможные статусы заказа."""

    CREATED = "created"
    PAID = "paid"
    CANCELLED = "cancelled"
    SHIPPED = "shipped"
    COMPLETED = "completed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class OrderItem:
    """Позиция заказа."""

    product_name: str
    price: Decimal
    quantity: int
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    order_id: Optional[uuid.UUID] = None

    def __post_init__(self) -> None:
        self.product_name = self.product_name.strip()
        if not self.product_name:
            raise ValueError("Product name cannot be empty")

        self.price = Decimal(str(self.price))
        if self.price < 0:
            raise InvalidPriceError(self.price)

        if self.quantity <= 0:
            raise InvalidQuantityError(self.quantity)

    @property
    def subtotal(self) -> Decimal:
        return self.price * self.quantity


@dataclass
class OrderStatusChange:
    """Изменение статуса заказа."""

    order_id: uuid.UUID
    status: OrderStatus
    changed_at: datetime = field(default_factory=utc_now)
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        self.status = OrderStatus(self.status)


@dataclass
class Order:
    """Доменная сущность заказа."""

    user_id: uuid.UUID
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: OrderStatus = OrderStatus.CREATED
    total_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    created_at: datetime = field(default_factory=utc_now)
    items: List[OrderItem] = field(default_factory=list)
    status_history: List[OrderStatusChange] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = OrderStatus(self.status)
        self.total_amount = Decimal(str(self.total_amount))
        if self.total_amount < 0:
            raise InvalidAmountError(self.total_amount)

        if self.items:
            self._recalculate_total()

        if not self.status_history:
            self.status_history.append(
                OrderStatusChange(order_id=self.id, status=self.status)
            )

    def add_item(self, product_name: str, price: Decimal, quantity: int) -> OrderItem:
        if self.status == OrderStatus.CANCELLED:
            raise OrderCancelledError(self.id)

        item = OrderItem(
            product_name=product_name,
            price=price,
            quantity=quantity,
            order_id=self.id,
        )
        self.items.append(item)
        self._recalculate_total()
        return item

    def pay(self) -> None:
        if self.status == OrderStatus.CANCELLED:
            raise OrderCancelledError(self.id)

        if self.status == OrderStatus.PAID or self._has_ever_been_paid():
            raise OrderAlreadyPaidError(self.id)

        self._change_status(OrderStatus.PAID)

    def cancel(self) -> None:
        if self._has_ever_been_paid():
            raise OrderAlreadyPaidError(self.id)

        if self.status != OrderStatus.CANCELLED:
            self._change_status(OrderStatus.CANCELLED)

    def ship(self) -> None:
        if self.status != OrderStatus.PAID:
            raise ValueError("Order must be paid before shipping")

        self._change_status(OrderStatus.SHIPPED)

    def complete(self) -> None:
        if self.status != OrderStatus.SHIPPED:
            raise ValueError("Order must be shipped before completion")

        self._change_status(OrderStatus.COMPLETED)

    def _recalculate_total(self) -> None:
        self.total_amount = sum((item.subtotal for item in self.items), Decimal("0"))
        if self.total_amount < 0:
            raise InvalidAmountError(self.total_amount)

    def _change_status(self, status: OrderStatus) -> None:
        self.status = status
        self.status_history.append(OrderStatusChange(order_id=self.id, status=status))

    def _has_ever_been_paid(self) -> bool:
        return any(change.status == OrderStatus.PAID for change in self.status_history)
