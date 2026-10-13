import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.health import ReadinessCheck
from app.main import create_app


def make_client(readiness_checks: list[ReadinessCheck] | None = None) -> TestClient:
    settings = Settings(NODE_ENV="test", log_level="critical")
    return TestClient(create_app(settings, readiness_checks), raise_server_exceptions=False)


@pytest.fixture
def client() -> TestClient:
    return make_client()
