"""Request/response models. JSON uses camelCase (platform convention); Python uses snake_case."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from app.models import SKU_PATTERN, SLUG_PATTERN


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class StrictInput(ApiModel):
    """Inputs reject unknown fields, so typos and smuggled fields fail loudly."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Sku = Annotated[str, StringConstraints(pattern=SKU_PATTERN)]
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=160)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
AttributeKey = Annotated[str, StringConstraints(max_length=50)]
AttributeValue = Annotated[str, StringConstraints(max_length=100)]


class Money(ApiModel):
    """Integer minor units (paise/cents) + ISO 4217 currency. Never floating point."""

    amount_minor: int = Field(ge=0, le=10**13, examples=[49900])
    currency: Currency = Field(examples=["INR"])


# ------------------------------------------------------------------ categories


class CategoryCreate(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    slug: Slug | None = None
    description: str | None = Field(None, max_length=2000)
    parent_id: uuid.UUID | None = None
    position: int = 0


class CategoryUpdate(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    slug: Slug | None = None
    description: str | None = Field(None, max_length=2000)
    parent_id: uuid.UUID | None = None
    position: int | None = None


class CategoryOut(ApiModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    parent_id: uuid.UUID | None
    position: int


class CategoryNode(CategoryOut):
    children: list[CategoryNode] = []


class CategoryRef(ApiModel):
    id: uuid.UUID
    name: str
    slug: str


# ------------------------------------------------------------------ variants


class VariantCreate(StrictInput):
    sku: Sku
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    attributes: dict[AttributeKey, AttributeValue] = Field(default_factory=dict, max_length=20)
    price: Money
    sale_price: Money | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None

    @model_validator(mode="after")
    def sale_is_consistent(self) -> VariantCreate:
        validate_sale(self.price, self.sale_price, self.sale_starts_at, self.sale_ends_at)
        return self


class VariantUpdate(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    attributes: dict[AttributeKey, AttributeValue] | None = Field(None, max_length=20)
    price: Money | None = None
    # Explicit null clears the sale; omitted leaves it unchanged (see model_fields_set).
    sale_price: Money | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None
    is_active: bool | None = None


def validate_sale(price: Money, sale: Money | None, starts: datetime | None, ends: datetime | None) -> None:
    if sale is not None:
        if sale.currency != price.currency:
            raise ValueError("salePrice currency must match price currency")
        if sale.amount_minor >= price.amount_minor:
            raise ValueError("salePrice must be lower than price")
    if starts and ends and starts >= ends:
        raise ValueError("saleStartsAt must be before saleEndsAt")
    for value in (starts, ends):
        if value is not None and value.tzinfo is None:
            raise ValueError("sale timestamps must include a timezone (use UTC, e.g. 2026-10-14T00:00:00Z)")


class VariantOut(ApiModel):
    id: uuid.UUID
    sku: str
    name: str | None
    attributes: dict[str, str]
    price: Money
    sale_price: Money | None
    sale_starts_at: datetime | None
    sale_ends_at: datetime | None
    effective_price: Money
    on_sale: bool
    is_active: bool


# ------------------------------------------------------------------ products


class ImageIn(StrictInput):
    url: AnyHttpUrl
    alt: Annotated[str, StringConstraints(max_length=300)] = ""


class ImageOut(ApiModel):
    url: str
    alt: str


class SpecificationIn(StrictInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class SpecificationOut(ApiModel):
    name: str
    value: str


ProductStatusIn = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]


class ProductCreate(StrictInput):
    name: Name
    slug: Slug | None = None
    description: str = Field("", max_length=20_000)
    brand: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    category_id: uuid.UUID
    status: Literal["DRAFT", "PUBLISHED"] = "DRAFT"
    featured: bool = False
    variants: list[VariantCreate] = Field(min_length=1, max_length=100)
    images: list[ImageIn] = Field(default_factory=list, max_length=20)
    specifications: list[SpecificationIn] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def one_currency_and_unique_skus(self) -> ProductCreate:
        if len({v.price.currency for v in self.variants}) > 1:
            raise ValueError("all variants of a product must use the same currency")
        skus = [v.sku for v in self.variants]
        if len(skus) != len(set(skus)):
            raise ValueError("variant SKUs must be unique")
        return self


class ProductUpdate(StrictInput):
    name: Name | None = None
    slug: Slug | None = None
    description: str | None = Field(None, max_length=20_000)
    brand: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    category_id: uuid.UUID | None = None
    status: ProductStatusIn | None = None
    featured: bool | None = None
    images: list[ImageIn] | None = Field(None, max_length=20)
    specifications: list[SpecificationIn] | None = Field(None, max_length=50)


class ProductSummary(ApiModel):
    """Listing card: the cheapest active variant's effective price."""

    id: uuid.UUID
    slug: str
    name: str
    brand: str | None
    category: CategoryRef
    lowest_price: Money
    on_sale: bool
    featured: bool
    primary_image: ImageOut | None
    status: str


class ProductDetail(ApiModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    brand: str | None
    category: CategoryRef
    status: str
    featured: bool
    variants: list[VariantOut]
    images: list[ImageOut]
    specifications: list[SpecificationOut]
    version: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PageInfo(ApiModel):
    page: int
    page_size: int
    total: int


class ProductPage(ApiModel):
    data: list[ProductSummary]
    page: PageInfo


# ------------------------------------------------------------------ internal


class PriceLookupRequest(StrictInput):
    skus: list[Sku] = Field(min_length=1, max_length=100)


class PriceLookupItem(ApiModel):
    sku: str
    product_id: uuid.UUID
    variant_id: uuid.UUID
    name: str
    unit_price: Money


class PriceLookupResponse(ApiModel):
    items: list[PriceLookupItem]
    unavailable: list[str] = Field(description="SKUs that are unknown, inactive, or not published")
