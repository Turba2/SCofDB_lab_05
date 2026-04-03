"""Redis client utilities for LAB 05."""

import os
import time
from functools import lru_cache
from typing import Any

try:
    from redis.asyncio import Redis  # type: ignore
    REDIS_LIB_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised in this environment
    Redis = Any  # type: ignore
    REDIS_LIB_AVAILABLE = False


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class InMemoryRedis:
    """Minimal async Redis-compatible client for tests and offline fallback."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    def _cleanup_key(self, key: str) -> None:
        payload = self._store.get(key)
        if payload is None:
            return
        _, expires_at = payload
        if expires_at is not None and expires_at <= time.time():
            self._store.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def flushdb(self) -> bool:
        self._store.clear()
        return True

    async def get(self, key: str) -> str | None:
        self._cleanup_key(key)
        payload = self._store.get(key)
        return payload[0] if payload is not None else None

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        expires_at = time.time() + ex if ex is not None else None
        self._store[key] = (str(value), expires_at)
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            self._cleanup_key(key)
            if key in self._store:
                self._store.pop(key, None)
                deleted += 1
        return deleted

    async def exists(self, key: str) -> int:
        self._cleanup_key(key)
        return 1 if key in self._store else 0

    async def incr(self, key: str) -> int:
        self._cleanup_key(key)
        payload = self._store.get(key)
        expires_at = payload[1] if payload is not None else None
        current = int(payload[0]) if payload is not None else 0
        current += 1
        self._store[key] = (str(current), expires_at)
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        self._cleanup_key(key)
        payload = self._store.get(key)
        if payload is None:
            return False
        self._store[key] = (payload[0], time.time() + seconds)
        return True

    async def ttl(self, key: str) -> int:
        self._cleanup_key(key)
        payload = self._store.get(key)
        if payload is None:
            return -2
        expires_at = payload[1]
        if expires_at is None:
            return -1
        return max(0, int(expires_at - time.time()))


@lru_cache
def get_redis() -> Redis | InMemoryRedis:
    """Получить singleton Redis client."""
    if REDIS_URL.startswith("memory://") or not REDIS_LIB_AVAILABLE:
        return InMemoryRedis()
    return Redis.from_url(REDIS_URL, decode_responses=True)


def reset_redis_client() -> None:
    """Clear cached Redis client instance."""
    get_redis.cache_clear()
