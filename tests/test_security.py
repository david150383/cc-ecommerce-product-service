import pytest


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/products"),
            ("patch", "/api/v1/products/0192a1b2-0000-7000-8000-000000000000"),
            ("delete", "/api/v1/products/0192a1b2-0000-7000-8000-000000000000"),
            ("post", "/api/v1/categories"),
            ("post", "/internal/v1/products/price-lookup"),
        ],
    )
    def test_writes_require_authentication(self, client, method, path):
        kwargs = {} if method == "delete" else {"json": {}}
        res = getattr(client, method)(path, **kwargs)
        assert res.status_code == 401
        assert res.headers["www-authenticate"].startswith("Bearer")
        assert res.json()["code"] == "UNAUTHENTICATED"

    def test_customers_cannot_manage_the_catalog(self, client, customer_headers):
        res = client.post("/api/v1/categories", json={"name": "X"}, headers=customer_headers)
        assert res.status_code == 403

    def test_only_service_tokens_may_use_internal_endpoints(self, client, admin_headers, service_headers):
        body = {"skus": ["ANY-SKU"]}
        assert client.post("/internal/v1/products/price-lookup", json=body, headers=admin_headers).status_code == 403
        assert client.post("/internal/v1/products/price-lookup", json=body, headers=service_headers).status_code == 200

    @pytest.mark.parametrize(
        "token_kwargs",
        [
            {"ttl": -120},  # expired (beyond the 30 s leeway)
            {"audience": "ecommerce-notification-service"},  # minted for another service
            {"iss": "someone-else"},
        ],
    )
    def test_invalid_tokens_are_rejected_even_on_public_endpoints(self, client, make_token, token_kwargs):
        token = make_token(["admin"], **token_kwargs)
        assert client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    @pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")  # the forged HS256 token
    def test_tokens_signed_by_another_key_or_unsigned_are_rejected(self, client):
        import time

        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        claims = {
            "sub": "x",
            "roles": ["admin"],
            "iss": "ecommerce-auth-service",
            "aud": "ecommerce-api",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(claims, other_key, algorithm="RS256", headers={"kid": "test-key"})
        unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "test-key"})
        hs256 = jwt.encode(claims, "guessable-secret", algorithm="HS256", headers={"kid": "test-key"})
        for token in (forged, unsigned, hs256):
            res = client.post(
                "/api/v1/categories", json={"name": "Pwned"}, headers={"Authorization": f"Bearer {token}"}
            )
            assert res.status_code == 401, token
        assert client.get("/api/v1/categories").json() == []

    def test_malformed_authorization_header(self, client):
        assert client.get("/api/v1/products", headers={"Authorization": "Basic abc"}).status_code == 401
