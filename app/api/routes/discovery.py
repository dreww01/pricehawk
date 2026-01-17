from urllib.parse import urlparse

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
    description="Add discovered products to a tracking group for price monitoring. Stores discovered prices directly - no re-scraping needed.",
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

    # Add competitors for each product URL (extract domain as retailer_name)
    competitors_data = []
    for product in body.products:
        parsed = urlparse(product.url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        competitors_data.append({
            "product_id": group_id,
            "url": product.url,
            "retailer_name": domain,
            "alert_threshold_percent": float(body.alert_threshold_percent),
        })

    competitors_result = client.table("competitors").insert(competitors_data).execute()
    competitors = competitors_result.data or []
    products_added = len(competitors)

    # Store discovered prices directly in price_history (no re-scraping)
    # Use service client to bypass RLS (price_history has no INSERT policy for users)
    service_client = get_supabase_client()
    prices_stored = 0
    for product, competitor in zip(body.products, competitors):
        if product.price is not None:
            price_data = {
                "competitor_id": competitor["id"],
                "price": float(product.price),
                "currency": product.currency,
                "scrape_status": "success",
                "error_message": None,
            }
            service_client.table("price_history").insert(price_data).execute()
            prices_stored += 1

    return TrackProductsResponse(
        group_id=group_id,
        group_name=body.group_name,
        products_added=products_added,
        prices_stored=prices_stored,
    )
