# ecommerce-product-service

The catalog for the e-commerce platform: products, categories and subcategories, variants
(SKUs), slugs, images, descriptions and specifications, catalog pricing and sales, publication
status, search, filtering, sorting and pagination, and featured products.

The Product Service owns **catalog prices**. It does **not** own stock (Inventory does) or the
price a customer paid (Order snapshots that at purchase time).

| | |
|---|---|
| Stack | Python 3.14, FastAPI, Pydantic 2, SQLAlchemy 2.1, Alembic, psycopg 3, PostgreSQL 17, PyJWT, structlog |
| Port | 4002 |
| Database | `product_db` (PostgreSQL), owned exclusively by this service |
| API docs | [docs/api.md](docs/api.md); generated OpenAPI at `/openapi.json`, UI at `/docs` |
| Messages | [docs/events.md](docs/events.md) (none in v1) |

## Status

| Phase | Done |
|---|---|
| 1: skeleton | ✅ app factory, pydantic-settings config, JSON logging with redaction, UUIDv7 correlation IDs, RFC 9457 errors (incl. validation and malformed JSON), security headers, CORS, health/readiness, graceful shutdown, Dockerfile, CI |
| 2: features | ✅ Catalog CRUD, categories (tree, cycle-safe moves), variants & sale pricing, full-text search, filters/sort/pagination, optimistic locking, JWT (JWKS) auth, internal price lookup, Alembic migrations, demo seed |

## Run locally

```bash
# Postgres: standalone here (port 5442; set DATABASE_URL accordingly) or the shared stack (5432)
docker compose up -d
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pip install --no-deps -e .
cp .env.example .env
.venv/bin/alembic upgrade head      # apply migrations
.venv/bin/python -m app.seed         # optional demo catalog (idempotent)
.venv/bin/python -m app              # http://localhost:4002/docs
.venv/bin/pytest                     # needs Postgres; recreates product_test from the migrations
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Admin endpoints need a token from the Auth Service (`POST localhost:4001/api/v1/auth/login` as an admin).

## Migrations (Alembic)

```bash
.venv/bin/alembic revision --autogenerate -m "describe change"   # DRAFT a migration from model changes
# → read and fix the draft! Autogenerate misses some things (e.g. enum types on downgrade,
#   generated-column expression changes). See the initial migration for examples.
.venv/bin/alembic upgrade head        # apply
.venv/bin/alembic downgrade -1       # roll back one
.venv/bin/alembic check              # fail if models have changes without a migration (CI runs this)
```

In containers, the `migrate` image target runs `alembic upgrade head` as a one-off job before the
service starts (`product-migrate` in the platform compose file).

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
app/models.py       SQLAlchemy models + database constraints (deterministic constraint names)
app/schemas.py      Pydantic request/response models (camelCase JSON)
app/catalog.py      business rules and queries (search, category subtree, pricing, locking)
app/pricing.py      the effective-price rule, in Python and SQL
app/security.py     JWT verification via Auth's JWKS; admin/service role dependencies
app/routers/        thin HTTP layer: products, categories, internal
app/seed.py         demo catalog for local development
alembic/            migration environment + versions/
```
