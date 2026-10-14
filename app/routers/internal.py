from fastapi import APIRouter

from app import catalog
from app.deps import DbSession
from app.schemas import PriceLookupRequest, PriceLookupResponse
from app.security import ServicePrincipal

# Service-to-service only (a token with role "service"); never exposed by the public gateway.
router = APIRouter(prefix="/internal/v1/products", tags=["internal"])


@router.post("/price-lookup", response_model=PriceLookupResponse, summary="Current purchasable prices by SKU")
def price_lookup(body: PriceLookupRequest, session: DbSession, _service: ServicePrincipal) -> PriceLookupResponse:
    return catalog.price_lookup(session, body.skus)
