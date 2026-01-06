from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_supabase_client
from app.db.models import (
    PriceHistoryResponse,
    PriceHistoryListResponse,
    ScrapeResultResponse,
)
from app.services.scraper_service import scrape_url


router = APIRouter(tags=["scraper"])
security = HTTPBearer()


class WorkerHealthResponse(BaseModel):
    """Response model for worker health check."""
    worker_status: str
    ping_response: str | None = None
    active_tasks: int | None = None
    error: str | None = None


@router.post(
    "/scrape/manual/{product_id}",
    response_model=list[ScrapeResultResponse],
    summary="Manual scrape",
    description="Trigger a manual scrape for all competitors of a tracked product.",
)
async def manual_scrape(
    product_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ScrapeResultResponse]:
    client = get_supabase_client(credentials.credentials)

    product_result = client.table("products").select("id").eq("id", product_id).execute()
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    competitors_result = client.table("competitors").select("*").eq("product_id", product_id).execute()
    if not competitors_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No competitors found")

    service_client = get_supabase_client()

    results = []
    for competitor in competitors_result.data:
        scrape_result = await scrape_url(competitor["url"])

        price_data = {
            "competitor_id": competitor["id"],
            "price": float(scrape_result.price) if scrape_result.price else None,
            "currency": scrape_result.currency,
            "scrape_status": scrape_result.status,
            "error_message": scrape_result.error_message,
        }
        service_client.table("price_history").insert(price_data).execute()

        results.append(ScrapeResultResponse(
            competitor_id=competitor["id"],
            competitor_url=competitor["url"],
            price=scrape_result.price,
            currency=scrape_result.currency,
            status=scrape_result.status,
            error_message=scrape_result.error_message,
        ))

    return results


@router.get(
    "/prices/{product_id}/history",
    response_model=PriceHistoryListResponse,
    summary="Get price history",
    description="Get all price history for a tracked product's competitors.",
)
def get_price_history(
    product_id: str,
    limit: int = 100,
    offset: int = 0,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> PriceHistoryListResponse:
    client = get_supabase_client(credentials.credentials)

    product_result = client.table("products").select("id").eq("id", product_id).execute()
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    competitors_result = client.table("competitors").select("id").eq("product_id", product_id).execute()
    if not competitors_result.data:
        return PriceHistoryListResponse(prices=[], total=0)

    competitor_ids = [c["id"] for c in competitors_result.data]

    service_client = get_supabase_client()
    history_result = (
        service_client.table("price_history")
        .select("*", count="exact")
        .in_("competitor_id", competitor_ids)
        .order("scraped_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )

    prices = [
        PriceHistoryResponse(
            id=h["id"],
            competitor_id=h["competitor_id"],
            price=h["price"],
            currency=h["currency"],
            scraped_at=h["scraped_at"],
            scrape_status=h["scrape_status"],
            error_message=h["error_message"],
        )
        for h in history_result.data or []
    ]

    return PriceHistoryListResponse(prices=prices, total=history_result.count or 0)


@router.get(
    "/prices/latest/{competitor_id}",
    response_model=PriceHistoryResponse | None,
    summary="Get latest price",
    description="Get the most recent price for a competitor.",
)
def get_latest_price(
    competitor_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> PriceHistoryResponse | None:
    client = get_supabase_client(credentials.credentials)

    competitor_result = client.table("competitors").select("id").eq("id", competitor_id).execute()
    if not competitor_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found")

    service_client = get_supabase_client()
    history_result = (
        service_client.table("price_history")
        .select("*")
        .eq("competitor_id", competitor_id)
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
    )

    if not history_result.data:
        return None

    h = history_result.data[0]
    return PriceHistoryResponse(
        id=h["id"],
        competitor_id=h["competitor_id"],
        price=h["price"],
        currency=h["currency"],
        scraped_at=h["scraped_at"],
        scrape_status=h["scrape_status"],
        error_message=h["error_message"],
    )


@router.get(
    "/scrape/worker-health",
    response_model=WorkerHealthResponse,
    summary="Check worker health",
    description="Check if Celery worker is running and responsive.",
)
def check_worker_health() -> WorkerHealthResponse:
    """Check Celery worker health by pinging it."""
    try:
        from app.tasks.celery_app import celery_app

        inspect = celery_app.control.inspect()
        ping_result = inspect.ping()

        if not ping_result:
            return WorkerHealthResponse(
                worker_status="offline",
                error="No workers responded to ping",
            )

        active = inspect.active()
        active_count = sum(len(tasks) for tasks in (active or {}).values())

        return WorkerHealthResponse(
            worker_status="healthy",
            ping_response=str(list(ping_result.keys())),
            active_tasks=active_count,
        )
    except Exception as e:
        return WorkerHealthResponse(
            worker_status="error",
            error=str(e)[:200],
        )
