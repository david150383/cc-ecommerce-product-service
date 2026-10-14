"""Loads a small demo catalog for local development. Idempotent: does nothing if any
category exists. Never run against production.

    python -m app.seed
"""

from app.catalog import create_category, create_product
from app.config import get_settings
from app.db import create_db_engine, session_factory
from app.models import Category
from app.schemas import CategoryCreate, ProductCreate

INR = "INR"


def money(amount_minor: int) -> dict:
    return {"amountMinor": amount_minor, "currency": INR}


def tee_variants(prefix: str, price: int, sale: int | None = None) -> list[dict]:
    out = []
    for color in ("BLK", "WHT"):
        for size in ("S", "M", "L"):
            v = {
                "sku": f"{prefix}-{color}-{size}",
                "name": f"{'Black' if color == 'BLK' else 'White'}, {size}",
                "attributes": {"color": color.lower(), "size": size},
                "price": money(price),
            }
            if sale:
                v["salePrice"] = money(sale)
            out.append(v)
    return out


def main() -> None:
    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit("refusing to seed a production database")
    engine = create_db_engine(settings.database_url)
    with session_factory(engine)() as session, session.begin():
        if session.query(Category).count() > 0:
            print("catalog already has data; skipping seed")
            return
        clothing = create_category(session, CategoryCreate(name="Clothing", position=0))
        tees = create_category(session, CategoryCreate(name="T-Shirts", parent_id=clothing.id))
        shirts = create_category(session, CategoryCreate(name="Shirts", parent_id=clothing.id))
        footwear = create_category(session, CategoryCreate(name="Footwear", position=1))
        accessories = create_category(session, CategoryCreate(name="Accessories", position=2))

        products = [
            dict(
                name="Classic Cotton Tee",
                brand="Acme",
                category_id=tees.id,
                featured=True,
                description="A soft, breathable everyday tee in 100% combed cotton.",
                variants=tee_variants("TEE-CLASSIC", 59900),
            ),
            dict(
                name="Graphic Mountain Tee",
                brand="Trailhead",
                category_id=tees.id,
                description="Organic cotton tee with a mountain print. Now on sale.",
                variants=tee_variants("TEE-MOUNTAIN", 89900, sale=69900),
            ),
            dict(
                name="Oxford Button-Down Shirt",
                brand="Acme",
                category_id=shirts.id,
                description="Crisp oxford cloth, regular fit, for office or weekend.",
                variants=[
                    {"sku": f"SHIRT-OXF-{s}", "name": s, "attributes": {"size": s}, "price": money(249900)}
                    for s in ("M", "L")
                ],
            ),
            dict(
                name="Trail Running Shoes",
                brand="Trailhead",
                category_id=footwear.id,
                featured=True,
                description="Lightweight running shoes with a grippy sole for rough trails.",
                variants=[
                    {
                        "sku": f"SHOE-TRAIL-{n}",
                        "name": f"UK {n}",
                        "attributes": {"size": str(n)},
                        "price": money(549900),
                    }
                    for n in (8, 9, 10)
                ],
            ),
            dict(
                name="Canvas Tote Bag",
                brand="Acme",
                category_id=accessories.id,
                description="Sturdy canvas tote for groceries and books.",
                variants=[{"sku": "BAG-TOTE-NAT", "name": "Natural", "price": money(39900)}],
            ),
        ]
        for p in products:
            create_product(
                session,
                ProductCreate.model_validate(
                    {
                        **{k: v for k, v in p.items() if k != "category_id"},
                        "categoryId": p["category_id"],
                        "status": "PUBLISHED",
                        "images": [
                            {
                                "url": f"https://picsum.photos/seed/{p['name'].replace(' ', '-').lower()}/800/800",
                                "alt": p["name"],
                            }
                        ],
                    }
                ),
            )
        print(f"seeded 5 categories and {len(products)} products")


if __name__ == "__main__":
    main()
