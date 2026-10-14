from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import variant


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TestCreateAndRead:
    def test_admin_creates_a_product_with_variants_images_and_specs(self, client, admin_headers, create_category):
        cat = create_category("T-Shirts")
        res = client.post(
            "/api/v1/products",
            headers=admin_headers,
            json={
                "name": "Classic Tee",
                "brand": "Acme",
                "description": "Soft **cotton** tee.",
                "categoryId": cat["id"],
                "status": "PUBLISHED",
                "variants": [
                    variant("TEE-BLK-M", 59900, attributes={"color": "black", "size": "M"}, name="Black, M"),
                    variant("TEE-BLK-L", 59900, attributes={"color": "black", "size": "L"}),
                ],
                "images": [{"url": "https://cdn.example.com/tee.jpg", "alt": "Front"}],
                "specifications": [{"name": "Material", "value": "100% cotton"}],
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert res.headers["location"] == f"/api/v1/products/{body['id']}"
        assert res.headers["etag"] == '"1"'
        assert body["slug"] == "classic-tee"
        assert body["category"] == {"id": cat["id"], "name": "T-Shirts", "slug": "t-shirts"}
        assert [v["sku"] for v in body["variants"]] == ["TEE-BLK-M", "TEE-BLK-L"]
        assert body["variants"][0]["price"] == {"amountMinor": 59900, "currency": "INR"}
        assert body["variants"][0]["effectivePrice"] == {"amountMinor": 59900, "currency": "INR"}
        assert body["publishedAt"] is not None
        assert body["specifications"] == [{"name": "Material", "value": "100% cotton"}]

    def test_public_reads_by_id_and_slug(self, client, create_product):
        p = create_product("Linen Shirt")
        assert client.get(f"/api/v1/products/{p['id']}").json()["name"] == "Linen Shirt"
        assert client.get("/api/v1/products/by-slug/linen-shirt").json()["id"] == p["id"]

    def test_drafts_are_invisible_to_the_public_but_visible_to_admins(self, client, create_product, admin_headers):
        p = create_product("Secret Launch", status="DRAFT")
        assert client.get(f"/api/v1/products/{p['id']}").status_code == 404
        assert client.get("/api/v1/products").json()["page"]["total"] == 0
        assert client.get(f"/api/v1/products/{p['id']}", headers=admin_headers).status_code == 200
        drafts = client.get("/api/v1/products?status=DRAFT", headers=admin_headers).json()
        assert [d["name"] for d in drafts["data"]] == ["Secret Launch"]

    def test_customers_cannot_filter_by_status(self, client, customer_headers):
        assert client.get("/api/v1/products?status=DRAFT", headers=customer_headers).status_code == 403

    def test_duplicate_explicit_slug_and_sku_are_rejected_by_the_database(self, client, admin_headers, create_product):
        first = create_product("Tee", slug="tee", variants=[variant("TEE-1", 100)])
        dup_slug = client.post(
            "/api/v1/products",
            headers=admin_headers,
            json={
                "name": "Other",
                "slug": "tee",
                "categoryId": first["category"]["id"],
                "variants": [variant("TEE-2", 1)],
            },
        )
        assert dup_slug.status_code == 409
        assert dup_slug.json()["code"] == "SLUG_TAKEN"
        dup_sku = client.post(
            "/api/v1/products",
            headers=admin_headers,
            json={"name": "Other", "categoryId": first["category"]["id"], "variants": [variant("TEE-1", 1)]},
        )
        assert dup_sku.json()["code"] == "SKU_TAKEN"

    def test_auto_slug_collision_gets_a_suffix(self, create_product):
        a = create_product("Same Name")
        b = create_product("Same Name")
        assert a["slug"] == "same-name"
        assert b["slug"].startswith("same-name-") and b["slug"] != a["slug"]

    def test_invalid_input_is_rejected(self, client, admin_headers, create_category):
        cat = create_category()

        def post(**over):
            body = {"name": "X", "categoryId": cat["id"], "variants": [variant("GOOD-SKU", 100)], **over}
            return client.post("/api/v1/products", headers=admin_headers, json=body)

        assert post(variants=[]).status_code == 400
        assert post(variants=[variant("bad sku", 100)]).status_code == 400
        assert post(variants=[variant("NEG-1", -1)]).status_code == 400
        assert post(variants=[{"sku": "FLT-1", "price": {"amountMinor": 9.99, "currency": "INR"}}]).status_code == 400
        assert post(variants=[variant("A-1", 100), variant("B-1", 100, currency="USD")]).status_code == 400
        assert post(variants=[variant("DUP-1", 1), variant("DUP-1", 2)]).status_code == 400
        assert (
            post(variants=[variant("S-1", 100, salePrice={"amountMinor": 150, "currency": "INR"})]).status_code == 400
        )
        assert post(unknownField=True).status_code == 400
        assert post(categoryId="0192a1b2-0000-7000-8000-000000000000").status_code == 404


class TestListing:
    def test_search_ranks_name_matches_above_description_matches(self, client, create_product):
        create_product("Running Shoes", description="Lightweight trainers")
        create_product("Wool Socks", description="Great with running shoes")
        create_product("Rain Jacket", description="Waterproof")
        res = client.get("/api/v1/products", params={"q": "running shoes"})
        assert [p["name"] for p in res.json()["data"]] == ["Running Shoes", "Wool Socks"]

    def test_search_uses_stemming(self, client, create_product):
        create_product("Hiking Boots")
        assert client.get("/api/v1/products", params={"q": "boot"}).json()["page"]["total"] == 1

    def test_price_filters_and_sorts_use_the_effective_price(self, client, create_product):
        create_product("Cheap", variants=[variant("CHEAP-1", 1000)])
        create_product(
            "On Sale", variants=[variant("SALE-1", 9000, salePrice={"amountMinor": 2000, "currency": "INR"})]
        )
        create_product("Pricey", variants=[variant("PRICEY-1", 5000)])

        asc = client.get("/api/v1/products", params={"sort": "price_asc"}).json()["data"]
        assert [(p["name"], p["lowestPrice"]["amountMinor"], p["onSale"]) for p in asc] == [
            ("Cheap", 1000, False),
            ("On Sale", 2000, True),
            ("Pricey", 5000, False),
        ]
        ranged = client.get("/api/v1/products", params={"minPrice": 1500, "maxPrice": 4000}).json()["data"]
        assert [p["name"] for p in ranged] == ["On Sale"]

    def test_lowest_price_ignores_inactive_variants(self, client, create_product, admin_headers):
        p = create_product("Tee", variants=[variant("TEE-CHEAP", 100), variant("TEE-NORMAL", 500)])
        cheap = next(v for v in p["variants"] if v["sku"] == "TEE-CHEAP")
        client.patch(
            f"/api/v1/products/{p['id']}/variants/{cheap['id']}", json={"isActive": False}, headers=admin_headers
        )
        listed = client.get("/api/v1/products").json()["data"][0]
        assert listed["lowestPrice"]["amountMinor"] == 500
        public_detail = client.get(f"/api/v1/products/{p['id']}").json()
        assert [v["sku"] for v in public_detail["variants"]] == ["TEE-NORMAL"]

    def test_pagination_is_stable_and_complete(self, client, create_product):
        for i in range(7):
            create_product(f"Item {i}")
        seen = []
        for page in (1, 2, 3):
            body = client.get("/api/v1/products", params={"page": page, "pageSize": 3, "sort": "name"}).json()
            assert body["page"]["total"] == 7
            seen += [p["name"] for p in body["data"]]
        assert seen == sorted(seen) and len(set(seen)) == 7

    def test_featured_filter_and_query_validation(self, client, create_product):
        create_product("Hero", featured=True)
        create_product("Regular")
        assert [p["name"] for p in client.get("/api/v1/products?featured=true").json()["data"]] == ["Hero"]
        assert client.get("/api/v1/products?pageSize=1000").status_code == 400
        assert client.get("/api/v1/products?sort=relevance").status_code == 400
        assert client.get("/api/v1/products?sort=random").status_code == 400


class TestUpdates:
    def test_patch_updates_only_sent_fields_and_bumps_the_etag(self, client, create_product, admin_headers):
        p = create_product("Tee", brand="Acme")
        res = client.patch(f"/api/v1/products/{p['id']}", json={"name": "Better Tee"}, headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["brand"] == "Acme"
        assert res.headers["etag"] == '"2"'

    def test_stale_if_match_is_rejected_with_412(self, client, create_product, admin_headers):
        p = create_product("Tee")
        etag = client.get(f"/api/v1/products/{p['id']}").headers["etag"]
        # Admin A saves first...
        ok = client.patch(
            f"/api/v1/products/{p['id']}", json={"name": "A"}, headers={**admin_headers, "If-Match": etag}
        )
        assert ok.status_code == 200
        # ...admin B, still holding the old ETag, must not silently overwrite A.
        stale = client.patch(
            f"/api/v1/products/{p['id']}", json={"name": "B"}, headers={**admin_headers, "If-Match": etag}
        )
        assert stale.status_code == 412
        assert stale.json()["code"] == "VERSION_MISMATCH"
        assert client.get(f"/api/v1/products/{p['id']}").json()["name"] == "A"

    def test_publishing_requires_an_active_variant(self, client, create_product, admin_headers):
        p = create_product("Tee", status="DRAFT", variants=[variant("ONLY-1", 100)])
        only = p["variants"][0]
        client.patch(
            f"/api/v1/products/{p['id']}/variants/{only['id']}", json={"isActive": False}, headers=admin_headers
        )
        res = client.patch(f"/api/v1/products/{p['id']}", json={"status": "PUBLISHED"}, headers=admin_headers)
        assert res.status_code == 409
        assert res.json()["code"] == "PRODUCT_NOT_PUBLISHABLE"

    def test_cannot_deactivate_the_last_variant_of_a_published_product(self, client, create_product, admin_headers):
        p = create_product("Tee", variants=[variant("ONLY-2", 100)])
        res = client.patch(
            f"/api/v1/products/{p['id']}/variants/{p['variants'][0]['id']}",
            json={"isActive": False},
            headers=admin_headers,
        )
        assert res.status_code == 409

    def test_archive_is_a_soft_delete_and_idempotent(self, client, create_product, admin_headers):
        p = create_product("Old Tee")
        for _ in range(2):
            assert client.delete(f"/api/v1/products/{p['id']}", headers=admin_headers).status_code == 204
        assert client.get(f"/api/v1/products/{p['id']}").status_code == 404
        assert client.get(f"/api/v1/products/{p['id']}", headers=admin_headers).json()["status"] == "ARCHIVED"

    def test_variants_can_be_added_and_repriced_but_not_in_another_currency(
        self, client, create_product, admin_headers
    ):
        p = create_product("Tee", variants=[variant("TEE-S", 1000)])
        added = client.post(f"/api/v1/products/{p['id']}/variants", json=variant("TEE-XL", 1200), headers=admin_headers)
        assert added.status_code == 201
        assert [v["sku"] for v in added.json()["variants"]] == ["TEE-S", "TEE-XL"]
        usd = client.post(
            f"/api/v1/products/{p['id']}/variants", json=variant("TEE-XXL", 15, "USD"), headers=admin_headers
        )
        assert usd.json()["code"] == "CURRENCY_MISMATCH"

        vid = p["variants"][0]["id"]
        repriced = client.patch(
            f"/api/v1/products/{p['id']}/variants/{vid}",
            json={"price": {"amountMinor": 1500, "currency": "INR"}},
            headers=admin_headers,
        )
        assert repriced.json()["variants"][0]["price"]["amountMinor"] == 1500

    def test_a_new_price_below_the_existing_sale_price_is_rejected(self, client, create_product, admin_headers):
        p = create_product(
            "Tee", variants=[variant("TEE-SALE", 1000, salePrice={"amountMinor": 800, "currency": "INR"})]
        )
        vid = p["variants"][0]["id"]
        res = client.patch(
            f"/api/v1/products/{p['id']}/variants/{vid}",
            json={"price": {"amountMinor": 700, "currency": "INR"}},
            headers=admin_headers,
        )
        assert res.status_code == 422
        assert res.json()["code"] == "INVALID_PRICING"
        assert client.get(f"/api/v1/products/{p['id']}").json()["variants"][0]["price"]["amountMinor"] == 1000


class TestSaleWindows:
    def test_sale_applies_only_inside_its_window(self, client: TestClient, create_product):
        now = datetime.now(UTC)
        sale = {"amountMinor": 500, "currency": "INR"}
        create_product(
            "Future Sale",
            variants=[variant("FUT-1", 1000, salePrice=sale, saleStartsAt=iso(now + timedelta(days=1)))],
        )
        create_product(
            "Active Sale",
            variants=[
                variant(
                    "ACT-1",
                    1000,
                    salePrice=sale,
                    saleStartsAt=iso(now - timedelta(days=1)),
                    saleEndsAt=iso(now + timedelta(days=1)),
                )
            ],
        )
        create_product(
            "Ended Sale", variants=[variant("END-1", 1000, salePrice=sale, saleEndsAt=iso(now - timedelta(minutes=1)))]
        )
        listing = {
            p["name"]: (p["lowestPrice"]["amountMinor"], p["onSale"])
            for p in client.get("/api/v1/products").json()["data"]
        }
        assert listing == {"Future Sale": (1000, False), "Active Sale": (500, True), "Ended Sale": (1000, False)}

    def test_naive_timestamps_are_rejected(self, client, admin_headers, create_category):
        cat = create_category()
        body = {
            "name": "X",
            "categoryId": cat["id"],
            "variants": [
                variant(
                    "NAIVE-1", 1000, salePrice={"amountMinor": 1, "currency": "INR"}, saleEndsAt="2030-01-01T00:00:00"
                )
            ],
        }
        assert client.post("/api/v1/products", json=body, headers=admin_headers).status_code == 400
