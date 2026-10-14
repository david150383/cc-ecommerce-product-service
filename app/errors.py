"""RFC 9457 problem details. See ecommerce-infrastructure/docs/architecture/conventions.md."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger

_CODE_BY_STATUS = {
    400: "VALIDATION_FAILED",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


class ProblemError(Exception):
    def __init__(
        self, status: int, code: str, title: str, detail: str | None = None, extra: dict | None = None
    ) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail
        self.extra = extra or {}


def problem_response(
    request: Request,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, str]] | None = None,
    extra: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"https://errors.ecommerce.local/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "code": code,
        "instance": request.url.path,
        "correlationId": getattr(request.state, "correlation_id", None),
    }
    if detail is not None:
        body["detail"] = detail
    if errors is not None:
        body["errors"] = errors
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        headers = {"WWW-Authenticate": 'Bearer realm="ecommerce"'} if exc.status == 401 else None
        return problem_response(request, exc.status, exc.code, exc.title, exc.detail, extra=exc.extra, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_BY_STATUS.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "BAD_REQUEST")
        if exc.status_code == 412:
            code = "VERSION_MISMATCH"
        title = "Resource not found" if exc.status_code == 404 else str(exc.detail)
        return problem_response(request, exc.status_code, code, title)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return problem_response(request, 400, "MALFORMED_JSON", "Malformed JSON body")
        errors = [
            {"path": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"]} for err in exc.errors()
        ]
        return problem_response(
            request,
            400,
            "VALIDATION_FAILED",
            "Request validation failed",
            "One or more fields are invalid.",
            errors,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the real cause; return a generic message. No stack traces to clients.
        get_logger().error("unhandled error", exc_info=exc)
        return problem_response(
            request, 500, "INTERNAL_ERROR", "Internal server error", "An unexpected error occurred."
        )
