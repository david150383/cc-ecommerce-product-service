"""Catalog schema (product_db). Owned exclusively by this service.

Invariants live in the database (CHECK / UNIQUE / FK), not only in the API, so that
no code path (admin script, future consumer, bug) can store an invalid catalog.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SKU_PATTERN = r"^[A-Z0-9][A-Z0-9-]{2,63}$"
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


def new_id() -> uuid.UUID:
    """UUIDv7: time-ordered, so primary-key inserts stay index-friendly."""
    return uuid.uuid7()


class Base(DeclarativeBase):
    # Deterministic constraint names, so later migrations can alter/drop them by name
    # instead of guessing what PostgreSQL auto-generated.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class Category(Timestamps, Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    children: Mapped[list[Category]] = relationship(back_populates="parent", order_by="Category.position")
    parent: Mapped[Category | None] = relationship(back_populates="children", remote_side=[id])


class Product(Timestamps, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        # A published product must have been published at some point in time.
        CheckConstraint("status <> 'PUBLISHED' OR published_at IS NOT NULL", name="published_has_timestamp"),
        Index("ix_products_search_vector", "search_vector", postgresql_using="gin"),
        # Typo- and prefix-tolerant name matching (pg_trgm word similarity), next to full-text search.
        Index("ix_products_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("ix_products_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    brand: Mapped[str | None] = mapped_column(String(100))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, name="product_status"), default=ProductStatus.DRAFT, server_default="DRAFT"
    )
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Optimistic locking: SQLAlchemy adds "AND version = :old" to every UPDATE and raises
    # StaleDataError when another writer got there first.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # Maintained by PostgreSQL itself (GENERATED ... STORED): always consistent with the row.
    # Weights: name (A) ranks above brand (B) above description (C).
    # WARNING: Alembic autogenerate cannot detect changes to this expression. If you change
    # it, write the migration by hand (drop and re-add the column, then recreate the index).
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(brand, '')), 'B') || "
            "setweight(to_tsvector('english', coalesce(description, '')), 'C')",
            persisted=True,
        ),
        deferred=True,
    )

    category: Mapped[Category] = relationship(lazy="joined")
    variants: Mapped[list[Variant]] = relationship(
        back_populates="product", order_by="Variant.position", cascade="all, delete-orphan"
    )
    images: Mapped[list[ProductImage]] = relationship(order_by="ProductImage.position", cascade="all, delete-orphan")
    specifications: Mapped[list[Specification]] = relationship(
        order_by="Specification.position", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}


class Variant(Timestamps, Base):
    """The purchasable unit. SKUs are referenced by Inventory and by every order ever placed,
    so variants are deactivated, never deleted."""

    __tablename__ = "product_variants"
    __table_args__ = (
        CheckConstraint(f"sku ~ '{SKU_PATTERN}'", name="sku_format"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("price_minor >= 0", name="price_non_negative"),
        CheckConstraint(
            "sale_price_minor IS NULL OR (sale_price_minor >= 0 AND sale_price_minor < price_minor)",
            name="sale_below_price",
        ),
        CheckConstraint(
            "sale_starts_at IS NULL OR sale_ends_at IS NULL OR sale_starts_at < sale_ends_at",
            name="sale_window_ordered",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    attributes: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Money: integer minor units (paise/cents) + ISO 4217 code. Never floats.
    price_minor: Mapped[int] = mapped_column(BigInteger)
    sale_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    sale_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sale_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    product: Mapped[Product] = relationship(back_populates="variants")


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    alt: Mapped[str] = mapped_column(String(300), default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Specification(Base):
    __tablename__ = "product_specifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
