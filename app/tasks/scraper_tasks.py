import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import redis

from app.tasks.celery_app import celery_app
from app.db.database import get_supabase_client
from app.services.scraper_service import scrape_and_check_alerts, scrape_url
from app.services.alert_service import AlertService
from app.services.email_service import EmailService
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Lazy Redis client initialization
_redis_client = None


def _get_redis_client():
    """Get or create Redis client (lazy init)."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def set_scrape_progress(task_id: str, data: dict, ttl: int = 300):
    """Store scrape progress in Redis with TTL (default 5 min)."""
    client = _get_redis_client()
    client.setex(f"scrape:{task_id}", ttl, json.dumps(data))


def get_scrape_progress(task_id: str) -> dict | None:
    """Get scrape progress from Redis."""
    client = _get_redis_client()
    data = client.get(f"scrape:{task_id}")
    return json.loads(data) if data else None

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

@celery_app.task(bind=True)
def scrape_product_manual(self, product_id: str) -> dict:
    """
    Scrape all competitors for a product (manual trigger).

    Updates Redis with progress for SSE streaming.
    Called from manual scrape endpoint.
    """
    task_id = self.request.id
    client = get_supabase_client()

    # Fetch competitors for this product
    competitors_result = (
        client.table("competitors")
        .select("id, url, retailer_name")
        .eq("product_id", product_id)
        .execute()
    )

    competitors = competitors_result.data or []
    total = len(competitors)

    if total == 0:
        set_scrape_progress(task_id, {
            "status": "completed",
            "completed": 0,
            "total": 0,
            "results": [],
            "error": "No competitors found"
        })
        return {"status": "completed", "results": []}

    # Initialize progress
    set_scrape_progress(task_id, {
        "status": "scraping",
        "completed": 0,
        "total": total,
        "current": None,
        "results": []
    })

    results = []

    for i, competitor in enumerate(competitors):
        competitor_id = competitor["id"]
        url = competitor["url"]
        retailer = competitor.get("retailer_name") or _extract_domain(url)

        # Update progress: starting this competitor
        set_scrape_progress(task_id, {
            "status": "scraping",
            "completed": i,
            "total": total,
            "current": retailer,
            "results": results
        })

        try:
            # Scrape the URL
            scrape_result = asyncio.run(scrape_url(url))

            # Store in price_history
            price_data = {
                "competitor_id": competitor_id,
                "price": float(scrape_result.price) if scrape_result.price else None,
                "currency": scrape_result.currency,
                "scrape_status": scrape_result.status,
                "error_message": scrape_result.error_message,
            }
            client.table("price_history").insert(price_data).execute()

            result = {
                "competitor_id": competitor_id,
                "retailer": retailer,
                "price": str(scrape_result.price) if scrape_result.price else None,
                "currency": scrape_result.currency,
                "status": scrape_result.status,
                "error_message": scrape_result.error_message,
            }

        except Exception as e:
            logger.error(f"Error scraping {url}: {str(e)}")
            result = {
                "competitor_id": competitor_id,
                "retailer": retailer,
                "price": None,
                "currency": "USD",
                "status": "error",
                "error_message": str(e)[:200],
            }

        results.append(result)

        # Update progress: completed this competitor
        set_scrape_progress(task_id, {
            "status": "scraping",
            "completed": i + 1,
            "total": total,
            "current": retailer,
            "results": results
        })

    # Final progress update
    set_scrape_progress(task_id, {
        "status": "completed",
        "completed": total,
        "total": total,
        "current": None,
        "results": results
    })

    logger.info(f"Manual scrape completed for product {product_id}: {total} competitors")

    return {"status": "completed", "results": results}


def _extract_domain(url: str) -> str:
    """Extract domain from URL for display."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "")
    except Exception:
        return url[:30]


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=240,
)
def scrape_single_competitor(self, competitor_id: str) -> dict:
    """
    Scrape a single competitor URL, store result, and check for alerts.

    This replaces the old scrape_single_competitor function.
    Now uses scrape_and_check_alerts which handles both scraping and alert detection.
    """
    client = get_supabase_client()

    if _was_scraped_today(client, competitor_id):
        logger.info(f"Competitor {competitor_id} already scraped today, skipping")
        return {"status": "skipped", "reason": "already_scraped_today"}

    # Use new function that combines scraping + alert detection
    result = asyncio.run(scrape_and_check_alerts(competitor_id))

    scrape_result = result.get("scrape_result", {})
    alert_result = result.get("alert_result")

    logger.info(
        f"Scraped {competitor_id}: {scrape_result.get('status')} - "
        f"{scrape_result.get('price')} {scrape_result.get('currency')}"
    )

    if alert_result and alert_result.get("alert_created"):
        logger.info(
            f"Alert created for {competitor_id}: {alert_result.get('alert_type')} "
            f"({alert_result.get('change_percent')}%)"
        )

    return {
        "competitor_id": competitor_id,
        "scrape_status": scrape_result.get("status"),
        "price": scrape_result.get("price"),
        "currency": scrape_result.get("currency"),
        "alert_created": alert_result.get("alert_created", False) if alert_result else False
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
        .select("id")
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
            scrape_single_competitor.delay(competitor["id"])
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


@celery_app.task(bind=True)
def send_alert_digests(self) -> dict:
    """
    Send digest emails to users who have pending alerts and are due.

    Runs every hour via Celery Beat.
    Checks each user's digest_frequency_hours setting and sends accordingly.
    """
    client = get_supabase_client()
    alert_service = AlertService()
    email_service = EmailService()

    # Get users due for digest
    users_due = asyncio.run(alert_service.get_users_due_for_digest())

    total_users = len(users_due)
    sent = 0
    failed = 0

    logger.info(f"Found {total_users} users due for alert digest")

    for user_data in users_due:
        user_id = user_data["user_id"]
        email = user_data["email"]
        digest_hours = user_data["digest_frequency_hours"]
        pending_count = user_data["pending_count"]

        try:
            # Get pending alerts for user
            alerts = asyncio.run(alert_service.get_pending_alerts_for_user(user_id))

            if not alerts:
                logger.warning(f"User {user_id} marked as due but has no pending alerts")
                continue

            # Extract user name from email
            user_name = email.split("@")[0] if email else "User"

            # Prepare alerts for email template
            alert_dicts = [
                {
                    "product_name": a["product_name"],
                    "competitor_name": a["competitor_name"],
                    "alert_type": a["alert_type"],
                    "old_price": a["old_price"],
                    "new_price": a["new_price"],
                    "price_change_percent": a["price_change_percent"],
                    "currency": a.get("currency", "USD")
                }
                for a in alerts
            ]

            # Send digest email
            email_result = email_service.send_price_alert_digest(
                to_email=email,
                user_name=user_name,
                alerts=alert_dicts,
                digest_period_hours=digest_hours
            )

            alert_ids = [a["id"] for a in alerts]

            if email_result["success"]:
                # Mark alerts as included
                asyncio.run(alert_service.mark_alerts_as_included(alert_ids))

                # Record in alert history
                client.table("alert_history").insert({
                    "user_id": user_id,
                    "alerts_count": len(alerts),
                    "email_status": "sent",
                    "alert_ids": alert_ids
                }).execute()

                # Update last_digest_sent_at
                client.table("user_alert_settings").update({
                    "last_digest_sent_at": datetime.now(timezone.utc).isoformat()
                }).eq("user_id", user_id).execute()

                sent += 1
                logger.info(f"Sent digest to {email} with {len(alerts)} alerts")
            else:
                # Record failure
                client.table("alert_history").insert({
                    "user_id": user_id,
                    "alerts_count": len(alerts),
                    "email_status": "failed",
                    "error_message": email_result.get("error", "Unknown error"),
                    "alert_ids": alert_ids
                }).execute()

                failed += 1
                logger.error(f"Failed to send digest to {email}: {email_result.get('error')}")

        except Exception as e:
            failed += 1
            logger.error(f"Error sending digest to user {user_id}: {str(e)}")

    logger.info(f"Alert digest batch complete: {sent} sent, {failed} failed out of {total_users}")

    return {
        "total_users": total_users,
        "sent": sent,
        "failed": failed
    }


@celery_app.task(bind=True)
def cleanup_old_alerts(self) -> dict:
    """
    Clean up old pending alerts that have been included in digests.

    Runs daily. Removes alerts older than 7 days that have been included in digests.
    """
    alert_service = AlertService()
    deleted_count = asyncio.run(alert_service.cleanup_old_pending_alerts())

    logger.info(f"Cleaned up {deleted_count} old pending alerts")

    return {
        "deleted_count": deleted_count
    }
