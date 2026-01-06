from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_supabase_client
from app.db.models import (
    StoreDiscoveryRequest,
    StoreDiscoveryResponse,
    DiscoveredProductResponse,
    TrackProductsRequest,
    TrackProductsResponse,
)
from app.services.store_discovery import discover_products


router = APIRouter(prefix="/stores", tags=["discovery"])
security = HTTPBearer()


@router.post(
    "/discover",
    response_model=StoreDiscoveryResponse,
    summary="Discover products from store",
    description="Detect store platform and fetch products. Supports Shopify, WooCommerce, Amazon, eBay, and custom stores.",
)
async def discover_store_products(
    body: StoreDiscoveryRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> StoreDiscoveryResponse:
    result = await discover_products(
        url=body.url,
        keyword=body.keyword,
        limit=body.limit,
    )

    # Convert internal models to response models
    products = [
        DiscoveredProductResponse(
            name=p.name,
            price=p.price,
            currency=p.currency,
            image_url=p.image_url,
            product_url=p.product_url,
            platform=p.platform,
            variant_id=p.variant_id,
            sku=p.sku,
            in_stock=p.in_stock,
        )
        for p in result.products
    ]

    return StoreDiscoveryResponse(
        platform=result.platform,
        store_url=result.store_url,
        total_found=result.total_found,
        products=products,
        error=result.error,
    )


@router.post(
    "/track",
    response_model=TrackProductsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Track discovered products",
    description="Add discovered products to a tracking group for price monitoring.",
)
async def track_products(
    body: TrackProductsRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> TrackProductsResponse:
    client = get_supabase_client(credentials.credentials)

    # Create product group
    group_data = {
        "user_id": current_user.id,
        "product_name": body.group_name,
    }
    group_result = client.table("products").insert(group_data).execute()

    if not group_result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create product group",
        )

    group = group_result.data[0]
    group_id = group["id"]

    # Add competitors for each product URL
    competitors_data = [
        {
            "product_id": group_id,
            "url": url,
            "alert_threshold_percent": float(body.alert_threshold_percent),
        }
        for url in body.product_urls
    ]

    competitors_result = client.table("competitors").insert(competitors_data).execute()

    return TrackProductsResponse(
        group_id=group_id,
        group_name=body.group_name,
        products_added=len(competitors_result.data or []),
    )
