import logging
import sys

import structlog

SERVICE_NAME = "ecommerce-product-service"

# Secrets must never reach the logs; redaction happens in the processor chain.
_REDACT_KEYS = {"authorization", "cookie", "password", "token", "access_token", "refresh_token", "secret"}


def _redact(_logger: object, _method: str, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="time"),
            _redact,
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("msg"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level.upper()]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.bind_contextvars(service=SERVICE_NAME)


def get_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()
