"""Celery task runner for asynchronous price scraping, digests, and maintenance."""
import asyncio
from datetime import datetime, timezone
import logging
from app.core.logging import correlation_context
from app.db.database import get_supabase_client
from app.services.account_service import (
    is_product_deleted,
    is_user_deleted,
    register_active_scrape_task,
    unregister_active_scrape_task,
)
from app.services.alert_service import AlertService
from app.services.digest_service import DigestService
from app.services.scraper_service import (
    DatabasePersistenceError,
    ScrapeFailureReason,
    classify_scrape_exception,
    scrape_and_check_alerts,
    scrape_url,
)
from app.services.webhook_service import WebhookService
from app.tasks.celery_app import celery_app
from app.tasks.helpers import (
    BATCH_SIZE,
    _check_competitor_active,
    _check_product_user,
    _extract_domain,
    _get_redis_client,
    _get_today_start_utc,
    _resolve_correlation_id,
    _was_scraped_today,
    get_scrape_progress,
    persist_scrape_result,
    record_alert_history,
    record_scrape_failure,
    set_scrape_progress,
)

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def scrape_product_manual(self, product_id: str) -> dict:
    """Scrape all competitors for a product (manual trigger)."""
    cid = _resolve_correlation_id(self)
    with correlation_context(cid):
        task_id = getattr(getattr(self, "request", None), "id", None) or "manual-task"
        client = get_supabase_client()

        if is_product_deleted(product_id):
            set_scrape_progress(task_id, {"status": "cancelled", "completed": 0, "total": 0, "results": [], "error": "Account or product deleted", "correlation_id": cid})
            return {"status": "cancelled", "results": [], "correlation_id": cid}

        user_id = _check_product_user(client, product_id)
        if user_id is False:
            set_scrape_progress(task_id, {"status": "cancelled", "completed": 0, "total": 0, "results": [], "error": "Product inactive", "correlation_id": cid})
            return {"status": "cancelled", "results": [], "correlation_id": cid}
        if user_id and is_user_deleted(user_id):
            set_scrape_progress(task_id, {"status": "cancelled", "completed": 0, "total": 0, "results": [], "error": "Account deleted", "correlation_id": cid})
            return {"status": "cancelled", "results": [], "correlation_id": cid}

        competitors = (client.table("competitors").select("id, url, retailer_name").eq("product_id", product_id).execute().data or [])
        total = len(competitors)
        if total == 0:
            set_scrape_progress(task_id, {"status": "completed", "completed": 0, "total": 0, "results": [], "error": "No competitors found", "correlation_id": cid})
            return {"status": "completed", "results": [], "correlation_id": cid}

        set_scrape_progress(task_id, {"status": "scraping", "completed": 0, "total": total, "current": None, "results": [], "correlation_id": cid})
        results = []

        for i, competitor in enumerate(competitors):
            if is_product_deleted(product_id) or (user_id and is_user_deleted(user_id)):
                set_scrape_progress(task_id, {"status": "cancelled", "completed": i, "total": total, "current": None, "results": results, "error": "Scrape cancelled: account or product deleted", "correlation_id": cid})
                return {"status": "cancelled", "results": results, "correlation_id": cid}

            comp_id = competitor.get("id") if isinstance(competitor, dict) else None
            url = competitor.get("url") if isinstance(competitor, dict) else None
            retailer = (competitor.get("retailer_name") if isinstance(competitor, dict) else None) or _extract_domain(url or "")
            set_scrape_progress(task_id, {"status": "scraping", "completed": i, "total": total, "current": retailer, "results": results, "correlation_id": cid})

            if not comp_id or not url:
                results.append({"competitor_id": comp_id or "", "retailer": retailer, "price": None, "currency": "USD", "status": "failed", "error_message": "Invalid competitor configuration: missing competitor ID or URL", "failure_reason": ScrapeFailureReason.INVALID_URL, "retry_count": 0})
            else:
                try:
                    scrape_res = asyncio.run(scrape_url(url))
                    results.append(persist_scrape_result(client, comp_id, retailer, scrape_res, cid))
                except Exception as exc:
                    results.append(record_scrape_failure(client, comp_id, retailer, exc))

            set_scrape_progress(task_id, {"status": "scraping", "completed": i + 1, "total": total, "current": retailer, "results": results, "correlation_id": cid})

        try:
            set_scrape_progress(task_id, {"status": "completed", "completed": total, "total": total, "current": None, "results": results, "correlation_id": cid})
        finally:
            if not is_product_deleted(product_id) and not (user_id and is_user_deleted(user_id)):
                try:
                    from app.services.dashboard_cache import invalidate_dashboard_cache_for_product
                    invalidate_dashboard_cache_for_product(product_id)
                except Exception as exc:
                    logger.debug(f"Failed to invalidate dashboard cache for product {product_id}: {exc}")

        return {"status": "completed", "results": results, "correlation_id": cid}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=(Exception,), retry_backoff=True, retry_backoff_max=240)
def scrape_single_competitor(self, competitor_id: str) -> dict:
    """Scrape a single competitor URL, store result, and check for alerts."""
    cid = _resolve_correlation_id(self)
    with correlation_context(cid):
        client = get_supabase_client()
        task_id = getattr(getattr(self, "request", None), "id", None)
        cancel_reason, pid, uid = _check_competitor_active(client, competitor_id)
        if cancel_reason:
            return {"status": "cancelled", "reason": cancel_reason, "correlation_id": cid}

        try:
            if _was_scraped_today(client, competitor_id):
                return {"status": "skipped", "reason": "already_scraped_today", "correlation_id": cid}

            result = asyncio.run(scrape_and_check_alerts(competitor_id, correlation_id=cid))
            scrape_res, alert_res = result.get("scrape_result", {}), result.get("alert_result")

            if scrape_res.get("failure_reason") == ScrapeFailureReason.DATABASE_ERROR:
                if getattr(self, "request", None) and getattr(self.request, "id", None):
                    raise DatabasePersistenceError(scrape_res.get("error") or f"Database persistence failed for competitor {competitor_id}")

            return {
                "competitor_id": competitor_id,
                "scrape_status": scrape_res.get("status"),
                "price": scrape_res.get("price"),
                "currency": scrape_res.get("currency"),
                "error_message": scrape_res.get("error"),
                "failure_reason": scrape_res.get("failure_reason"),
                "retry_count": scrape_res.get("retry_count", 0),
                "alert_created": alert_res.get("alert_created", False) if alert_res else False,
                "correlation_id": cid,
            }
        finally:
            if task_id:
                try:
                    unregister_active_scrape_task(task_id=task_id, user_id=uid, product_id=pid, competitor_id=competitor_id)
                except Exception as unreg_exc:
                    logger.debug(f"Failed to unregister completed task {task_id}: {unreg_exc}")


@celery_app.task(bind=True)
def scrape_all_products(self) -> dict:
    """Scrape all active competitors in batches (scheduled daily at 2 AM UTC)."""
    cid = _resolve_correlation_id(self)
    with correlation_context(cid):
        client = get_supabase_client()
        products_res = client.table("products").select("id, user_id").eq("is_active", True).execute()
        if not products_res.data:
            return {"total": 0, "queued": 0, "correlation_id": cid}

        prod_to_user = {p["id"]: p.get("user_id") for p in products_res.data if isinstance(p, dict) and "id" in p}
        comp_res = client.table("competitors").select("id, product_id").in_("product_id", list(prod_to_user.keys())).execute()
        competitors = comp_res.data or []
        queued = 0

        for i in range(0, len(competitors), BATCH_SIZE):
            for competitor in competitors[i:i + BATCH_SIZE]:
                comp_id, prod_id = competitor["id"], competitor.get("product_id")
                task = scrape_single_competitor.delay(comp_id)
                if task and getattr(task, "id", None):
                    register_active_scrape_task(user_id=prod_to_user.get(prod_id), product_id=prod_id, task_id=task.id, competitor_id=comp_id)
                queued += 1

        return {"total": len(competitors), "queued": queued, "correlation_id": cid}


@celery_app.task
def check_worker_health() -> dict:
    """Health check task to verify worker is responsive."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@celery_app.task(bind=True)
def send_alert_digests(self, force: bool = False, dry_run: bool = False) -> dict:
    """Run batched digests for configured users."""
    cid = _resolve_correlation_id(self)
    with correlation_context(cid):
        client = get_supabase_client()
        settings_res = client.table("user_alert_settings").select("user_id").or_("email_enabled.eq.true,webhook_enabled.eq.true").execute()
        digest_service, results = DigestService(), []

        for setting in settings_res.data or []:
            user_id = setting["user_id"]
            try:
                auth_res = client.auth.admin.get_user_by_id(user_id)
                email = getattr(getattr(auth_res, "user", None), "email", None)
                results.append(digest_service.run_for_user(user_id, email, force=force, dry_run=dry_run, correlation_id=cid))
            except Exception as exc:
                logger.exception("Digest batch failed for user %s: %s", user_id, exc)
                results.append({"user_id": user_id, "status": "failed", "alerts_count": 0, "correlation_id": cid})

        return {
            "total_users": len(results),
            "sent": sum(1 for r in results if r["status"] == "sent"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "skipped": sum(1 for r in results if r["status"] == "skipped"),
            "dry_run": sum(1 for r in results if r["status"] == "dry_run"),
            "alerts_sent": sum(r.get("alerts_count", 0) for r in results if r["status"] == "sent"),
            "results": results,
            "correlation_id": cid,
        }


@celery_app.task(bind=True)
def cleanup_old_alerts(self) -> dict:
    """Clean up old pending alerts included in digests (older than 7 days)."""
    cid = _resolve_correlation_id(self)
    with correlation_context(cid):
        deleted_count = asyncio.run(AlertService().cleanup_old_pending_alerts())
        return {"deleted_count": deleted_count, "correlation_id": cid}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10, retry_backoff=True)
def dispatch_webhook_alert(self, user_id: str, alert_data: dict, correlation_id: str | None = None) -> dict:
    """Deliver real-time webhook notification for a price drop alert."""
    cid = correlation_id or _resolve_correlation_id(self)
    with correlation_context(cid):
        client = get_supabase_client()
        try:
            settings_res = client.table("user_alert_settings").select("*").eq("user_id", user_id).execute()
            if not settings_res.data or not settings_res.data[0].get("webhook_enabled") or not settings_res.data[0].get("webhook_url"):
                return {"success": False, "reason": "webhook_not_enabled" if settings_res.data else "settings_not_found", "correlation_id": cid}
            settings = settings_res.data[0]
            result = WebhookService().send_alert(webhook_url=settings["webhook_url"], payload=alert_data, webhook_secret=settings.get("webhook_secret"), correlation_id=cid)
            record_alert_history(client, user_id, alert_data, result)
            return {"success": result.get("success", False), "status_code": result.get("status_code"), "error": result.get("error"), "correlation_id": cid}
        except Exception as exc:
            logger.exception("Error dispatching webhook alert for user %s: %s", user_id, exc)
            return {"success": False, "error": str(exc), "correlation_id": cid}
