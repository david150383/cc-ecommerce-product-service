# ecommerce-product-service

The catalog for the e-commerce platform: products, categories and subcategories, variants
(SKUs), slugs, images, descriptions and specifications, catalog pricing and sales, publication
status, search, filtering, sorting and pagination, and featured products.

The Product Service owns **catalog prices**. It does **not** own stock (Inventory does) or the
price a customer paid (Order snapshots that at purchase time).

| | |
|---|---|
| Stack | Python 3.14, FastAPI, Pydantic 2, structlog; SQLAlchemy 2 + Alembic + PostgreSQL 17 *(Phase 2)* |
| Port | 4002 |
| Database | `product_db` (PostgreSQL), owned exclusively by this service |
| API docs | [docs/api.md](docs/api.md); generated OpenAPI at `/openapi.json`, UI at `/docs` |
| Messages | [docs/events.md](docs/events.md) (none in v1) |

## Status

| Phase | Done |
|---|---|
| 1: skeleton | ✅ app factory, pydantic-settings config, JSON logging with redaction, UUIDv7 correlation IDs, RFC 9457 errors (incl. validation and malformed JSON), security headers, CORS, health/readiness, graceful shutdown, Dockerfile, CI |
| 2: features | ⏳ SQLAlchemy models, Alembic migrations, catalog CRUD, search |

## Run locally

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pip install --no-deps -e .
cp .env.example .env
.venv/bin/python -m app            # http://localhost:4002/health/live, /docs
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Dependency locking

`pyproject.toml` declares version ranges. `requirements.txt` (runtime) and
`requirements-dev.txt` (runtime + dev) pin exact versions for Docker and CI. After changing
dependencies:

```bash
python3.14 -m venv /tmp/lock && /tmp/lock/bin/pip install . && /tmp/lock/bin/pip freeze | grep -v ecommerce-product-service > requirements.txt
.venv/bin/pip install -e ".[dev]" && .venv/bin/pip freeze | grep -v -e "^-e" -e ecommerce-product-service > requirements-dev.txt
```

## Layout

```
app/main.py         create_app(): FastAPI app factory, used by tests
app/__main__.py     `python -m app`: uvicorn with graceful shutdown
app/config.py       validated settings (environment variables / .env)
app/logging.py      structlog JSON logging + redaction
app/middleware.py   correlation/request IDs, request logging, security headers
app/errors.py       RFC 9457 problem details for every error type
app/health.py       /health/live, /health/ready
```
