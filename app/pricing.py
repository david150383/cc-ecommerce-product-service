"""The single definition of "effective price", in Python and in SQL.

A sale price applies only inside its (optional) window. Both functions encode the same
rule; tests run them against the same rows to prove they agree.
"""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, case, func, or_

from app.models import Variant


def is_on_sale(variant: Variant, now: datetime) -> bool:
    return (
        variant.sale_price_minor is not None
        and (variant.sale_starts_at is None or variant.sale_starts_at <= now)
        and (variant.sale_ends_at is None or variant.sale_ends_at > now)
    )


def effective_price_minor(variant: Variant, now: datetime) -> int:
    if is_on_sale(variant, now) and variant.sale_price_minor is not None:
        return variant.sale_price_minor
    return variant.price_minor


def sql_on_sale() -> ColumnElement[bool]:
    return and_(
        Variant.sale_price_minor.is_not(None),
        or_(Variant.sale_starts_at.is_(None), Variant.sale_starts_at <= func.now()),
        or_(Variant.sale_ends_at.is_(None), Variant.sale_ends_at > func.now()),
    )


def sql_effective_price() -> ColumnElement[int]:
    return case((sql_on_sale(), Variant.sale_price_minor), else_=Variant.price_minor)
