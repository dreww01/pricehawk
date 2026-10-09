"""Celery tasks for scraping, alert evaluation, and digest dispatching."""
import asyncio
from datetime import datetime, timezone
import logging

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
from app.tasks.celery_app import celery_app
from app.tasks.progress import (
    _execute_manual_competitor,
    _extract_domain,
    _get_today_start_utc,
    _task_context,
    get_scrape_progress,
    set_scrape_progress,
)

logger = logging.getLogger(__name__)
BATCH_SIZE = 50


def _was_scraped_today(client, competitor_id: str) -> bool:
    res = client.table("price_history").select("id").eq("competitor_id", competitor_id).gte("scraped_at", _get_today_start_utc()).limit(1).execute()
    return bool(res.data)


@celery_app.task(bind=True)
def scrape_product_manual(self, product_id: str) -> dict:
    """Scrape all competitors for a product (manual trigger)."""
    with _task_context(self) as cid:
        task_id = getattr(getattr(self, "request", None), "id", None) or "manual-task"
        client = get_supabase_client()
        if is_product_deleted(product_id):
            set_scrape_progress(task_id, {"status": "cancelled", "completed": 0, "total": 0, "results": [], "error": "Account or product deleted", "correlation_id": cid})
            return {"status": "cancelled", "results": [], "correlation_id": cid}

        user_id = None
        try:
            prod_res = client.table("products").select("id, user_id, is_active").eq("id", product_id).execute()
            if prod_res and prod_res.data:
                p_row = prod_res.data[0]
                if p_row.get("is_active") is False or ((user_id := p_row.get("user_id")) and is_user_deleted(user_id)):
                    set_scrape_progress(task_id, {"status": "cancelled", "completed": 0, "total": 0, "results": [], "error": "Product inactive" if p_row.get("is_active") is False else "Account deleted", "correlation_id": cid})
                    return {"status": "cancelled", "results": [], "correlation_id": cid}
        except Exception as exc:
            logger.debug(f"Product status verification error for {product_id}: {exc}")

        comps = (client.table("competitors").select("id, url, retailer_name").eq("product_id", product_id).execute()).data or []
        total = len(comps)
        if total == 0:
            set_scrape_progress(task_id, {"status": "completed", "completed": 0, "total": 0, "results": [], "error": "No competitors found", "correlation_id": cid})
            return {"status": "completed", "results": [], "correlation_id": cid}

        set_scrape_progress(task_id, {"status": "scraping", "completed": 0, "total": total, "current": None, "results": [], "correlation_id": cid})
        results = []
        for i, comp in enumerate(comps):
            if is_product_deleted(product_id) or (user_id and is_user_deleted(user_id)):
                set_scrape_progress(task_id, {"status": "cancelled", "completed": i, "total": total, "current": None, "results": results, "error": "Scrape cancelled: account or product deleted", "correlation_id": cid})
                return {"status": "cancelled", "results": results, "correlation_id": cid}

            retailer = (comp.get("retailer_name") if isinstance(comp, dict) else None) or _extract_domain(comp.get("url", "") if isinstance(comp, dict) else "")
            set_scrape_progress(task_id, {"status": "scraping", "completed": i, "total": total, "current": retailer, "results": results, "correlation_id": cid})
            res = _execute_manual_competitor(client, comp, retailer, cid, scrape_fn=scrape_url)
            results.append(res)
            set_scrape_progress(task_id, {"status": "scraping", "completed": i + 1, "total": total, "current": retailer, "results": results, "correlation_id": cid})

        try:
            set_scrape_progress(task_id, {"status": "completed", "completed": total, "total": total, "current": None, "results": results, "correlation_id": cid})
        finally:
            if not is_product_deleted(product_id) and not (user_id and is_user_deleted(user_id)):
                try:
                    from app.services.dashboard_cache import invalidate_dashboard_cache_for_product
                    invalidate_dashboard_cache_for_product(product_id)
                except Exception:
                    pass
        return {"status": "completed", "results": results, "correlation_id": cid}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=(Exception,), retry_backoff=True, retry_backoff_max=240)
def scrape_single_competitor(self, competitor_id: str) -> dict:
    """Scrape a single competitor URL, store result, and check for alerts."""
    with _task_context(self) as cid:
        client = get_supabase_client()
        task_id = getattr(getattr(self, "request", None), "id", None)
        pid, uid = None, None
        try:
            try:
                comp_res = client.table("competitors").select("id, product_id, products(id, user_id, is_active)").eq("id", competitor_id).execute()
                if not comp_res or not comp_res.data:
                    return {"status": "cancelled", "reason": "competitor_deleted_or_not_found", "correlation_id": cid}
                comp_rec = comp_res.data[0]
                if isinstance(comp_rec, dict):
                    pid = comp_rec.get("product_id")
                    if pid and is_product_deleted(pid):
                        return {"status": "cancelled", "reason": "product_deleted", "correlation_id": cid}
                    p_data = comp_rec.get("products")
                    if isinstance(p_data, dict):
                        if p_data.get("is_active") is False:
                            return {"status": "cancelled", "reason": "product_inactive", "correlation_id": cid}
                        uid = p_data.get("user_id")
                        if uid and is_user_deleted(uid):
                            return {"status": "cancelled", "reason": "user_deleted", "correlation_id": cid}
            except Exception as exc:
                logger.debug(f"Competitor active check failed for {competitor_id}: {exc}")

            if _was_scraped_today(client, competitor_id):
                return {"status": "skipped", "reason": "already_scraped_today", "correlation_id": cid}

            result = asyncio.run(scrape_and_check_alerts(competitor_id, correlation_id=cid))
            scrape_res = result.get("scrape_result", {})
            alert_res = result.get("alert_result")

            if scrape_res.get("failure_reason") == ScrapeFailureReason.DATABASE_ERROR and getattr(self, "request", None) and getattr(self.request, "id", None):
                raise DatabasePersistenceError(scrape_res.get("error") or f"Database persistence failed for competitor {competitor_id}")

            return {
                "competitor_id": competitor_id, "scrape_status": scrape_res.get("status"), "price": scrape_res.get("price"),
                "currency": scrape_res.get("currency"), "error_message": scrape_res.get("error"),
                "failure_reason": scrape_res.get("failure_reason"), "retry_count": scrape_res.get("retry_count", 0),
                "alert_created": alert_res.get("alert_created", False) if alert_res else False, "correlation_id": cid,
            }
        finally:
            if task_id:
                try:
                    unregister_active_scrape_task(task_id=task_id, user_id=uid, product_id=pid, competitor_id=competitor_id)
                except Exception:
                    pass


@celery_app.task(bind=True)
def scrape_all_products(self) -> dict:
    """Scrape all active competitors in batches."""
    with _task_context(self) as cid:
        client = get_supabase_client()
        prods = (client.table("products").select("id, user_id").eq("is_active", True).execute()).data or []
        if not prods:
            return {"total": 0, "queued": 0, "correlation_id": cid}

        prod_to_user = {p["id"]: p.get("user_id") for p in prods if isinstance(p, dict) and "id" in p}
        comps = (client.table("competitors").select("id, product_id").in_("product_id", list(prod_to_user.keys())).execute()).data or []
        total, queued = len(comps), 0
        for i in range(0, total, BATCH_SIZE):
            for comp in comps[i:i + BATCH_SIZE]:
                comp_id, prod_id = comp["id"], comp.get("product_id")
                task = scrape_single_competitor.delay(comp_id)
                if task and getattr(task, "id", None):
                    register_active_scrape_task(user_id=prod_to_user.get(prod_id), product_id=prod_id, task_id=task.id, competitor_id=comp_id)
                queued += 1
        return {"total": total, "queued": queued, "correlation_id": cid}


@celery_app.task
def check_worker_health() -> dict:
    """Health check task to verify worker is responsive."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@celery_app.task(bind=True)
def send_alert_digests(self, force: bool = False, dry_run: bool = False) -> dict:
    """Run batched digests for configured users."""
    with _task_context(self) as cid:
        client = get_supabase_client()
        settings_res = client.table("user_alert_settings").select("user_id").or_("email_enabled.eq.true,webhook_enabled.eq.true").execute()
        digest_service = DigestService()
        results = []
        for setting in settings_res.data or []:
            user_id = setting["user_id"]
            try:
                auth_resp = client.auth.admin.get_user_by_id(user_id)
                email = getattr(getattr(auth_resp, "user", None), "email", None)
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
            "results": results, "correlation_id": cid,
        }


@celery_app.task(bind=True)
def cleanup_old_alerts(self) -> dict:
    """Clean up old pending alerts included in digests."""
    with _task_context(self) as cid:
        return {"deleted_count": asyncio.run(AlertService().cleanup_old_pending_alerts()), "correlation_id": cid}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10, retry_backoff=True)
def dispatch_webhook_alert(self, user_id: str, alert_data: dict, correlation_id: str | None = None) -> dict:
    """Deliver real-time webhook notification for a price drop alert."""
    with _task_context(self, correlation_id) as cid:
        client = get_supabase_client()
        try:
            s_res = client.table("user_alert_settings").select("*").eq("user_id", user_id).execute()
            if not s_res.data:
                return {"success": False, "reason": "settings_not_found", "correlation_id": cid}
            settings = s_res.data[0]
            if not settings.get("webhook_enabled") or not settings.get("webhook_url"):
                return {"success": False, "reason": "webhook_not_enabled", "correlation_id": cid}

            from app.services.webhook_service import WebhookService
            res = WebhookService().send_alert(webhook_url=settings["webhook_url"], payload=alert_data, webhook_secret=settings.get("webhook_secret"), correlation_id=cid)
            a_type = alert_data.get("alert_type") or alert_data.get("event")
            try:
                client.table("alert_history").insert({
                    "user_id": user_id, "digest_sent_at": datetime.now(timezone.utc).isoformat(), "alerts_count": 1,
                    "price_drops": int(a_type == "price_drop"), "price_increases": int(a_type == "price_increase"), "currency_changes": int(a_type == "currency_changed"),
                    "email_status": "disabled", "webhook_status": "sent" if res.get("success") else "failed",
                    "response_code": res.get("status_code"), "error_message": res.get("error"),
                    "alert_ids": [aid] if (aid := alert_data.get("alert_id")) else [],
                }).execute()
            except Exception as h_err:
                logger.warning(f"Failed to record alert_history entry: {h_err}")
            return {"success": res.get("success", False), "status_code": res.get("status_code"), "error": res.get("error"), "correlation_id": cid}
        except Exception as exc:
            logger.exception("Error dispatching webhook alert for user %s: %s", user_id, exc)
            return {"success": False, "error": str(exc), "correlation_id": cid}
