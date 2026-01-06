from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_supabase_client
from app.db.models import (
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    CompetitorResponse,
)


router = APIRouter(prefix="/products", tags=["products"])
security = HTTPBearer()


def _build_product_response(product: dict, competitors: list[dict]) -> ProductResponse:
    """Build ProductResponse from database rows."""
    return ProductResponse(
        id=product["id"],
        product_name=product["product_name"],
        is_active=product["is_active"],
        created_at=product["created_at"],
        updated_at=product["updated_at"],
        competitors=[
            CompetitorResponse(
                id=c["id"],
                url=c["url"],
                retailer_name=c["retailer_name"],
                alert_threshold_percent=c["alert_threshold_percent"],
                created_at=c["created_at"],
            )
            for c in competitors
        ],
    )


@router.get(
    "",
    response_model=ProductListResponse,
    summary="List tracked products",
    description="Get all tracked product groups for the current user.",
)
def list_products(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> ProductListResponse:
    client = get_supabase_client(credentials.credentials)

    products_result = (
        client.table("products")
        .select("*", count="exact")
        .eq("user_id", current_user.id)
        .order("created_at", desc=True)
        .execute()
    )

    products = []
    for p in products_result.data or []:
        competitors_result = client.table("competitors").select("*").eq("product_id", p["id"]).execute()
        products.append(_build_product_response(p, competitors_result.data or []))

    return ProductListResponse(products=products, total=products_result.count or 0)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get tracked product",
    description="Get a tracked product group by ID with all competitors.",
)
def get_product(
    product_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> ProductResponse:
    client = get_supabase_client(credentials.credentials)

    product_result = (
        client.table("products")
        .select("*")
        .eq("id", product_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product = product_result.data[0]
    competitors_result = client.table("competitors").select("*").eq("product_id", product_id).execute()

    return _build_product_response(product, competitors_result.data or [])


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Update tracked product",
    description="Update product name or active status.",
)
def update_product(
    product_id: str,
    body: ProductUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> ProductResponse:
    client = get_supabase_client(credentials.credentials)

# application level user validation
    existing = (
        client.table("products")
        .select("id")
        .eq("id", product_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    update_data = {}
    if body.product_name is not None:
        update_data["product_name"] = body.product_name
    if body.is_active is not None:
        update_data["is_active"] = body.is_active

    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    product_result = (
        client.table("products")
        .update(update_data)
        .eq("id", product_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update product")

    product = product_result.data[0]
    competitors_result = client.table("competitors").select("*").eq("product_id", product_id).execute()

    return _build_product_response(product, competitors_result.data or [])


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tracked product",
    description="Soft delete a tracked product by setting is_active to false.",
)
def delete_product(
    product_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    client = get_supabase_client(credentials.credentials)

    existing = (
        client.table("products")
        .select("id")
        .eq("id", product_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    client.table("products").update({"is_active": False}).eq("id", product_id).eq("user_id", current_user.id).execute()
