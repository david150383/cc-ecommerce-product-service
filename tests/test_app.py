import re

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.health import ReadinessCheck
from tests.conftest import make_settings


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("database down")


def test_live(client: TestClient) -> None:
    res = client.get("/health/live")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ready_when_checks_pass(make_client) -> None:
    res = make_client([ReadinessCheck("broker", _ok)]).get("/health/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "checks": {"accepting-traffic": "ok", "database": "ok", "broker": "ok"}}


def test_not_ready_when_a_check_fails(make_client) -> None:
    res = make_client([ReadinessCheck("broker", _fail)]).get("/health/ready")
    assert res.status_code == 503
    assert res.json()["checks"]["broker"] == "failed"


def test_correlation_id_is_echoed_when_valid(client: TestClient) -> None:
    res = client.get("/health/live", headers={"X-Correlation-Id": "test-correlation-123"})
    assert res.headers["x-correlation-id"] == "test-correlation-123"
    assert re.fullmatch(r"[0-9a-f-]{36}", res.headers["x-request-id"])


def test_unsafe_correlation_id_is_replaced_with_uuid7(client: TestClient) -> None:
    res = client.get("/health/live", headers={"X-Correlation-Id": "<script>"})
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-7", res.headers["x-correlation-id"])


def test_unknown_route_is_problem_json(client: TestClient) -> None:
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "NOT_FOUND"
    assert body["instance"] == "/api/v1/nope"
    assert body["correlationId"] == res.headers["x-correlation-id"]


@pytest.fixture
def echo_client(engine, verifier) -> TestClient:
    from pydantic import BaseModel, Field

    class Item(BaseModel):
        name: str = Field(min_length=1)
        price_minor: int = Field(ge=0)

    router = APIRouter()

    @router.post("/echo")
    async def echo(item: Item) -> Item:
        return item

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    from app.main import create_app

    app = create_app(make_settings(), engine=engine, token_verifier=verifier)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_malformed_json_is_400(echo_client: TestClient) -> None:
    res = echo_client.post("/echo", content='{"broken":', headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json()["code"] == "MALFORMED_JSON"


def test_validation_errors_list_fields(echo_client: TestClient) -> None:
    res = echo_client.post("/echo", json={"name": "", "price_minor": -1})
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert {e["path"] for e in body["errors"]} == {"name", "price_minor"}


def test_unhandled_errors_do_not_leak_details(echo_client: TestClient) -> None:
    res = echo_client.get("/boom")
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in res.text


def test_security_headers(client: TestClient) -> None:
    res = client.get("/health/live")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "server" not in {k.lower() for k in res.headers if res.headers[k].startswith("uvicorn")}


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    allowed = client.get("/health/live", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/health/live", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_openapi_is_generated(client: TestClient) -> None:
    res = client.get("/openapi.json")
    assert res.status_code == 200
    assert res.json()["info"]["title"] == "Product Service"


def test_settings_parse_comma_separated_origins() -> None:
    settings = make_settings(cors_allowed_origins="https://a.example, https://b.example")
    assert settings.cors_allowed_origins == ["https://a.example", "https://b.example"]
