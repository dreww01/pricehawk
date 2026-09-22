import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import redis

from app.tasks.celery_app import celery_app
from app.db.database import get_supabase_client
from app.services.scraper_service import (
    DatabasePersistenceError,
    classify_scrape_exception,
    scrape_and_check_alerts,
    scrape_url,
    ScrapeFailureReason,
)
from app.services.alert_service import AlertService
from app.services.digest_service import DigestService
from app.core.config import get_settings
from app.core.logging import correlation_context, get_correlation_id, generate_correlation_id
from app.services.account_service import (
    is_product_deleted,
    is_user_deleted,
    register_active_scrape_task,
    unregister_active_scrape_task,
)

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
    try:
        if isinstance(data, dict) and "correlation_id" not in data:
            cid = get_correlation_id()
            if cid:
                data["correlation_id"] = cid
        client = _get_redis_client()
        client.setex(f"scrape:{task_id}", ttl, json.dumps(data))
    except Exception as exc:
        logger.warning(f"Failed to update scrape progress in Redis for task {task_id}: {exc}")


def get_scrape_progress(task_id: str) -> dict | None:
    """Get scrape progress from Redis."""
    try:
        client = _get_redis_client()
        data = client.get(f"scrape:{task_id}")
        return json.loads(data) if data else None
    except Exception as exc:
        logger.warning(f"Failed to read scrape progress from Redis for task {task_id}: {exc}")
        return None

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
    from app.services.account_service import is_product_deleted, is_user_deleted

    req = getattr(self, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )

    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid

        task_id = getattr(req, "id", None) or "manual-task"
        client = get_supabase_client()

        # Guard: abort immediately if product has been marked deleted
        if is_product_deleted(product_id):
            set_scrape_progress(task_id, {
                "status": "cancelled",
                "completed": 0,
                "total": 0,
                "results": [],
                "error": "Account or product deleted",
                "correlation_id": cid,
            })
            return {"status": "cancelled", "results": [], "correlation_id": cid}

        # Verify if product exists and whether owning user has been deleted
        user_id = None
        try:
            prod_res = client.table("products").select("id, user_id, is_active").eq("id", product_id).execute()
            if prod_res and isinstance(prod_res.data, list) and len(prod_res.data) > 0:
                prod_row = prod_res.data[0]
                if isinstance(prod_row, dict):
                    if prod_row.get("is_active") is False:
                        set_scrape_progress(task_id, {
                            "status": "cancelled",
                            "completed": 0,
                            "total": 0,
                            "results": [],
                            "error": "Product inactive",
                            "correlation_id": cid,
                        })
                        return {"status": "cancelled", "results": [], "correlation_id": cid}
                    user_id = prod_row.get("user_id")
                    if user_id and is_user_deleted(user_id):
                        set_scrape_progress(task_id, {
                            "status": "cancelled",
                            "completed": 0,
                            "total": 0,
                            "results": [],
                            "error": "Account deleted",
                            "correlation_id": cid,
                        })
                        return {"status": "cancelled", "results": [], "correlation_id": cid}
        except Exception as exc:
            logger.debug(f"Product status verification error for {product_id}: {exc}")

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
                "error": "No competitors found",
                "correlation_id": cid,
            })
            return {"status": "completed", "results": [], "correlation_id": cid}

        # Initialize progress
        set_scrape_progress(task_id, {
            "status": "scraping",
            "completed": 0,
            "total": total,
            "current": None,
            "results": [],
            "correlation_id": cid,
        })

        results = []

        for i, competitor in enumerate(competitors):
            # Abort mid-run if product or user was deleted
            if is_product_deleted(product_id) or (user_id and is_user_deleted(user_id)):
                logger.info(f"Manual scrape aborted mid-run for product {product_id}")
                set_scrape_progress(task_id, {
                    "status": "cancelled",
                    "completed": i,
                    "total": total,
                    "current": None,
                    "results": results,
                    "error": "Scrape cancelled: account or product deleted",
                    "correlation_id": cid,
                })
                return {"status": "cancelled", "results": results, "correlation_id": cid}

            competitor_id = competitor.get("id") if isinstance(competitor, dict) else None
            url = competitor.get("url") if isinstance(competitor, dict) else None
            retailer = (
                (competitor.get("retailer_name") if isinstance(competitor, dict) else None)
                or _extract_domain(url or "")
            )

            # Update progress: starting this competitor
            set_scrape_progress(task_id, {
                "status": "scraping",
                "completed": i,
                "total": total,
                "current": retailer,
                "results": results,
                "correlation_id": cid,
            })

            if not competitor_id or not url:
                result = {
                    "competitor_id": competitor_id or "",
                    "retailer": retailer,
                    "price": None,
                    "currency": "USD",
                    "status": "failed",
                    "error_message": "Invalid competitor configuration: missing competitor ID or URL",
                    "failure_reason": ScrapeFailureReason.INVALID_URL,
                    "retry_count": 0,
                }
                results.append(result)
                set_scrape_progress(task_id, {
                    "status": "scraping",
                    "completed": i + 1,
                    "total": total,
                    "current": retailer,
                    "results": results,
                    "correlation_id": cid,
                })
                continue

            try:
                # Scrape the URL
                scrape_result = asyncio.run(scrape_url(url))

                # Store in price_history (isolated from aborting scrape run)
                db_error = None
                max_db_retries = 3
                price_data = {
                    "competitor_id": competitor_id,
                    "price": float(scrape_result.price) if scrape_result.price else None,
                    "currency": scrape_result.currency,
                    "scrape_status": scrape_result.status,
                    "error_message": scrape_result.error_message,
                }
                for db_attempt in range(max_db_retries):
                    try:
                        client.table("price_history").insert(price_data).execute()
                        db_error = None
                        break
                    except Exception as db_exc:
                        db_error = db_exc
                        logger.warning(
                            f"Failed to persist price_history (attempt {db_attempt + 1}/{max_db_retries}) for competitor {competitor_id}: {db_exc}"
                        )
                        if db_attempt < max_db_retries - 1:
                            time.sleep(min(0.05 * (2 ** db_attempt), 0.5))

                if db_error:
                    logger.error(f"Failed to persist price_history for competitor {competitor_id}: {db_error}")
                    result = {
                        "competitor_id": competitor_id,
                        "retailer": retailer,
                        "price": None,
                        "currency": scrape_result.currency,
                        "status": "failed",
                        "error_message": f"Failed to record price history: {str(db_error)[:150]}",
                        "failure_reason": ScrapeFailureReason.DATABASE_ERROR,
                        "retry_count": scrape_result.retry_count,
                    }
                else:
                    result = {
                        "competitor_id": competitor_id,
                        "retailer": retailer,
                        "price": str(scrape_result.price) if scrape_result.price else None,
                        "currency": scrape_result.currency,
                        "status": scrape_result.status,
                        "error_message": scrape_result.error_message,
                        "failure_reason": scrape_result.failure_reason,
                        "retry_count": scrape_result.retry_count,
                    }
                    if scrape_result.status == "success" and scrape_result.price:
                        try:
                            from app.services.alert_service import AlertService
                            alert_svc = AlertService()
                            alert_res = asyncio.run(alert_svc.check_price_change_and_alert(
                                competitor_id=competitor_id,
                                new_price=scrape_result.price,
                                currency=scrape_result.currency,
                                correlation_id=cid,
                            ))
                            if alert_res and alert_res.get("alert_created"):
                                logger.info(
                                    f"Alert triggered for competitor {competitor_id}: {alert_res.get('alert_type')}"
                                )
                        except Exception as alert_exc:
                            logger.debug(f"Alert check during manual scrape failed for competitor {competitor_id}: {alert_exc}")

            except Exception as e:
                logger.error(f"Error scraping competitor {competitor_id} ({url}): {str(e)}")
                failure_reason, friendly_msg = classify_scrape_exception(e)
                try:
                    price_data = {
                        "competitor_id": competitor_id,
                        "price": None,
                        "currency": "USD",
                        "scrape_status": "failed",
                        "error_message": friendly_msg,
                    }
                    client.table("price_history").insert(price_data).execute()
                except Exception as db_exc:
                    logger.error(f"Failed to persist failure in price_history for competitor {competitor_id}: {db_exc}")

                result = {
                    "competitor_id": competitor_id,
                    "retailer": retailer,
                    "price": None,
                    "currency": "USD",
                    "status": "failed",
                    "error_message": friendly_msg,
                    "failure_reason": failure_reason,
                    "retry_count": 0,
                }

            results.append(result)

            # Update progress: completed this competitor
            set_scrape_progress(task_id, {
                "status": "scraping",
                "completed": i + 1,
                "total": total,
                "current": retailer,
                "results": results,
                "correlation_id": cid,
            })

        try:
            # Final progress update
            set_scrape_progress(task_id, {
                "status": "completed",
                "completed": total,
                "total": total,
                "current": None,
                "results": results,
                "correlation_id": cid,
            })

            logger.info(f"Manual scrape completed for product {product_id}: {total} competitors")
        finally:
            # Invalidate dashboard cache for product owner unless deleted
            if not is_product_deleted(product_id) and not (user_id and is_user_deleted(user_id)):
                try:
                    from app.services.dashboard_cache import invalidate_dashboard_cache_for_product
                    invalidate_dashboard_cache_for_product(product_id)
                except Exception as exc:
                    logger.debug(f"Failed to invalidate dashboard cache for product {product_id}: {exc}")

        return {"status": "completed", "results": results, "correlation_id": cid}


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
    from app.services.account_service import (
        is_product_deleted,
        is_user_deleted,
        unregister_active_scrape_task,
    )

    req = getattr(self, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )

    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid

        client = get_supabase_client()
        task_id = getattr(req, "id", None)
        pid = None
        uid = None

        try:
            # Guard: verify competitor and parent product are not deleted or inactive
            try:
                comp_res = (
                    client.table("competitors")
                    .select("id, product_id, products(id, user_id, is_active)")
                    .eq("id", competitor_id)
                    .execute()
                )
                if not comp_res or not comp_res.data:
                    return {"status": "cancelled", "reason": "competitor_deleted_or_not_found", "correlation_id": cid}

                comp_rec = comp_res.data[0]
                if isinstance(comp_rec, dict):
                    pid = comp_rec.get("product_id")
                    if pid and is_product_deleted(pid):
                        return {"status": "cancelled", "reason": "product_deleted", "correlation_id": cid}
                    prod_data = comp_rec.get("products")
                    if isinstance(prod_data, dict):
                        if prod_data.get("is_active") is False:
                            return {"status": "cancelled", "reason": "product_inactive", "correlation_id": cid}
                        uid = prod_data.get("user_id")
                        if uid and is_user_deleted(uid):
                            return {"status": "cancelled", "reason": "user_deleted", "correlation_id": cid}
            except Exception as exc:
                logger.debug(f"Competitor active check failed for {competitor_id}: {exc}")

            if _was_scraped_today(client, competitor_id):
                logger.info(f"Competitor {competitor_id} already scraped today, skipping")
                return {"status": "skipped", "reason": "already_scraped_today", "correlation_id": cid}

            # Use new function that combines scraping + alert detection
            result = asyncio.run(scrape_and_check_alerts(competitor_id, correlation_id=cid))

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

            if scrape_result.get("failure_reason") == ScrapeFailureReason.DATABASE_ERROR:
                if req is not None and getattr(req, "id", None):
                    raise DatabasePersistenceError(
                        scrape_result.get("error") or f"Database persistence failed for competitor {competitor_id}"
                    )

            return {
                "competitor_id": competitor_id,
                "scrape_status": scrape_result.get("status"),
                "price": scrape_result.get("price"),
                "currency": scrape_result.get("currency"),
                "error_message": scrape_result.get("error"),
                "failure_reason": scrape_result.get("failure_reason"),
                "retry_count": scrape_result.get("retry_count", 0),
                "alert_created": alert_result.get("alert_created", False) if alert_result else False,
                "correlation_id": cid,
            }
        finally:
            if task_id:
                try:
                    unregister_active_scrape_task(
                        task_id=task_id,
                        user_id=uid,
                        product_id=pid,
                        competitor_id=competitor_id,
                    )
                except Exception as unreg_exc:
                    logger.debug(f"Failed to unregister completed task {task_id}: {unreg_exc}")


@celery_app.task(bind=True)
def scrape_all_products(self) -> dict:
    """
    Scrape all active competitors in batches.

    Scheduled daily at 2 AM UTC via Celery Beat.
    Processes competitors in batches of 50 to prevent memory issues.
    """
    req = getattr(self, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )

    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid

        client = get_supabase_client()

        # Get all active products
        products_result = (
            client.table("products")
            .select("id, user_id")
            .eq("is_active", True)
            .execute()
        )

        if not products_result.data:
            logger.info("No active products to scrape")
            return {"total": 0, "queued": 0, "correlation_id": cid}

        product_to_user = {
            p["id"]: p.get("user_id")
            for p in products_result.data
            if isinstance(p, dict) and "id" in p
        }
        product_ids = list(product_to_user.keys())

        # Get all competitors for active products
        competitors_result = (
            client.table("competitors")
            .select("id, product_id")
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
                comp_id = competitor["id"]
                prod_id = competitor.get("product_id")
                user_id = product_to_user.get(prod_id)

                task = scrape_single_competitor.delay(comp_id)
                if task and getattr(task, "id", None):
                    register_active_scrape_task(
                        user_id=user_id,
                        product_id=prod_id,
                        task_id=task.id,
                        competitor_id=comp_id,
                    )
                queued += 1

            logger.info(f"Queued batch {i // BATCH_SIZE + 1}: {len(batch)} competitors")

        logger.info(f"Daily scrape queued: {queued}/{total} competitors")

        return {"total": total, "queued": queued, "correlation_id": cid}


@celery_app.task
def check_worker_health() -> dict:
    """Health check task to verify worker is responsive."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@celery_app.task(bind=True)
def send_alert_digests(self, force: bool = False, dry_run: bool = False) -> dict:
    """Run batched digests for configured users."""
    req = getattr(self, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )

    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid

        client = get_supabase_client()
        settings_response = (
            client.table("user_alert_settings")
            .select("user_id")
            .or_("email_enabled.eq.true,webhook_enabled.eq.true")
            .execute()
        )
        digest_service = DigestService()
        results = []

        for setting in settings_response.data or []:
            user_id = setting["user_id"]
            try:
                auth_response = client.auth.admin.get_user_by_id(user_id)
                email = getattr(getattr(auth_response, "user", None), "email", None)
                results.append(
                    digest_service.run_for_user(
                        user_id, email, force=force, dry_run=dry_run, correlation_id=cid
                    )
                )
            except Exception as exc:
                logger.exception("Digest batch failed for user %s: %s", user_id, exc)
                results.append({"user_id": user_id, "status": "failed", "alerts_count": 0, "correlation_id": cid})

        return {
            "total_users": len(results),
            "sent": sum(1 for result in results if result["status"] == "sent"),
            "failed": sum(1 for result in results if result["status"] == "failed"),
            "skipped": sum(1 for result in results if result["status"] == "skipped"),
            "dry_run": sum(1 for result in results if result["status"] == "dry_run"),
            "alerts_sent": sum(
                result.get("alerts_count", 0)
                for result in results
                if result["status"] == "sent"
            ),
            "results": results,
            "correlation_id": cid,
        }


@celery_app.task(bind=True)
def cleanup_old_alerts(self) -> dict:
    """
    Clean up old pending alerts that have been included in digests.

    Runs daily. Removes alerts older than 7 days that have been included in digests.
    """
    req = getattr(self, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )

    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid

        alert_service = AlertService()
        deleted_count = asyncio.run(alert_service.cleanup_old_pending_alerts())

        logger.info(f"Cleaned up {deleted_count} old pending alerts")

        return {
            "deleted_count": deleted_count,
            "correlation_id": cid,
        }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    retry_backoff=True,
)
def dispatch_webhook_alert(self, user_id: str, alert_data: dict, correlation_id: str | None = None) -> dict:
    """
    Deliver real-time webhook notification for a price drop alert.
    Executes in background so main scraping jobs are never blocked.
    """
    req = getattr(self, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        correlation_id
        or get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )

    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid

        client = get_supabase_client()
        try:
            settings_res = (
                client.table("user_alert_settings")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            if not settings_res.data:
                logger.info("No alert settings found for user %s; skipping webhook", user_id)
                return {"success": False, "reason": "settings_not_found", "correlation_id": cid}

            settings = settings_res.data[0]
            if not settings.get("webhook_enabled") or not settings.get("webhook_url"):
                logger.info("Webhooks disabled or not configured for user %s", user_id)
                return {"success": False, "reason": "webhook_not_enabled", "correlation_id": cid}

            webhook_url = settings["webhook_url"]
            webhook_secret = settings.get("webhook_secret")

            from app.services.webhook_service import WebhookService
            wh_service = WebhookService()
            result = wh_service.send_alert(
                webhook_url=webhook_url,
                payload=alert_data,
                webhook_secret=webhook_secret,
                correlation_id=cid,
            )

            # Record in alert_history for audit
            alert_id = alert_data.get("alert_id")
            alert_type = alert_data.get("alert_type") or alert_data.get("event")
            history_record = {
                "user_id": user_id,
                "digest_sent_at": datetime.now(timezone.utc).isoformat(),
                "alerts_count": 1,
                "price_drops": 1 if alert_type == "price_drop" else 0,
                "price_increases": 1 if alert_type == "price_increase" else 0,
                "currency_changes": 1 if alert_type == "currency_changed" else 0,
                "email_status": "disabled",
                "webhook_status": "sent" if result.get("success") else "failed",
                "response_code": result.get("status_code"),
                "error_message": result.get("error"),
                "alert_ids": [alert_id] if alert_id else [],
            }
            try:
                client.table("alert_history").insert(history_record).execute()
            except Exception as hist_err:
                logger.warning("Failed to record alert_history entry: %s", hist_err)

            return {
                "success": result.get("success", False),
                "status_code": result.get("status_code"),
                "error": result.get("error"),
                "correlation_id": cid,
            }
        except Exception as exc:
            logger.exception("Error dispatching webhook alert for user %s: %s", user_id, exc)
            return {"success": False, "error": str(exc), "correlation_id": cid}
