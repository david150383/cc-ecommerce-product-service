import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging import get_logger

# Accept a caller-supplied correlation ID only if it looks like an ID.
_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{8,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns correlation/request IDs, binds them to every log line, logs the request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-correlation-id")
        correlation_id = incoming if incoming and _SAFE_ID.match(incoming) else str(uuid.uuid7())
        request_id = str(uuid.uuid7())
        request.state.correlation_id = correlation_id
        request.state.request_id = request_id

        with structlog.contextvars.bound_contextvars(correlationId=correlation_id, requestId=request_id):
            started = time.perf_counter()
            response = await call_next(request)
            if not request.url.path.startswith("/health"):
                get_logger().info(
                    "request completed",
                    method=request.method,
                    path=request.url.path,
                    statusCode=response.status_code,
                    responseTimeMs=round((time.perf_counter() - started) * 1000, 1),
                )

        response.headers["X-Correlation-Id"] = correlation_id
        response.headers["X-Request-Id"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """A JSON-API subset of what helmet sets in the Node services."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # Swagger UI (/docs) loads its assets from a CDN, so a strict CSP is applied only to API routes.
        if not request.url.path.startswith(("/docs", "/redoc")):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response
