"""Доменная сущность пользователя."""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .exceptions import InvalidEmailError


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class User:
    """Доменная сущность пользователя."""

    email: str
    name: str = ""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.email = (self.email or "").strip()
        if not self.email or not EMAIL_REGEX.fullmatch(self.email):
            raise InvalidEmailError(self.email)
