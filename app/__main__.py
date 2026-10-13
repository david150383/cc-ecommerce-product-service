import uvicorn

from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.port,
        # Uvicorn's own logs are plain text; keep only its warnings. Our JSON logger
        # records startup, shutdown and requests.
        log_level="warning",
        access_log=False,  # our middleware logs requests as JSON
        timeout_graceful_shutdown=10,
    )
