import asyncio
import logging
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.db.database import get_supabase_client
from app.services.scraper_service import scrape_url

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def _get_today_start_utc() -> str:
    """Get today's start time in UTC as ISO string."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _was_scraped_today(client, competitor_id: str) -> bool:
    """Check if competitor was already scraped today."""
    today_start = _get_today_start_utc()
    result = (
        client.table("price_history")
        .select("id")
        .eq("competitor_id", competitor_id)
        .gte("scraped_at", today_start)
        .limit(1)
        .execute()
    )
    return bool(result.data)

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=240,
)
def scrape_single_competitor(self, competitor_id: str, url: str) -> dict:
    """Scrape a single competitor URL and store result."""
    client = get_supabase_client()

    if _was_scraped_today(client, competitor_id):
        logger.info(f"Competitor {competitor_id} already scraped today, skipping")
        return {"status": "skipped", "reason": "already_scraped_today"}

    result = asyncio.run(scrape_url(url))

    price_data = {
        "competitor_id": competitor_id,
        "price": float(result.price) if result.price else None,
        "currency": result.currency,
        "scrape_status": result.status,
        "error_message": result.error_message,
    }
    client.table("price_history").insert(price_data).execute()

    logger.info(f"Scraped {competitor_id}: {result.status} - {result.price} {result.currency}")

    return {
        "competitor_id": competitor_id,
        "status": result.status,
        "price": float(result.price) if result.price else None,
        "currency": result.currency,
    }


@celery_app.task(bind=True)
def scrape_all_products(self) -> dict:
    """
    Scrape all active competitors in batches.

    Scheduled daily at 2 AM UTC via Celery Beat.
    Processes competitors in batches of 50 to prevent memory issues.
    """
    client = get_supabase_client()

    # Get all active products
    products_result = (
        client.table("products")
        .select("id")
        .eq("is_active", True)
        .execute()
    )

    if not products_result.data:
        logger.info("No active products to scrape")
        return {"total": 0, "queued": 0}

    product_ids = [p["id"] for p in products_result.data]

    # Get all competitors for active products
    competitors_result = (
        client.table("competitors")
        .select("id, url")
        .in_("product_id", product_ids)
        .execute()
    )

    competitors = competitors_result.data or []
    total = len(competitors)
    queued = 0

    logger.info(f"Starting daily scrape for {total} competitors")

    # Queue scrape tasks in batches
    for i in range(0, total, BATCH_SIZE):
        batch = competitors[i:i + BATCH_SIZE]
        for competitor in batch:
            scrape_single_competitor.delay(competitor["id"], competitor["url"])
            queued += 1

        logger.info(f"Queued batch {i // BATCH_SIZE + 1}: {len(batch)} competitors")

    logger.info(f"Daily scrape queued: {queued}/{total} competitors")

    return {"total": total, "queued": queued}


@celery_app.task
def check_worker_health() -> dict:
    """Health check task to verify worker is responsive."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
