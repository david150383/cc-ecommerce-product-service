from datetime import UTC, datetime, timedelta

from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from alembic import command
from app.models import Variant
from app.pricing import effective_price_minor, sql_effective_price
from tests.conftest import TEST_DATABASE_URL, variant


class TestPriceLookup:
    def test_returns_current_purchasable_prices_and_lists_the_rest_as_unavailable(
        self, client, create_product, service_headers, admin_headers
    ):
        create_product(
            "Tee",
            variants=[
                variant("TEE-1", 1000, name="Black"),
                variant("TEE-2", 2000, salePrice={"amountMinor": 1500, "currency": "INR"}),
            ],
        )
        create_product("Draft", status="DRAFT", variants=[variant("DRAFT-1", 500)])
        res = client.post(
            "/internal/v1/products/price-lookup",
            json={"skus": ["TEE-2", "TEE-1", "DRAFT-1", "NOPE-1", "TEE-1"]},
            headers=service_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert [(i["sku"], i["name"], i["unitPrice"]["amountMinor"]) for i in body["items"]] == [
            ("TEE-2", "Tee", 1500),  # effective (sale) price
            ("TEE-1", "Tee, Black", 1000),
        ]
        assert body["unavailable"] == ["DRAFT-1", "NOPE-1"]

    def test_limits_batch_size(self, client, service_headers):
        skus = [f"SKU-{i:03d}" for i in range(101)]
        assert (
            client.post("/internal/v1/products/price-lookup", json={"skus": skus}, headers=service_headers).status_code
            == 400
        )


class TestPricingRuleAgreesInPythonAndSql:
    def test_same_answer_for_every_kind_of_sale_window(self, engine, create_product):
        now = datetime.now(UTC)
        sale = {"amountMinor": 500, "currency": "INR"}

        def iso(d):
            return d.isoformat().replace("+00:00", "Z")

        cases = {
            "NO-SALE": variant("NO-SALE", 1000),
            "OPEN-SALE": variant("OPEN-SALE", 1000, salePrice=sale),
            "FUTURE": variant("FUTURE", 1000, salePrice=sale, saleStartsAt=iso(now + timedelta(hours=1))),
            "PAST": variant("PAST", 1000, salePrice=sale, saleEndsAt=iso(now - timedelta(hours=1))),
            "WINDOW": variant(
                "WINDOW",
                1000,
                salePrice=sale,
                saleStartsAt=iso(now - timedelta(hours=1)),
                saleEndsAt=iso(now + timedelta(hours=1)),
            ),
        }
        create_product("Matrix", variants=list(cases.values()))
        with Session(engine) as s:
            rows = s.execute(select(Variant, sql_effective_price())).all()
            check_time = datetime.now(UTC)
            results = {v.sku: (sql_price, effective_price_minor(v, check_time)) for v, sql_price in rows}
        assert results == {
            "NO-SALE": (1000, 1000),
            "OPEN-SALE": (500, 500),
            "FUTURE": (1000, 1000),
            "PAST": (1000, 1000),
            "WINDOW": (500, 500),
        }


class TestMigrations:
    def test_models_and_migrations_have_not_drifted(self, engine):
        cfg = Config("alembic.ini")
        cfg.attributes["url"] = TEST_DATABASE_URL
        cfg.attributes["configure_logger"] = False
        command.check(cfg)  # raises if `alembic revision --autogenerate` would produce anything

    def test_database_constraints_hold_even_without_the_api(self, engine, create_product):
        import pytest
        from sqlalchemy.exc import IntegrityError

        product = create_product("Tee", variants=[variant("DB-1", 1000)])
        with Session(engine) as s:
            v = s.scalar(select(Variant).where(Variant.sku == "DB-1"))
            v.sale_price_minor = 2000  # sale above price
            with pytest.raises(IntegrityError, match="ck_product_variants_sale_below_price"):
                s.flush()
        assert product["id"]
