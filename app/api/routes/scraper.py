import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_supabase_client
from app.middleware.rate_limit import limiter, SCRAPE_RATE_LIMIT
from app.db.models import (
    PriceHistoryResponse,
    PriceHistoryListResponse,
    ScrapeResultResponse,
    ScrapeTaskResponse,
    ChartDataResponse,
)
from app.services.scraper_service import scrape_url
from app.services.chart_service import ChartService
from app.tasks.scraper_tasks import scrape_product_manual, get_scrape_progress


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
    response_model=ScrapeTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Manual scrape (async)",
    description="Queue a manual scrape for all competitors. Returns task_id for SSE streaming.",
)
@limiter.limit(SCRAPE_RATE_LIMIT)
async def manual_scrape(
    request: Request,
    product_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> ScrapeTaskResponse:
    """
    Dispatch scrape task to Celery and return immediately.
    Use /scrape/stream/{task_id} to receive real-time progress via SSE.
    """
    client = get_supabase_client(credentials.credentials)

    # Validate product exists and user owns it
    product_result = client.table("products").select("id").eq("id", product_id).execute()
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Check competitors exist
    competitors_result = (
        client.table("competitors")
        .select("id", count="exact")
        .eq("product_id", product_id)
        .execute()
    )
    if not competitors_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No competitors found")

    # Dispatch to Celery (non-blocking)
    task = scrape_product_manual.delay(product_id)

    return ScrapeTaskResponse(
        task_id=task.id,
        status="queued",
        message=f"Scraping {competitors_result.count} competitors"
    )


@router.get(
    "/scrape/stream/{task_id}",
    summary="Stream scrape progress (SSE)",
    description="Server-Sent Events stream for real-time scrape progress updates.",
)
async def stream_scrape_progress(task_id: str):
    """
    SSE endpoint for real-time scrape progress.

    Yields events:
    - {"status": "scraping", "completed": 2, "total": 5, "current": "amazon.com"}
    - {"status": "completed", "results": [...]}
    """
    async def event_generator():
        timeout_seconds = 300  # 5 minute max
        poll_interval = 1.0  # Check Redis every 1 second
        elapsed = 0

        while elapsed < timeout_seconds:
            progress = get_scrape_progress(task_id)

            if progress is None:
                # Task not started yet or expired
                yield f"data: {json.dumps({'status': 'queued', 'completed': 0, 'total': 0})}\n\n"
            else:
                yield f"data: {json.dumps(progress)}\n\n"

                # Check if complete
                if progress.get("status") in ("completed", "error"):
                    break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout reached
        if elapsed >= timeout_seconds:
            yield f"data: {json.dumps({'status': 'error', 'error': 'Timeout'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


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
    "/prices/{product_id}/chart-data",
    response_model=ChartDataResponse,
    summary="Get chart data",
    description="Get formatted price history data for visualization (charts).",
)
async def get_chart_data(
    product_id: str,
    days: int = 30,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> ChartDataResponse:
    """
    Get chart-ready data for price history visualization.

    Returns structured data with:
    - Time series per competitor
    - Min/max/average prices
    - Price change percentages
    - Date ranges
    """
    try:
        chart_service = ChartService()
        chart_data = await chart_service.get_chart_data(product_id, credentials.credentials, days)
        return chart_data
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate chart data: {str(e)}"
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
