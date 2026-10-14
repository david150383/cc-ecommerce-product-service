"""Verifies access tokens issued by ecommerce-auth-service.

This service never calls the Auth Service per request: it fetches the public keys
(JWKS) once, caches them for 10 minutes, and verifies RS256 signatures locally. An
unknown `kid` (key rotation) triggers a refetch.
"""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

import jwt
from fastapi import Depends, Header, Request
from jwt import PyJWK, PyJWKClient, PyJWKClientConnectionError, PyJWKSet

from app.errors import ProblemError

Role = Literal["customer", "admin", "service"]


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


class SigningKeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> PyJWK: ...


class StaticKeyResolver:
    """Fixed key set (tests, or air-gapped deployments)."""

    def __init__(self, jwks: dict) -> None:
        self._keys = PyJWKSet.from_dict(jwks)

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        for key in self._keys.keys:
            if key.key_id == kid:
                return key
        raise jwt.InvalidTokenError("unknown signing key")


class TokenVerifier:
    def __init__(self, resolver: SigningKeyResolver, issuer: str, audience: str | list[str]) -> None:
        self._resolver = resolver
        self._issuer = issuer
        self._audience = audience

    @classmethod
    def from_jwks_url(cls, url: str, issuer: str, audience: str | list[str]) -> TokenVerifier:
        return cls(PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5), issuer, audience)

    def verify(self, token: str) -> Principal:
        try:
            key = self._resolver.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],  # never trust the token's own "alg" header
                issuer=self._issuer,
                audience=self._audience,
                leeway=30,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except PyJWKClientConnectionError as exc:
            # Auth is unreachable and the key is not cached: our problem, not the client's.
            raise ProblemError(503, "DEPENDENCY_UNAVAILABLE", "Token keys unavailable") from exc
        except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
            raise ProblemError(401, "UNAUTHENTICATED", "Invalid or expired access token") from exc

        roles = claims.get("roles")
        if not isinstance(roles, list):
            raise ProblemError(401, "UNAUTHENTICATED", "Invalid or expired access token")
        return Principal(subject=str(claims["sub"]), roles=frozenset(str(r) for r in roles))


def optional_principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal | None:
    """Anonymous callers are allowed (public catalog); a *bad* token is still rejected."""
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(401, "UNAUTHENTICATED", "Authentication required")
    verifier: TokenVerifier = request.app.state.token_verifier
    return verifier.verify(token)


def require_principal(principal: Annotated[Principal | None, Depends(optional_principal)]) -> Principal:
    if principal is None:
        raise ProblemError(401, "UNAUTHENTICATED", "Authentication required")
    return principal


def require_role(role: Role):
    def dependency(principal: Annotated[Principal, Depends(require_principal)]) -> Principal:
        if role not in principal.roles:
            raise ProblemError(403, "FORBIDDEN", "Insufficient permissions")
        return principal

    return dependency


OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
AdminPrincipal = Annotated[Principal, Depends(require_role("admin"))]
ServicePrincipal = Annotated[Principal, Depends(require_role("service"))]
