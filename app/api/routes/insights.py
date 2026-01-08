from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from supabase import Client

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_user_supabase_client, security
from app.db.models import (
    InsightListResponse,
    InsightResponse,
    GenerateInsightRequest
)
from app.services.ai_service import AIService

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/{product_id}", response_model=InsightListResponse)
async def get_insights(
    product_id: str,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Retrieve all AI-generated insights for a product.

    Only returns insights for products owned by the authenticated user.
    """

    try:
        # Verify product ownership (RLS will also enforce this)
        product_check = sb.table("products").select("id").eq("id", product_id).execute()
        if not product_check.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )

        # Fetch insights
        response = (
            sb.table("insights")
            .select("*")
            .eq("product_id", product_id)
            .order("generated_at", desc=True)
            .execute()
        )

        insights = [
            InsightResponse(
                id=row["id"],
                product_id=row["product_id"],
                insight_text=row["insight_text"],
                insight_type=row["insight_type"],
                confidence_score=row["confidence_score"],
                generated_at=row["generated_at"]
            )
            for row in response.data
        ]

        return InsightListResponse(insights=insights, total=len(insights))

    except HTTPException:
        raise
    except Exception as e:
        error_detail = str(e)
        if hasattr(e, '__dict__'):
            error_detail = f"{error_detail} | {e.__dict__}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {error_detail}"
        )


@router.post("/generate/{product_id}", response_model=InsightListResponse)
async def generate_insights(
    product_id: str,
    request: GenerateInsightRequest = GenerateInsightRequest(),
    sb: Client = Depends(get_user_supabase_client),
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Manually trigger AI insight generation for a product.

    Rate limited to once per product per day (unless force_regenerate=true).
    Requires at least 1 day of price history data.
    """
    try:
        # Verify product ownership
        product_check = sb.table("products").select("id, product_name").eq("id", product_id).execute()
        if not product_check.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )

        # Initialize AI service and generate insights
        ai_service = AIService()
        insights_data = await ai_service.generate_insights(product_id, credentials.credentials)

        # Fetch newly created insights from database
        response = (
            sb.table("insights")
            .select("*")
            .eq("product_id", product_id)
            .order("generated_at", desc=True)
            .limit(10)
            .execute()
        )

        insights = [
            InsightResponse(
                id=row["id"],
                product_id=row["product_id"],
                insight_text=row["insight_text"],
                insight_type=row["insight_type"],
                confidence_score=row["confidence_score"],
                generated_at=row["generated_at"]
            )
            for row in response.data
        ]

        return InsightListResponse(insights=insights, total=len(insights))

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        error_detail = str(e)
        if hasattr(e, '__dict__'):
            error_detail = f"{error_detail} | {e.__dict__}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate insights: {error_detail}"
        )
