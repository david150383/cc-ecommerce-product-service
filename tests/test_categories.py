class TestCategories:
    def test_tree_nests_subcategories(self, client, create_category):
        clothing = create_category("Clothing")
        create_category("Shirts", parentId=clothing["id"])
        create_category("Shoes")
        tree = client.get("/api/v1/categories").json()
        assert [(c["name"], [k["name"] for k in c["children"]]) for c in tree] == [
            ("Clothing", ["Shirts"]),
            ("Shoes", []),
        ]

    def test_category_products_include_all_descendants(self, client, create_category, create_product):
        clothing = create_category("Clothing")
        shirts = create_category("Shirts", parentId=clothing["id"])
        formal = create_category("Formal", parentId=shirts["id"])
        shoes = create_category("Shoes")
        create_product("Tee", categoryId=shirts["id"])
        create_product("Dress Shirt", categoryId=formal["id"])
        create_product("Sneaker", categoryId=shoes["id"])
        names = {p["name"] for p in client.get(f"/api/v1/categories/{clothing['id']}/products").json()["data"]}
        assert names == {"Tee", "Dress Shirt"}
        assert client.get("/api/v1/categories/0192a1b2-0000-7000-8000-000000000000/products").status_code == 404

    def test_moving_a_category_under_its_own_descendant_is_rejected(self, client, create_category, admin_headers):
        a = create_category("A")
        b = create_category("B", parentId=a["id"])
        c = create_category("C", parentId=b["id"])
        res = client.patch(f"/api/v1/categories/{a['id']}", json={"parentId": c["id"]}, headers=admin_headers)
        assert res.status_code == 409
        assert res.json()["code"] == "CATEGORY_CYCLE"
        self_parent = client.patch(f"/api/v1/categories/{a['id']}", json={"parentId": a["id"]}, headers=admin_headers)
        assert self_parent.status_code == 409

    def test_only_empty_categories_can_be_deleted(self, client, create_category, create_product, admin_headers):
        parent = create_category("Parent")
        child = create_category("Child", parentId=parent["id"])
        create_product("Thing", categoryId=child["id"])
        assert (
            client.delete(f"/api/v1/categories/{parent['id']}", headers=admin_headers).json()["code"]
            == "CATEGORY_NOT_EMPTY"
        )
        assert client.delete(f"/api/v1/categories/{child['id']}", headers=admin_headers).status_code == 409
        empty = create_category("Empty")
        assert client.delete(f"/api/v1/categories/{empty['id']}", headers=admin_headers).status_code == 204

    def test_duplicate_category_slug_is_409(self, client, create_category, admin_headers):
        create_category("Shoes", slug="shoes")
        res = client.post("/api/v1/categories", json={"name": "Shoes 2", "slug": "shoes"}, headers=admin_headers)
        assert res.json()["code"] == "SLUG_TAKEN"
