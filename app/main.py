import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from app.config import Settings, get_settings
from app.db import create_db_engine, ping, session_factory
from app.errors import register_error_handlers
from app.health import ReadinessCheck
from app.health import router as health_router
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.routers import categories, internal, products
from app.security import TokenVerifier


def create_app(
    settings: Settings | None = None,
    readiness_checks: list[ReadinessCheck] | None = None,
    *,
    engine: Engine | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Builds the app without starting a server, so tests can drive it in-process.
    Tests inject their own engine (scratch database) and token verifier (test keys)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = engine or create_db_engine(settings.database_url, settings.db_pool_size)

    shutting_down = False

    async def accepting_traffic() -> None:
        if shutting_down:
            raise RuntimeError("shutting down")

    async def database() -> None:
        await asyncio.to_thread(ping, engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal shutting_down
        get_logger().info("server started", port=settings.port)
        yield
        # Uvicorn stops accepting connections and drains in-flight requests on SIGTERM;
        # readiness reports not_ready from here on.
        shutting_down = True
        engine.dispose()
        get_logger().info("shutdown complete")

    app = FastAPI(
        title="Product Service",
        description=(
            "Catalog, categories, variants and pricing. Money is integer minor units + ISO 4217 currency. "
            "Conventions: ecommerce-infrastructure/docs/architecture/conventions.md"
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.readiness_checks = [
        ReadinessCheck("accepting-traffic", accepting_traffic),
        ReadinessCheck("database", database),
        *(readiness_checks or []),
    ]
    app.state.session_factory = session_factory(engine)
    app.state.token_verifier = token_verifier or TokenVerifier.from_jwks_url(
        # Accept user tokens (shared audience) and service tokens minted for this service only.
        settings.jwks_url,
        settings.jwt_issuer,
        [settings.jwt_audience, settings.jwt_service_audience],
    )

    # Starlette runs middleware in reverse order of registration: RequestContext runs first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key", "X-Correlation-Id"],
        expose_headers=["ETag", "Location", "X-Correlation-Id", "X-Request-Id"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(products.router)
    app.include_router(categories.router)
    app.include_router(internal.router)
    return app
