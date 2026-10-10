"""Helper functions for Celery scraping and maintenance tasks."""
import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import redis

from app.core.config import get_settings
from app.core.logging import generate_correlation_id, get_correlation_id
from app.services.account_service import is_product_deleted, is_user_deleted
from app.services.scraper.exceptions import ScrapeFailureReason, classify_scrape_exception

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
BATCH_SIZE = 50


def _get_redis_client() -> redis.Redis:
    """Get or create Redis client (lazy init)."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def set_scrape_progress(task_id: str, data: dict, ttl: int = 300) -> None:
    """Store scrape progress in Redis with TTL."""
    try:
        if isinstance(data, dict) and "correlation_id" not in data:
            cid = get_correlation_id()
            if cid:
                data["correlation_id"] = cid
        _get_redis_client().setex(f"scrape:{task_id}", ttl, json.dumps(data))
    except Exception as exc:
        logger.warning(f"Failed to update scrape progress in Redis for task {task_id}: {exc}")


def get_scrape_progress(task_id: str) -> dict | None:
    """Get scrape progress from Redis."""
    try:
        data = _get_redis_client().get(f"scrape:{task_id}")
        return json.loads(data) if data else None
    except Exception as exc:
        logger.warning(f"Failed to read scrape progress from Redis for task {task_id}: {exc}")
        return None


def _get_today_start_utc() -> str:
    """Get today's start time in UTC as ISO string."""
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _was_scraped_today(client: Any, competitor_id: str) -> bool:
    """Check if competitor was already scraped today."""
    result = client.table("price_history").select("id").eq("competitor_id", competitor_id).gte("scraped_at", _get_today_start_utc()).limit(1).execute()
    return bool(result.data)


def _extract_domain(url: str) -> str:
    """Extract domain from URL for display."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url[:30]


def _resolve_correlation_id(task_instance: Any) -> str:
    """Extract or generate correlation id for Celery task invocation."""
    req = getattr(task_instance, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        get_correlation_id()
        or req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or generate_correlation_id()
    )
    if req is not None:
        req.correlation_id = cid
        if not getattr(req, "headers", None):
            req.headers = {}
        req.headers["correlation_id"] = cid
    return cid


def _check_product_user(client: Any, product_id: str) -> str | None | bool:
    """Check product active status and owning user id. Returns False if inactive."""
    try:
        prod_res = client.table("products").select("id, user_id, is_active").eq("id", product_id).execute()
        if prod_res and isinstance(prod_res.data, list) and len(prod_res.data) > 0:
            prod_row = prod_res.data[0]
            if isinstance(prod_row, dict):
                if prod_row.get("is_active") is False:
                    return False
                return prod_row.get("user_id")
    except Exception as exc:
        logger.debug(f"Product status verification error for {product_id}: {exc}")
    return None


def _check_competitor_active(client: Any, competitor_id: str) -> tuple[str | None, str | None, str | None]:
    """Verify competitor and product are active and not deleted. Returns (cancel_reason, pid, uid)."""
    try:
        comp_res = client.table("competitors").select("id, product_id, products(id, user_id, is_active)").eq("id", competitor_id).execute()
        if not comp_res or not comp_res.data:
            return "competitor_deleted_or_not_found", None, None
        comp_rec = comp_res.data[0]
        if isinstance(comp_rec, dict):
            pid = comp_rec.get("product_id")
            if pid and is_product_deleted(pid):
                return "product_deleted", pid, None
            prod_data = comp_rec.get("products")
            if isinstance(prod_data, dict):
                if prod_data.get("is_active") is False:
                    return "product_inactive", pid, None
                uid = prod_data.get("user_id")
                if uid and is_user_deleted(uid):
                    return "user_deleted", pid, uid
            return None, pid, uid
    except Exception as exc:
        logger.debug(f"Competitor active check failed for {competitor_id}: {exc}")
    return None, None, None


def persist_scrape_result(client: Any, competitor_id: str, retailer: str, scrape_result: Any, cid: str) -> dict[str, Any]:
    """Persist scrape result to price_history with retries, check alerts, and format response."""
    db_error = None
    price_data = {
        "competitor_id": competitor_id,
        "price": float(scrape_result.price) if scrape_result.price else None,
        "currency": scrape_result.currency,
        "scrape_status": scrape_result.status,
        "error_message": scrape_result.error_message,
    }
    for db_attempt in range(3):
        try:
            client.table("price_history").insert(price_data).execute()
            db_error = None
            break
        except Exception as db_exc:
            db_error = db_exc
            if db_attempt < 2:
                time.sleep(min(0.05 * (2 ** db_attempt), 0.5))

    if db_error:
        logger.error(f"Failed to persist price_history for competitor {competitor_id}: {db_error}")
        return {
            "competitor_id": competitor_id,
            "retailer": retailer,
            "price": None,
            "currency": scrape_result.currency,
            "status": "failed",
            "error_message": f"Failed to record price history: {str(db_error)[:150]}",
            "failure_reason": ScrapeFailureReason.DATABASE_ERROR,
            "retry_count": scrape_result.retry_count,
        }

    if scrape_result.status == "success" and scrape_result.price:
        try:
            from app.services.alert_service import AlertService
            asyncio.run(AlertService().check_price_change_and_alert(
                competitor_id=competitor_id,
                new_price=scrape_result.price,
                currency=scrape_result.currency,
                correlation_id=cid,
            ))
        except Exception as alert_exc:
            logger.debug(f"Alert check during manual scrape failed for competitor {competitor_id}: {alert_exc}")

    return {
        "competitor_id": competitor_id,
        "retailer": retailer,
        "price": str(scrape_result.price) if scrape_result.price else None,
        "currency": scrape_result.currency,
        "status": scrape_result.status,
        "error_message": scrape_result.error_message,
        "failure_reason": scrape_result.failure_reason,
        "retry_count": scrape_result.retry_count,
    }


def record_scrape_failure(client: Any, competitor_id: str, retailer: str, exc: Exception) -> dict[str, Any]:
    """Persist scrape failure to price_history and format response."""
    failure_reason, friendly_msg = classify_scrape_exception(exc)
    try:
        client.table("price_history").insert({
            "competitor_id": competitor_id,
            "price": None,
            "currency": "USD",
            "scrape_status": "failed",
            "error_message": friendly_msg,
        }).execute()
    except Exception as db_exc:
        logger.error(f"Failed to persist failure in price_history for competitor {competitor_id}: {db_exc}")

    return {
        "competitor_id": competitor_id,
        "retailer": retailer,
        "price": None,
        "currency": "USD",
        "status": "failed",
        "error_message": friendly_msg,
        "failure_reason": failure_reason,
        "retry_count": 0,
    }


def record_alert_history(client: Any, user_id: str, alert_data: dict[str, Any], result: dict[str, Any]) -> None:
    """Record webhook delivery audit record in alert_history."""
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
        "alert_ids": [alert_data["alert_id"]] if alert_data.get("alert_id") else [],
    }
    try:
        client.table("alert_history").insert(history_record).execute()
    except Exception as hist_err:
        logger.warning("Failed to record alert_history entry: %s", hist_err)
