# Product Service API (v1)

Base URL (local): `http://localhost:4002`. Conventions: `ecommerce-infrastructure/docs/architecture/conventions.md`.

**Machine-readable spec:** `GET /openapi.json` (Swagger UI at `/docs`). FastAPI generates it from the
same Pydantic models that validate requests, so it cannot drift from the code.

Status: **implemented** (Phase 2).

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/products` | public (admin: `status` filter) | Search, filter, sort, paginate |
| GET | `/api/v1/products/{id}` | public (admin sees drafts) | Product detail, `ETag` header |
| GET | `/api/v1/products/by-slug/{slug}` | public | Same, by SEO slug |
| POST | `/api/v1/products` | admin | Create with variants, images, specifications |
| PATCH | `/api/v1/products/{id}` | admin | Partial update; optional `If-Match` (optimistic locking) |
| DELETE | `/api/v1/products/{id}` | admin | Archive (soft delete), idempotent |
| POST | `/api/v1/products/{id}/variants` | admin | Add a variant (SKU) |
| PATCH | `/api/v1/products/{id}/variants/{variantId}` | admin | Reprice, set/clear sale, (de)activate. SKU is immutable |
| GET | `/api/v1/categories` | public | Category tree |
| GET | `/api/v1/categories/{id}/products` | public | Products in the category **and all subcategories** |
| POST | `/api/v1/categories` | admin | Create |
| PATCH | `/api/v1/categories/{id}` | admin | Update or move (cycles rejected) |
| DELETE | `/api/v1/categories/{id}` | admin | Delete; only if empty |
| POST | `/internal/v1/products/price-lookup` | service token | Current purchasable price per SKU (Order Service) |

Authentication: `Authorization: Bearer <access token from ecommerce-auth-service>`. Tokens are verified
locally against Auth's JWKS (cached 10 minutes). A *present but invalid* token is rejected with 401 even
on public endpoints, so a broken client fails loudly instead of silently browsing anonymously.

## GET /api/v1/products

| Param | Example | Notes |
|---|---|---|
| `q` | `running shoes` | PostgreSQL full-text search (`websearch_to_tsquery`, English stemming). Name ranks above brand above description |
| `category` | `uuid` | Includes all subcategories (recursive CTE) |
| `minPrice`, `maxPrice` | `10000` | Integer minor units, compared with the **lowest effective price** |
| `featured` | `true` | |
| `sort` | `relevance` (needs `q`), `newest` (default without `q`), `price_asc`, `price_desc`, `name` | `id` is always the final tie-breaker, so pages are stable |
| `page`, `pageSize` | `1`, `24` | `pageSize` ≤ 100 |
| `status` | `DRAFT` | **Admins only** (403 otherwise). Public callers always get `PUBLISHED` |

```json
{
  "data": [{
    "id": "uuid", "slug": "classic-cotton-tee", "name": "Classic Cotton Tee", "brand": "Acme",
    "category": { "id": "uuid", "name": "T-Shirts", "slug": "t-shirts" },
    "lowestPrice": { "amountMinor": 59900, "currency": "INR" },
    "onSale": false, "featured": true, "status": "PUBLISHED",
    "primaryImage": { "url": "https://…", "alt": "Classic Cotton Tee" }
  }],
  "page": { "page": 1, "pageSize": 24, "total": 5 }
}
```

`inStock` is deliberately **not** a filter here: Product does not own stock. The frontend asks
Inventory for availability.

## Prices

Each **variant** has `price` and an optional `salePrice` with an optional `[saleStartsAt, saleEndsAt)`
window.

- `effectivePrice` = `salePrice` while inside the window, otherwise `price`. One rule is implemented
  twice: in SQL (`app/pricing.py: sql_effective_price`, used for filtering and sorting) and in Python
  (used for responses). A test runs both against the same rows to prove they agree.
- A listing's `lowestPrice` is the minimum effective price over **active** variants. Products with no
  active variant never appear in listings.
- Invariants enforced in the database: `price ≥ 0`, `salePrice < price`, `saleStartsAt < saleEndsAt`,
  ISO currency, one currency per product (API). Timestamps must carry a timezone.

## Product model

```
Category: id, parent_id, name, slug (unique), description, position
Product:  id, slug (unique), name, description (Markdown, rendered without raw HTML by the frontend),
          brand, category_id, status DRAFT | PUBLISHED | ARCHIVED, featured, published_at, version,
          search_vector (GENERATED tsvector, GIN index)
Variant:  id, product_id, sku (unique, ^[A-Z0-9][A-Z0-9-]{2,63}$, immutable), name, attributes (JSONB),
          price_minor, sale_price_minor, sale_starts_at, sale_ends_at, currency, is_active, position
Image:    product_id, url (http/https), alt, position
Spec:     product_id, name, value, position
```

- **Variants are never deleted**, only deactivated: Inventory and every past order reference their SKUs.
- **Publishing** requires at least one active variant (`409 PRODUCT_NOT_PUBLISHABLE`), and the last
  active variant of a published product cannot be deactivated.
- **Archiving** (`DELETE`) hides the product from the public. Admins can still read it.

## Optimistic locking

`GET` returns `ETag: "<version>"`. Send it back as `If-Match` on `PATCH`. If someone saved in between,
you get `412 VERSION_MISMATCH` instead of silently overwriting their change. Without `If-Match`, the
last write wins (convenient for scripts), but the version check still guards the actual UPDATE
(`version_id_col`), so two truly simultaneous writes cannot both succeed.

## POST /internal/v1/products/price-lookup

```json
// request (service token; max 100 SKUs)
{ "skus": ["TEE-CLASSIC-BLK-M", "NOPE-1"] }
// 200
{ "items": [{ "sku": "TEE-CLASSIC-BLK-M", "productId": "uuid", "variantId": "uuid",
              "name": "Classic Cotton Tee, Black, M",
              "unitPrice": { "amountMinor": 59900, "currency": "INR" } }],
  "unavailable": ["NOPE-1"] }
```

Only **active variants of published products** are purchasable. `unitPrice` is the effective price at
the moment of the call; the Order Service snapshots it.

## Errors

| Status | Code | When |
|---|---|---|
| 400 | `VALIDATION_FAILED`, `MALFORMED_JSON` | Bad input (unknown fields rejected) |
| 401 / 403 | `UNAUTHENTICATED` / `FORBIDDEN` | Missing/invalid token / wrong role |
| 404 | `NOT_FOUND` | Unknown, or not visible to you |
| 409 | `SLUG_TAKEN`, `SKU_TAKEN`, `CATEGORY_CYCLE`, `CATEGORY_NOT_EMPTY`, `PRODUCT_NOT_PUBLISHABLE` | Conflicts |
| 412 | `VERSION_MISMATCH` | Stale `If-Match` |
| 422 | `CATEGORY_NOT_FOUND`, `CURRENCY_MISMATCH`, `INVALID_PRICING` | Semantically invalid |
| 503 | `DEPENDENCY_UNAVAILABLE` | Auth's JWKS unreachable and no cached key |

Uniqueness and foreign keys are enforced by the database. Violations are mapped to these codes by
constraint **name**, which is why every constraint has a deterministic name (`app/models.py`).
