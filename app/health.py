"""Liveness ("is the process alive?") and readiness ("can it serve traffic now?")."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

CHECK_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    check: Callable[[], Awaitable[None]]


router = APIRouter(include_in_schema=False)


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    checks: list[ReadinessCheck] = request.app.state.readiness_checks
    results = await asyncio.gather(
        *(asyncio.wait_for(c.check(), CHECK_TIMEOUT_SECONDS) for c in checks), return_exceptions=True
    )
    report = {
        c.name: ("failed" if isinstance(r, BaseException) else "ok") for c, r in zip(checks, results, strict=True)
    }
    is_ready = all(status == "ok" for status in report.values())
    return JSONResponse(
        {"status": "ready" if is_ready else "not_ready", "checks": report},
        status_code=200 if is_ready else 503,
    )
