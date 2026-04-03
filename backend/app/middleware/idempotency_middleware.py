"""Idempotency middleware for retry-safe payment requests."""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.db import SessionLocal


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    Middleware для идемпотентности POST-запросов оплаты.

    Реализует API-level защиту от повторной обработки одного и того же
    платежного намерения клиента при повторной отправке запроса.
    """

    TARGET_PATHS = {"/api/payments/retry-demo", "/api/payments/pay"}

    def __init__(self, app, ttl_seconds: int = 24 * 60 * 60):
        super().__init__(app)
        self.ttl_seconds = ttl_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method != "POST" or request.url.path not in self.TARGET_PATHS:
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return await call_next(request)

        raw_body = await request.body()
        request_hash = self.build_request_hash(raw_body)
        request_method = request.method
        request_path = request.url.path

        existing = await self._fetch_record(idempotency_key, request_method, request_path)
        if existing is not None:
            return await self._handle_existing_record(existing, request_hash)

        created = await self._create_processing_record(
            idempotency_key=idempotency_key,
            request_method=request_method,
            request_path=request_path,
            request_hash=request_hash,
        )
        if not created:
            existing = await self._fetch_record(idempotency_key, request_method, request_path)
            if existing is not None:
                return await self._handle_existing_record(existing, request_hash)

        downstream_request = self._clone_request(request, raw_body)

        try:
            response = await call_next(downstream_request)
            response_body = await self._consume_response_body(response)

            await self._mark_completed(
                idempotency_key=idempotency_key,
                request_method=request_method,
                request_path=request_path,
                status_code=response.status_code,
                response_body=response_body.decode("utf-8"),
            )

            final_response = Response(
                content=response_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
            final_response.headers["X-Idempotency-Replayed"] = "false"
            return final_response
        except Exception as exc:
            await self._mark_failed(
                idempotency_key=idempotency_key,
                request_method=request_method,
                request_path=request_path,
                status_code=500,
                response_body=self.encode_response_payload({"detail": str(exc)}),
            )
            raise

    async def _handle_existing_record(self, record: dict[str, Any], request_hash: str) -> Response:
        if record["request_hash"] != request_hash:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        "Idempotency-Key reuse with a different payload is not allowed"
                    )
                },
            )

        if record["status"] in {"completed", "failed"} and record["response_body"] is not None:
            return self._build_cached_response(record)

        for _ in range(20):
            await asyncio.sleep(0.05)
            refreshed = await self._fetch_record(
                record["idempotency_key"],
                record["request_method"],
                record["request_path"],
            )
            if refreshed is None:
                break
            if refreshed["request_hash"] != request_hash:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            "Idempotency-Key reuse with a different payload is not allowed"
                        )
                    },
                )
            if refreshed["status"] in {"completed", "failed"} and refreshed["response_body"] is not None:
                return self._build_cached_response(refreshed)

        return JSONResponse(
            status_code=409,
            content={"detail": "Request with this Idempotency-Key is already processing"},
        )

    async def _fetch_record(
        self,
        idempotency_key: str,
        request_method: str,
        request_path: str,
    ) -> dict[str, Any] | None:
        async with SessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        idempotency_key,
                        request_method,
                        request_path,
                        request_hash,
                        status,
                        status_code,
                        response_body,
                        created_at,
                        updated_at,
                        expires_at
                    FROM idempotency_keys
                    WHERE idempotency_key = :idempotency_key
                      AND request_method = :request_method
                      AND request_path = :request_path
                    """
                ),
                {
                    "idempotency_key": idempotency_key,
                    "request_method": request_method,
                    "request_path": request_path,
                },
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def _create_processing_record(
        self,
        *,
        idempotency_key: str,
        request_method: str,
        request_path: str,
        request_hash: str,
    ) -> bool:
        now = self._utc_now()
        expires_at = now + timedelta(seconds=self.ttl_seconds)

        async with SessionLocal() as session:
            try:
                await session.execute(
                    text(
                        """
                        INSERT INTO idempotency_keys (
                            id,
                            idempotency_key,
                            request_method,
                            request_path,
                            request_hash,
                            status,
                            created_at,
                            updated_at,
                            expires_at
                        )
                        VALUES (
                            :id,
                            :idempotency_key,
                            :request_method,
                            :request_path,
                            :request_hash,
                            'processing',
                            :created_at,
                            :updated_at,
                            :expires_at
                        )
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "idempotency_key": idempotency_key,
                        "request_method": request_method,
                        "request_path": request_path,
                        "request_hash": request_hash,
                        "created_at": now,
                        "updated_at": now,
                        "expires_at": expires_at,
                    },
                )
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def _mark_completed(
        self,
        *,
        idempotency_key: str,
        request_method: str,
        request_path: str,
        status_code: int,
        response_body: str,
    ) -> None:
        async with SessionLocal() as session:
            await session.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET status = 'completed',
                        status_code = :status_code,
                        response_body = :response_body,
                        updated_at = :updated_at
                    WHERE idempotency_key = :idempotency_key
                      AND request_method = :request_method
                      AND request_path = :request_path
                    """
                ),
                {
                    "status_code": status_code,
                    "response_body": response_body,
                    "updated_at": self._utc_now(),
                    "idempotency_key": idempotency_key,
                    "request_method": request_method,
                    "request_path": request_path,
                },
            )
            await session.commit()

    async def _mark_failed(
        self,
        *,
        idempotency_key: str,
        request_method: str,
        request_path: str,
        status_code: int,
        response_body: str,
    ) -> None:
        async with SessionLocal() as session:
            await session.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET status = 'failed',
                        status_code = :status_code,
                        response_body = :response_body,
                        updated_at = :updated_at
                    WHERE idempotency_key = :idempotency_key
                      AND request_method = :request_method
                      AND request_path = :request_path
                    """
                ),
                {
                    "status_code": status_code,
                    "response_body": response_body,
                    "updated_at": self._utc_now(),
                    "idempotency_key": idempotency_key,
                    "request_method": request_method,
                    "request_path": request_path,
                },
            )
            await session.commit()

    def _build_cached_response(self, record: dict[str, Any]) -> Response:
        return Response(
            content=record["response_body"],
            status_code=record["status_code"] or 200,
            media_type="application/json",
            headers={"X-Idempotency-Replayed": "true"},
        )

    @staticmethod
    async def _consume_response_body(response: Response) -> bytes:
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        return body

    @staticmethod
    def _clone_request(request: Request, raw_body: bytes) -> Request:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw_body, "more_body": False}

        return Request(request.scope, receive)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def build_request_hash(raw_body: bytes) -> str:
        """Стабильный хэш тела запроса для проверки reuse ключа с другим payload."""
        return hashlib.sha256(raw_body).hexdigest()

    @staticmethod
    def encode_response_payload(body_obj: Any) -> str:
        """Сериализация response body для сохранения в idempotency_keys."""
        return json.dumps(body_obj, ensure_ascii=False)
