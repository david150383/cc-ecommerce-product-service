import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app import catalog
from app.catalog import SortKey
from app.deps import DbSession
from app.routers.products import list_products_response
from app.schemas import CategoryCreate, CategoryNode, CategoryOut, CategoryUpdate, ProductPage
from app.security import AdminPrincipal, OptionalPrincipal

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])


@router.get("", response_model=list[CategoryNode], summary="Category tree")
def category_tree(session: DbSession) -> list[CategoryNode]:
    return catalog.category_tree(session)


@router.get(
    "/{category_id}/products", response_model=ProductPage, summary="Products in a category and its subcategories"
)
def category_products(
    category_id: uuid.UUID,
    session: DbSession,
    principal: OptionalPrincipal,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    min_price: Annotated[int | None, Query(alias="minPrice", ge=0)] = None,
    max_price: Annotated[int | None, Query(alias="maxPrice", ge=0)] = None,
    featured: bool | None = None,
    sort: SortKey | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 24,
) -> ProductPage:
    catalog.get_category(session, category_id)  # 404 for unknown categories, not an empty page
    return list_products_response(
        session, principal, q, category_id, min_price, max_price, featured, None, sort, page, page_size
    )


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED, summary="Create category")
def create_category(body: CategoryCreate, session: DbSession, _admin: AdminPrincipal) -> CategoryOut:
    return CategoryOut.model_validate(catalog.create_category(session, body))


@router.patch("/{category_id}", response_model=CategoryOut, summary="Update or move a category")
def update_category(
    category_id: uuid.UUID, body: CategoryUpdate, session: DbSession, _admin: AdminPrincipal
) -> CategoryOut:
    return CategoryOut.model_validate(catalog.update_category(session, category_id, body))


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an empty category")
def delete_category(category_id: uuid.UUID, session: DbSession, _admin: AdminPrincipal) -> None:
    catalog.delete_category(session, category_id)
