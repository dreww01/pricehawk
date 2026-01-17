"""
Chart data API endpoints for price visualization.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_supabase_client
from app.db.models import ChartDataResponse
from app.services.chart_service import ChartService


router = APIRouter(prefix="/charts", tags=["charts"])
security = HTTPBearer()


@router.get(
    "/{product_id}",
    response_model=ChartDataResponse,
    summary="Get chart data for a product",
    description="Returns formatted price history data ready for Chart.js visualization.",
)
async def get_chart_data(
    product_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days of history"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> ChartDataResponse:
    client = get_supabase_client(credentials.credentials)

    # Verify product ownership
    product_result = (
        client.table("products")
        .select("id")
        .eq("id", product_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    chart_service = ChartService()
    try:
        return await chart_service.get_chart_data(product_id, credentials.credentials, days)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
