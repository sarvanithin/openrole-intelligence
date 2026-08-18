"""Small public-API security middleware for the single-process beta."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from fortune_intel.config import Settings


class PublicSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings):
        super().__init__(app)
        self.settings = settings
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    def _client_key(self, request: Request) -> str:
        # Never parse X-Forwarded-For here. Uvicorn rewrites request.client only
        # when the immediate peer is in FORWARDED_ALLOW_IPS; reading the header
        # directly would let an internet client rotate spoofed addresses.
        return request.client.host if request.client else "unknown"

    async def _limited(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            timestamps = self._requests[key]
            while timestamps and timestamps[0] <= now - 60:
                timestamps.popleft()
            if len(timestamps) >= self.settings.rate_limit_per_minute:
                return True
            timestamps.append(now)
            if len(self._requests) > 10000:
                stale = [
                    client
                    for client, values in self._requests.items()
                    if not values or values[-1] <= now - 60
                ]
                for client in stale[:2000]:
                    self._requests.pop(client, None)
            return False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            content_length = 0
        if content_length > 1_000_000:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        if (
            request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and await self._limited(self._client_key(request))
        ):
            return JSONResponse(
                {"detail": "rate limit exceeded", "request_id": request_id},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'"
        )
        if self.settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/") and request.method == "GET":
            response.headers.setdefault("Cache-Control", "public, max-age=30")
        return response
