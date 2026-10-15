"""Catalog business logic and queries. Routers stay thin; rules live here."""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, and_, func, literal, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.errors import ProblemError
from app.models import Category, Product, ProductImage, ProductStatus, Specification, Variant
from app.pricing import effective_price_minor, is_on_sale, sql_effective_price, sql_on_sale
from app.schemas import (
    CategoryCreate,
    CategoryNode,
    CategoryOut,
    CategoryRef,
    CategoryUpdate,
    ImageOut,
    Money,
    PriceLookupItem,
    PriceLookupResponse,
    ProductCreate,
    ProductDetail,
    ProductSummary,
    ProductUpdate,
    SpecificationOut,
    VariantCreate,
    VariantOut,
    VariantUpdate,
    validate_sale,
)

SortKey = Literal["relevance", "newest", "price_asc", "price_desc", "name"]

# Database constraint name -> API error. Deterministic names come from the naming
# convention in app/models.py; the database is the final authority on uniqueness.
_CONSTRAINT_ERRORS = {
    "uq_products_slug": (409, "SLUG_TAKEN", "A product with this slug already exists"),
    "uq_categories_slug": (409, "SLUG_TAKEN", "A category with this slug already exists"),
    "uq_product_variants_sku": (409, "SKU_TAKEN", "A variant with this SKU already exists"),
    "fk_products_category_id_categories": (422, "CATEGORY_NOT_FOUND", "Category does not exist"),
    "fk_categories_parent_id_categories": (422, "CATEGORY_NOT_FOUND", "Parent category does not exist"),
}


def _translate_integrity_error(exc: IntegrityError) -> ProblemError:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    status, code, title = _CONSTRAINT_ERRORS.get(
        constraint or "", (409, "CONSTRAINT_VIOLATION", "The change violates a data constraint")
    )
    return ProblemError(status, code, title)


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise _translate_integrity_error(exc) from exc
    except StaleDataError as exc:
        session.rollback()
        raise ProblemError(412, "VERSION_MISMATCH", "The product was modified by someone else; reload it") from exc


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:120] or "item"


def _unique_slug(session: Session, model: type[Product] | type[Category], base: str) -> str:
    """Auto-generated slugs get a short suffix on collision; explicit slugs fail with 409 instead."""
    if session.scalar(select(func.count()).where(model.slug == base)) == 0:
        return base
    return f"{base}-{secrets.token_hex(3)}"


def _now() -> datetime:
    return datetime.now(UTC)


# ============================================================================ categories


def category_tree(session: Session) -> list[CategoryNode]:
    rows = session.scalars(select(Category).order_by(Category.position, Category.name)).all()
    # Build from the flat columns only: validating the ORM object directly would also pull in
    # its lazy-loaded `children` relationship, and every child would then be added twice.
    nodes = {c.id: CategoryNode(**CategoryOut.model_validate(c).model_dump(), children=[]) for c in rows}
    roots: list[CategoryNode] = []
    for c in rows:
        node = nodes[c.id]
        if c.parent_id is not None and c.parent_id in nodes:
            nodes[c.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


def get_category(session: Session, category_id: uuid.UUID) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise ProblemError(404, "NOT_FOUND", "Category not found")
    return category


def _descendant_ids(session: Session, category_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Recursive CTE: the category and every category below it, at any depth."""
    tree = select(Category.id).where(Category.id == category_id).cte("tree", recursive=True)
    tree = tree.union_all(select(Category.id).where(Category.parent_id == tree.c.id))
    return select(tree.c.id)


def create_category(session: Session, data: CategoryCreate) -> Category:
    if data.parent_id is not None:
        get_category(session, data.parent_id)
    category = Category(
        name=data.name,
        slug=data.slug or _unique_slug(session, Category, slugify(data.name)),
        description=data.description,
        parent_id=data.parent_id,
        position=data.position,
    )
    session.add(category)
    _flush(session)
    return category


def update_category(session: Session, category_id: uuid.UUID, data: CategoryUpdate) -> Category:
    category = get_category(session, category_id)
    fields = data.model_fields_set
    if "parent_id" in fields and data.parent_id is not None:
        # Moving a category under its own descendant would create a cycle.
        if data.parent_id in set(session.scalars(_descendant_ids(session, category_id))):
            raise ProblemError(409, "CATEGORY_CYCLE", "A category cannot be moved under itself or its descendants")
        get_category(session, data.parent_id)
    for field in ("name", "slug", "description", "parent_id", "position"):
        if field in fields:
            value = getattr(data, field)
            if field in ("name", "slug", "position") and value is None:
                raise ProblemError(400, "VALIDATION_FAILED", f"{field} cannot be null")
            setattr(category, field, value)
    _flush(session)
    return category


def delete_category(session: Session, category_id: uuid.UUID) -> None:
    category = get_category(session, category_id)
    has_children = session.scalar(select(func.count()).where(Category.parent_id == category_id))
    has_products = session.scalar(select(func.count()).where(Product.category_id == category_id))
    if has_children or has_products:
        raise ProblemError(409, "CATEGORY_NOT_EMPTY", "Move or delete its subcategories and products first")
    session.delete(category)
    _flush(session)


# ============================================================================ product reads


WORD_SIMILARITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class ListFilters:
    q: str | None = None
    category_id: uuid.UUID | None = None
    min_price: int | None = None
    max_price: int | None = None
    featured: bool | None = None
    statuses: tuple[ProductStatus, ...] = (ProductStatus.PUBLISHED,)
    sort: SortKey | None = None
    page: int = 1
    page_size: int = 24


def list_products(session: Session, f: ListFilters) -> tuple[list[ProductSummary], int]:
    # Per-product price aggregate over ACTIVE variants, computed by the same SQL rule as
    # the detail endpoint. Products with no active variant never appear in listings.
    prices = (
        select(
            Variant.product_id.label("product_id"),
            func.min(sql_effective_price()).label("lowest_price"),
            func.min(Variant.currency).label("currency"),
            func.bool_or(sql_on_sale()).label("on_sale"),
        )
        .where(Variant.is_active.is_(True))
        .group_by(Variant.product_id)
        .subquery("prices")
    )

    conditions = [Product.status.in_(f.statuses)]
    rank = None
    if f.q:
        query = func.websearch_to_tsquery("english", f.q)
        # Full text (stemming, weights) OR a fuzzy match on the name, so typos ("cotten") and word starts
        # ("cott") still find products. `<%` is pg_trgm word similarity, served by ix_products_name_trgm;
        # its threshold (default 0.6) is lowered for this transaction only: 0.5 still rejects unrelated
        # words (they score ~0) but accepts one-letter typos.
        session.execute(text(f"SET LOCAL pg_trgm.word_similarity_threshold = {WORD_SIMILARITY_THRESHOLD}"))
        conditions.append(or_(Product.search_vector.op("@@")(query), literal(f.q).op("<%")(Product.name)))
        rank = func.ts_rank(Product.search_vector, query) + func.word_similarity(literal(f.q), Product.name)
    if f.category_id is not None:
        conditions.append(Product.category_id.in_(_descendant_ids(session, f.category_id)))
    if f.min_price is not None:
        conditions.append(prices.c.lowest_price >= f.min_price)
    if f.max_price is not None:
        conditions.append(prices.c.lowest_price <= f.max_price)
    if f.featured is not None:
        conditions.append(Product.featured.is_(f.featured))

    base = select(Product).join(prices, prices.c.product_id == Product.id).where(and_(*conditions))
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0

    sort = f.sort or ("relevance" if f.q else "newest")
    order = {
        "relevance": [rank.desc()] if rank is not None else [Product.created_at.desc()],
        "newest": [Product.created_at.desc()],
        "price_asc": [prices.c.lowest_price.asc()],
        "price_desc": [prices.c.lowest_price.desc()],
        "name": [Product.name.asc()],
    }[sort]
    stmt = (
        base.add_columns(prices.c.lowest_price, prices.c.currency, prices.c.on_sale)
        .options(selectinload(Product.images))
        .order_by(*order, Product.id)  # id tie-breaker: stable pages
        .limit(f.page_size)
        .offset((f.page - 1) * f.page_size)
    )
    items = [
        ProductSummary(
            id=p.id,
            slug=p.slug,
            name=p.name,
            brand=p.brand,
            category=CategoryRef.model_validate(p.category),
            lowest_price=Money(amount_minor=lowest, currency=currency),
            on_sale=bool(on_sale),
            featured=p.featured,
            primary_image=ImageOut.model_validate(p.images[0]) if p.images else None,
            status=p.status.value,
        )
        for p, lowest, currency, on_sale in session.execute(stmt).all()
    ]
    return items, total


def _load_product(session: Session, condition) -> Product | None:
    return session.scalar(
        select(Product)
        .where(condition)
        .options(selectinload(Product.variants), selectinload(Product.images), selectinload(Product.specifications))
    )


def get_product(
    session: Session, *, product_id: uuid.UUID | None = None, slug: str | None = None, admin: bool
) -> Product:
    condition = Product.id == product_id if product_id is not None else Product.slug == slug
    product = _load_product(session, condition)
    # Unpublished products are invisible (404, not 403) to the public.
    if product is None or (not admin and product.status != ProductStatus.PUBLISHED):
        raise ProblemError(404, "NOT_FOUND", "Product not found")
    return product


def to_detail(product: Product, *, admin: bool) -> ProductDetail:
    now = _now()
    variants = [v for v in product.variants if admin or v.is_active]
    return ProductDetail(
        id=product.id,
        slug=product.slug,
        name=product.name,
        description=product.description,
        brand=product.brand,
        category=CategoryRef.model_validate(product.category),
        status=product.status.value,
        featured=product.featured,
        variants=[_variant_out(v, now) for v in variants],
        images=[ImageOut.model_validate(i) for i in product.images],
        specifications=[SpecificationOut.model_validate(s) for s in product.specifications],
        version=product.version,
        published_at=product.published_at,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _variant_out(v: Variant, now: datetime) -> VariantOut:
    return VariantOut(
        id=v.id,
        sku=v.sku,
        name=v.name,
        attributes=v.attributes,
        price=Money(amount_minor=v.price_minor, currency=v.currency),
        sale_price=Money(amount_minor=v.sale_price_minor, currency=v.currency)
        if v.sale_price_minor is not None
        else None,
        sale_starts_at=v.sale_starts_at,
        sale_ends_at=v.sale_ends_at,
        effective_price=Money(amount_minor=effective_price_minor(v, now), currency=v.currency),
        on_sale=is_on_sale(v, now),
        is_active=v.is_active,
    )


# ============================================================================ product writes


def _variant_from(data: VariantCreate, position: int) -> Variant:
    return Variant(
        sku=data.sku,
        name=data.name,
        attributes=data.attributes,
        price_minor=data.price.amount_minor,
        sale_price_minor=data.sale_price.amount_minor if data.sale_price else None,
        sale_starts_at=data.sale_starts_at,
        sale_ends_at=data.sale_ends_at,
        currency=data.price.currency,
        # Set explicitly: column defaults only apply at INSERT, and the publish check
        # below inspects this object before it is flushed.
        is_active=True,
        position=position,
    )


def _set_status(product: Product, status: ProductStatus) -> None:
    if status == ProductStatus.PUBLISHED:
        if not any(v.is_active for v in product.variants):
            raise ProblemError(409, "PRODUCT_NOT_PUBLISHABLE", "A product needs at least one active variant to publish")
        if product.published_at is None:
            product.published_at = _now()
    product.status = status


def create_product(session: Session, data: ProductCreate) -> Product:
    get_category(session, data.category_id)
    product = Product(
        name=data.name,
        slug=data.slug or _unique_slug(session, Product, slugify(data.name)),
        description=data.description,
        brand=data.brand,
        category_id=data.category_id,
        featured=data.featured,
        variants=[_variant_from(v, i) for i, v in enumerate(data.variants)],
        images=[ProductImage(url=str(img.url), alt=img.alt, position=i) for i, img in enumerate(data.images)],
        specifications=[
            Specification(name=s.name, value=s.value, position=i) for i, s in enumerate(data.specifications)
        ],
    )
    _set_status(product, ProductStatus(data.status))
    session.add(product)
    _flush(session)
    return product


def update_product(session: Session, product_id: uuid.UUID, data: ProductUpdate, if_match: int | None) -> Product:
    product = get_product(session, product_id=product_id, admin=True)
    if if_match is not None and if_match != product.version:
        raise ProblemError(412, "VERSION_MISMATCH", "The product was modified by someone else; reload it")

    fields = data.model_fields_set
    for field in ("name", "slug", "featured", "category_id"):
        if field in fields:
            value = getattr(data, field)
            if value is None:
                raise ProblemError(400, "VALIDATION_FAILED", f"{field} cannot be null")
            if field == "category_id":
                get_category(session, value)
            setattr(product, field, value)
    if "description" in fields:
        product.description = data.description or ""
    if "brand" in fields:
        product.brand = data.brand
    if "images" in fields:
        product.images = [ProductImage(url=str(i.url), alt=i.alt, position=n) for n, i in enumerate(data.images or [])]
    if "specifications" in fields:
        product.specifications = [
            Specification(name=s.name, value=s.value, position=n) for n, s in enumerate(data.specifications or [])
        ]
    if "status" in fields and data.status is not None:
        _set_status(product, ProductStatus(data.status))

    # Any change bumps the version (even child-only changes), so concurrent editors notice.
    product.updated_at = _now()
    _flush(session)
    return product


def archive_product(session: Session, product_id: uuid.UUID) -> None:
    """Soft delete: orders and inventory keep referencing its SKUs. Idempotent."""
    product = session.get(Product, product_id)
    if product is None:
        raise ProblemError(404, "NOT_FOUND", "Product not found")
    if product.status != ProductStatus.ARCHIVED:
        product.status = ProductStatus.ARCHIVED
        _flush(session)


def add_variant(session: Session, product_id: uuid.UUID, data: VariantCreate) -> Product:
    product = get_product(session, product_id=product_id, admin=True)
    currency = product.variants[0].currency if product.variants else data.price.currency
    if data.price.currency != currency:
        raise ProblemError(422, "CURRENCY_MISMATCH", f"All variants of this product use {currency}")
    product.variants.append(_variant_from(data, len(product.variants)))
    product.updated_at = _now()
    _flush(session)
    return product


def update_variant(session: Session, product_id: uuid.UUID, variant_id: uuid.UUID, data: VariantUpdate) -> Product:
    product = get_product(session, product_id=product_id, admin=True)
    variant = next((v for v in product.variants if v.id == variant_id), None)
    if variant is None:
        raise ProblemError(404, "NOT_FOUND", "Variant not found")

    fields = data.model_fields_set
    if "price" in fields:
        if data.price is None:
            raise ProblemError(400, "VALIDATION_FAILED", "price cannot be null")
        if data.price.currency != variant.currency:
            raise ProblemError(422, "CURRENCY_MISMATCH", f"This variant is priced in {variant.currency}")
        variant.price_minor = data.price.amount_minor
    if "sale_price" in fields:
        variant.sale_price_minor = data.sale_price.amount_minor if data.sale_price else None
    for field in ("sale_starts_at", "sale_ends_at", "name"):
        if field in fields:
            setattr(variant, field, getattr(data, field))
    if "attributes" in fields:
        variant.attributes = data.attributes or {}
    if "is_active" in fields and data.is_active is not None:
        variant.is_active = data.is_active

    # Re-validate the combined state (e.g. a new price below an existing sale price).
    try:
        validate_sale(
            Money(amount_minor=variant.price_minor, currency=variant.currency),
            Money(amount_minor=variant.sale_price_minor, currency=variant.currency)
            if variant.sale_price_minor is not None
            else None,
            variant.sale_starts_at,
            variant.sale_ends_at,
        )
    except ValueError as exc:
        session.rollback()
        raise ProblemError(422, "INVALID_PRICING", str(exc)) from exc
    if product.status == ProductStatus.PUBLISHED and not any(v.is_active for v in product.variants):
        session.rollback()
        raise ProblemError(409, "PRODUCT_NOT_PUBLISHABLE", "A published product must keep at least one active variant")

    product.updated_at = _now()
    _flush(session)
    return product


# ============================================================================ internal


def price_lookup(session: Session, skus: list[str]) -> PriceLookupResponse:
    """Authoritative current prices for the Order Service's snapshots.
    Only active variants of PUBLISHED products are purchasable."""
    rows = session.execute(
        select(Variant, Product.name)
        .join(Product, Product.id == Variant.product_id)
        .where(
            Variant.sku.in_(set(skus)),
            Variant.is_active.is_(True),
            Product.status == ProductStatus.PUBLISHED,
        )
    ).all()
    now = _now()
    found = {
        v.sku: PriceLookupItem(
            sku=v.sku,
            product_id=v.product_id,
            variant_id=v.id,
            name=f"{product_name}, {v.name}" if v.name else product_name,
            unit_price=Money(amount_minor=effective_price_minor(v, now), currency=v.currency),
        )
        for v, product_name in rows
    }
    ordered = list(dict.fromkeys(skus))
    return PriceLookupResponse(
        items=[found[s] for s in ordered if s in found],
        unavailable=[s for s in ordered if s not in found],
    )
