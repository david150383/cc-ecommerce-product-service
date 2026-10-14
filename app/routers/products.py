import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app import catalog
from app.catalog import ListFilters, SortKey
from app.deps import DbSession
from app.errors import ProblemError
from app.models import ProductStatus
from app.schemas import PageInfo, ProductCreate, ProductDetail, ProductPage, ProductUpdate, VariantCreate, VariantUpdate
from app.security import AdminPrincipal, OptionalPrincipal

router = APIRouter(prefix="/api/v1", tags=["products"])

PROBLEM = {"model": None, "content": {"application/problem+json": {}}}


def _etag(version: int) -> str:
    return f'"{version}"'


def _parse_if_match(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip().removeprefix("W/").strip('"'))
    except ValueError as exc:
        raise ProblemError(400, "VALIDATION_FAILED", "If-Match must be the ETag returned by GET") from exc


def list_products_response(
    session: DbSession,
    principal: OptionalPrincipal,
    q: str | None,
    category_id: uuid.UUID | None,
    min_price: int | None,
    max_price: int | None,
    featured: bool | None,
    status_filter: ProductStatus | None,
    sort: SortKey | None,
    page: int,
    page_size: int,
) -> ProductPage:
    if status_filter is not None and not (principal and principal.is_admin):
        raise ProblemError(403, "FORBIDDEN", "Only administrators can filter by status")
    statuses = (status_filter,) if status_filter else (ProductStatus.PUBLISHED,)
    if sort == "relevance" and not q:
        raise ProblemError(400, "VALIDATION_FAILED", "sort=relevance requires q")
    items, total = catalog.list_products(
        session,
        ListFilters(
            q=q,
            category_id=category_id,
            min_price=min_price,
            max_price=max_price,
            featured=featured,
            statuses=statuses,
            sort=sort,
            page=page,
            page_size=page_size,
        ),
    )
    return ProductPage(data=items, page=PageInfo(page=page, page_size=page_size, total=total))


@router.get("/products", response_model=ProductPage, summary="Search, filter, sort and paginate products")
def list_products(
    session: DbSession,
    principal: OptionalPrincipal,
    q: Annotated[str | None, Query(min_length=1, max_length=200, description="Full-text search")] = None,
    category: Annotated[uuid.UUID | None, Query(description="Category id; includes subcategories")] = None,
    min_price: Annotated[int | None, Query(alias="minPrice", ge=0, description="Minor units")] = None,
    max_price: Annotated[int | None, Query(alias="maxPrice", ge=0, description="Minor units")] = None,
    featured: bool | None = None,
    status_filter: Annotated[ProductStatus | None, Query(alias="status", description="Admins only")] = None,
    sort: SortKey | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 24,
) -> ProductPage:
    return list_products_response(
        session, principal, q, category, min_price, max_price, featured, status_filter, sort, page, page_size
    )


@router.get("/products/by-slug/{slug}", response_model=ProductDetail, summary="Product by slug (SEO URLs)")
def get_by_slug(slug: str, session: DbSession, principal: OptionalPrincipal, response: Response) -> ProductDetail:
    admin = bool(principal and principal.is_admin)
    product = catalog.get_product(session, slug=slug, admin=admin)
    response.headers["ETag"] = _etag(product.version)
    return catalog.to_detail(product, admin=admin)


@router.get("/products/{product_id}", response_model=ProductDetail, summary="Product detail")
def get_product(
    product_id: uuid.UUID, session: DbSession, principal: OptionalPrincipal, response: Response
) -> ProductDetail:
    admin = bool(principal and principal.is_admin)
    product = catalog.get_product(session, product_id=product_id, admin=admin)
    response.headers["ETag"] = _etag(product.version)
    return catalog.to_detail(product, admin=admin)


@router.post("/products", response_model=ProductDetail, status_code=status.HTTP_201_CREATED, summary="Create product")
def create_product(
    body: ProductCreate, session: DbSession, _admin: AdminPrincipal, response: Response
) -> ProductDetail:
    product = catalog.create_product(session, body)
    response.headers["ETag"] = _etag(product.version)
    response.headers["Location"] = f"/api/v1/products/{product.id}"
    return catalog.to_detail(product, admin=True)


@router.patch("/products/{product_id}", response_model=ProductDetail, summary="Update product (If-Match optional)")
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    session: DbSession,
    _admin: AdminPrincipal,
    response: Response,
    if_match: Annotated[str | None, Header(description='ETag from GET, e.g. "3"')] = None,
) -> ProductDetail:
    product = catalog.update_product(session, product_id, body, _parse_if_match(if_match))
    response.headers["ETag"] = _etag(product.version)
    return catalog.to_detail(product, admin=True)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Archive (soft delete)")
def archive_product(product_id: uuid.UUID, session: DbSession, _admin: AdminPrincipal) -> None:
    catalog.archive_product(session, product_id)


@router.post(
    "/products/{product_id}/variants",
    response_model=ProductDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Add a variant (SKU)",
)
def add_variant(
    product_id: uuid.UUID, body: VariantCreate, session: DbSession, _admin: AdminPrincipal, response: Response
) -> ProductDetail:
    product = catalog.add_variant(session, product_id, body)
    response.headers["ETag"] = _etag(product.version)
    return catalog.to_detail(product, admin=True)


@router.patch(
    "/products/{product_id}/variants/{variant_id}",
    response_model=ProductDetail,
    summary="Update a variant's price, sale or status (SKU is immutable)",
)
def update_variant(
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: VariantUpdate,
    session: DbSession,
    _admin: AdminPrincipal,
    response: Response,
) -> ProductDetail:
    product = catalog.update_variant(session, product_id, variant_id, body)
    response.headers["ETag"] = _etag(product.version)
    return catalog.to_detail(product, admin=True)
