"""Test harness: a real PostgreSQL database built by the real migrations, and tokens signed
the same way ecommerce-auth-service signs them (RS256 + kid), verified through the real
TokenVerifier with a static JWKS."""

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import psycopg
import pytest
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from alembic import command
from app.config import Settings
from app.health import ReadinessCheck
from app.main import create_app
from app.security import StaticKeyResolver, TokenVerifier

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://product_svc:product_local_pw@localhost:5432/product_test"
)
ISSUER, AUDIENCE, KID = "ecommerce-auth-service", "ecommerce-api", "test-key"


def _recreate_database(url: str) -> None:
    sa_url = url.replace("postgresql+psycopg://", "postgresql://")
    db_name = sa_url.rsplit("/", 1)[1].split("?")[0]
    maintenance = sa_url.rsplit("/", 1)[0] + "/postgres"
    try:
        conn = psycopg.connect(maintenance, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.exit(
            "Cannot reach PostgreSQL for integration tests. Start it with `docker compose up -d` here "
            f"or in ecommerce-infrastructure, or set TEST_DATABASE_URL. ({exc})",
            returncode=2,
        )
    with conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{db_name}"')


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    _recreate_database(TEST_DATABASE_URL)
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")  # the real migrations, not metadata.create_all()
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_tables(request: pytest.FixtureRequest) -> None:
    if "engine" not in request.fixturenames and "client" not in request.fixturenames:
        return
    eng: Engine = request.getfixturevalue("engine")
    with eng.begin() as conn:
        conn.execute(
            text("TRUNCATE product_specifications, product_images, product_variants, products, categories CASCADE")
        )


# ------------------------------------------------------------------ tokens


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def verifier(signing_key: rsa.RSAPrivateKey) -> TokenVerifier:
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwks = {"keys": [{**public_jwk, "kid": KID, "alg": "RS256", "use": "sig"}]}
    return TokenVerifier(StaticKeyResolver(jwks), ISSUER, [AUDIENCE, "ecommerce-product-service"])


TokenFactory = Callable[..., str]


@pytest.fixture(scope="session")
def make_token(signing_key: rsa.RSAPrivateKey) -> TokenFactory:
    def factory(
        roles: list[str], *, sub: str | None = None, audience: str = AUDIENCE, ttl: int = 900, **extra: Any
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": sub or str(uuid.uuid7()),
            "roles": roles,
            "iss": ISSUER,
            "aud": audience,
            "iat": now,
            "exp": now + ttl,
            "jti": str(uuid.uuid7()),
            **extra,
        }
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": KID})

    return factory


@pytest.fixture
def admin_headers(make_token: TokenFactory) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(['admin'])}"}


@pytest.fixture
def customer_headers(make_token: TokenFactory) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(['customer'])}"}


@pytest.fixture
def service_headers(make_token: TokenFactory) -> dict[str, str]:
    # Service tokens are addressed to the target service, like Auth's client-credentials grant.
    token = make_token(["service"], sub="ecommerce-order-service", audience="ecommerce-product-service")
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ app


def make_settings(**overrides: Any) -> Settings:
    return Settings(NODE_ENV="test", log_level="critical", database_url=TEST_DATABASE_URL, **overrides)


@pytest.fixture
def make_client(engine: Engine, verifier: TokenVerifier) -> Callable[..., TestClient]:
    def factory(readiness_checks: list[ReadinessCheck] | None = None, **settings: Any) -> TestClient:
        app = create_app(make_settings(**settings), readiness_checks, engine=engine, token_verifier=verifier)
        return TestClient(app, raise_server_exceptions=False)

    return factory


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


# ------------------------------------------------------------------ data builders


@pytest.fixture
def create_category(client: TestClient, admin_headers: dict[str, str]) -> Callable[..., dict]:
    def factory(name: str = "Clothing", **fields: Any) -> dict:
        res = client.post("/api/v1/categories", json={"name": name, **fields}, headers=admin_headers)
        assert res.status_code == 201, res.text
        return res.json()

    return factory


def variant(sku: str, price: int, currency: str = "INR", **fields: Any) -> dict:
    return {"sku": sku, "price": {"amountMinor": price, "currency": currency}, **fields}


@pytest.fixture
def create_product(client: TestClient, admin_headers: dict[str, str], create_category) -> Callable[..., dict]:
    default_category: dict[str, str] = {}

    def factory(name: str = "Classic Tee", *, variants: list[dict] | None = None, **fields: Any) -> dict:
        if "categoryId" not in fields:
            if not default_category:
                default_category.update(create_category("Default"))
            fields["categoryId"] = default_category["id"]
        body = {
            "name": name,
            "status": "PUBLISHED",
            "variants": variants or [variant(f"SKU-{uuid.uuid4().hex[:8].upper()}", 49900)],
            **fields,
        }
        res = client.post("/api/v1/products", json=body, headers=admin_headers)
        assert res.status_code == 201, res.text
        return res.json()

    return factory
