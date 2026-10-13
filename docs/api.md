# Product Service API (v1)

Base URL (local): `http://localhost:4002`. Conventions: `ecommerce-infrastructure/docs/architecture/conventions.md`.
Generated OpenAPI: `GET /openapi.json`, Swagger UI at `/docs`. Both are produced by FastAPI from the
Pydantic schemas, so they cannot drift from the code.
Status: **documented, not yet implemented** (Phase 2). Implemented today: health, OpenAPI shell.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/products` | public | Search, filter, sort, paginate **published** products |
| GET | `/api/v1/products/{id}` | public | Product detail (by UUID). `/api/v1/products/by-slug/{slug}` for SEO URLs |
| POST | `/api/v1/products` | admin | Create product (+ variants, images, specs) |
| PATCH | `/api/v1/products/{id}` | admin | Partial update; `If-Match: <version>` for optimistic locking |
| DELETE | `/api/v1/products/{id}` | admin | Soft delete (archived). Order snapshots are unaffected |
| GET | `/api/v1/categories` | public | Category tree (parent/children) |
| GET | `/api/v1/categories/{id}/products` | public | Products in a category (includes subcategories) |
| POST/PATCH/DELETE | `/api/v1/categories[/{id}]` | admin | Category management |
| POST | `/internal/v1/products/price-lookup` | service token | Batch SKU → name, productId, current price (used by Order Service) |

## GET /api/v1/products

Query parameters:

| Param | Example | Notes |
|---|---|---|
| `q` | `black tee` | Full-text search (PostgreSQL `tsvector` over name, description, brand) |
| `category` | `uuid` | Includes subcategories |
| `minPrice`, `maxPrice` | `10000` | Integer minor units, compared against the *effective* price |
| `inStock` | `true` | Not available here. The frontend asks Inventory, because Product does not own stock |
| `featured` | `true` | |
| `sort` | `price_asc`, `price_desc`, `newest`, `relevance` | |
| `page`, `pageSize` | `1`, `24` | `pageSize` ≤ 100 |

```json
{
  "data": [{
    "id": "uuid", "slug": "classic-tee", "name": "Classic Tee", "brand": "Acme",
    "category": { "id": "uuid", "name": "T-Shirts" },
    "price": { "amountMinor": 59900, "currency": "INR" },
    "salePrice": { "amountMinor": 49900, "currency": "INR" },
    "effectivePrice": { "amountMinor": 49900, "currency": "INR" },
    "primaryImage": { "url": "https://...", "alt": "Black tee front" },
    "featured": true
  }],
  "page": { "page": 1, "pageSize": 24, "total": 132 }
}
```

## Product model

```
Product: id, slug (unique), name, description (sanitized Markdown), brand, category_id,
         status: DRAFT | PUBLISHED | ARCHIVED, featured, version, created_at, updated_at
Variant: id, product_id, sku (unique, ^[A-Z0-9][A-Z0-9-]{2,63}$), attributes {color, size},
         price_minor BIGINT, sale_price_minor BIGINT NULL, sale_starts_at, sale_ends_at, currency
Image:   id, product_id, variant_id NULL, url, alt, position
Spec:    product_id, name, value, position
```

- Prices are per **variant** (the thing with a SKU). The product-level price shown in listings is
  the lowest effective variant price.
- `effectivePrice` = sale price if inside the sale window, else price. It is computed on the
  server so every client agrees.
- Only `PUBLISHED` products are visible to the public. Admins see all statuses.

## Errors

`400 VALIDATION_FAILED`, `404 NOT_FOUND`, `409 SLUG_TAKEN` / `SKU_TAKEN`,
`412 VERSION_MISMATCH` (stale `If-Match`), `401`/`403` for admin routes.
