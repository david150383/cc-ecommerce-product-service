from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.errors import register_error_handlers
from app.health import ReadinessCheck
from app.health import router as health_router
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware


def create_app(settings: Settings | None = None, readiness_checks: list[ReadinessCheck] | None = None) -> FastAPI:
    """Builds the app without starting a server, so tests can drive it in-process."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    shutting_down = False

    async def accepting_traffic() -> None:
        if shutting_down:
            raise RuntimeError("shutting down")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal shutting_down
        get_logger().info("server started", port=settings.port)
        yield
        # Uvicorn stops accepting connections and drains in-flight requests on SIGTERM;
        # readiness reports not_ready from here on.
        shutting_down = True
        get_logger().info("shutdown complete")

    app = FastAPI(
        title="Product Service",
        description="Catalog, categories, variants, pricing. See docs/api.md for semantics.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.readiness_checks = [ReadinessCheck("accepting-traffic", accepting_traffic), *(readiness_checks or [])]

    # Starlette runs middleware in reverse order of registration: RequestContext runs first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-Id"],
        expose_headers=["X-Correlation-Id", "X-Request-Id"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)
    app.include_router(health_router)
    # Versioned routers (/api/v1/products, /api/v1/categories) are added in Phase 2.
    return app
