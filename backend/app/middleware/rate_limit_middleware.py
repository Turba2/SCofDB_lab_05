"""Rate limiting middleware for LAB 05."""

from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.cache_keys import payment_rate_limit_key
from app.infrastructure.redis_client import get_redis


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-based rate limiting для endpoint оплаты.

    Цель:
    - защита от DDoS/шторма запросов;
    - защита от случайных повторных кликов пользователя.
    """

    def __init__(self, app, limit_per_window: int = 5, window_seconds: int = 10):
        super().__init__(app)
        self.limit_per_window = limit_per_window
        self.window_seconds = window_seconds
        self.redis = get_redis()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method != "POST" or not self._is_payment_endpoint(request.url.path):
            return await call_next(request)

        subject = self._build_subject(request)
        key = payment_rate_limit_key(f"{subject}:{request.url.path}")

        current_count = await self.redis.incr(key)
        if current_count == 1:
            await self.redis.expire(key, self.window_seconds)

        ttl = await self.redis.ttl(key)
        if ttl < 0:
            ttl = self.window_seconds

        remaining = max(0, self.limit_per_window - current_count)
        headers = {
            "X-RateLimit-Limit": str(self.limit_per_window),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(ttl),
        }

        if current_count > self.limit_per_window:
            headers["Retry-After"] = str(ttl)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded for payment endpoint"},
                headers=headers,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value
        return response

    @staticmethod
    def _is_payment_endpoint(path: str) -> bool:
        return (
            path == "/api/payments/retry-demo"
            or path == "/api/payments/pay"
            or (path.startswith("/api/orders/") and path.endswith("/pay"))
        )

    @staticmethod
    def _build_subject(request: Request) -> str:
        explicit = request.headers.get("X-RateLimit-Subject") or request.headers.get(
            "X-Test-Client"
        )
        if explicit:
            return explicit
        if request.client is not None and request.client.host:
            return request.client.host
        return "anonymous"
