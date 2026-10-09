"""Progress tracking and execution context helpers for Celery tasks."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import time
from urllib.parse import urlparse

import redis

from app.core.config import get_settings
from app.core.logging import correlation_context, generate_correlation_id, get_correlation_id
from app.services.alert_service import AlertService
from app.services.scraper.engine import ScrapeFailureReason, classify_scrape_exception
from app.services.scraper_service import scrape_url

logger = logging.getLogger(__name__)
_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


def set_scrape_progress(task_id: str, data: dict, ttl: int = 300):
    """Store scrape progress in Redis with TTL."""
    try:
        if isinstance(data, dict) and "correlation_id" not in data and (cid := get_correlation_id()):
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
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url[:30]


@contextmanager
def _task_context(task, correlation_id=None):
    req = getattr(task, "request", None)
    req_h = getattr(req, "headers", None) or {}
    cid = correlation_id or get_correlation_id() or req_h.get("correlation_id") or getattr(req, "correlation_id", None) or generate_correlation_id()
    with correlation_context(cid):
        if req is not None:
            req.correlation_id = cid
            if not getattr(req, "headers", None):
                req.headers = {}
            req.headers["correlation_id"] = cid
        yield cid


def _execute_manual_competitor(client, comp: dict, retailer: str, cid: str, scrape_fn=None) -> dict:
    cid_val = comp.get("id") if isinstance(comp, dict) else None
    url = comp.get("url") if isinstance(comp, dict) else None
    if not cid_val or not url:
        return {"competitor_id": cid_val or "", "retailer": retailer, "price": None, "currency": "USD", "status": "failed", "error_message": "Invalid competitor configuration: missing competitor ID or URL", "failure_reason": ScrapeFailureReason.INVALID_URL, "retry_count": 0}

    scrape_action = scrape_fn or scrape_url
    try:
        res = asyncio.run(scrape_action(url))
        p_data = {"competitor_id": cid_val, "price": float(res.price) if res.price else None, "currency": res.currency, "scrape_status": res.status, "error_message": res.error_message}
        db_err = None
        for attempt in range(3):
            try:
                client.table("price_history").insert(p_data).execute()
                db_err = None
                break
            except Exception as e:
                db_err = e
                if attempt < 2:
                    time.sleep(min(0.05 * (2 ** attempt), 0.5))

        if db_err:
            return {"competitor_id": cid_val, "retailer": retailer, "price": None, "currency": res.currency, "status": "failed", "error_message": f"Failed to record price history: {str(db_err)[:150]}", "failure_reason": ScrapeFailureReason.DATABASE_ERROR, "retry_count": res.retry_count}

        if res.status == "success" and res.price:
            try:
                asyncio.run(AlertService().check_price_change_and_alert(competitor_id=cid_val, new_price=res.price, currency=res.currency, correlation_id=cid))
            except Exception as alert_exc:
                logger.debug(f"Alert check failed for competitor {cid_val}: {alert_exc}")

        return {"competitor_id": cid_val, "retailer": retailer, "price": str(res.price) if res.price else None, "currency": res.currency, "status": res.status, "error_message": res.error_message, "failure_reason": res.failure_reason, "retry_count": res.retry_count}
    except Exception as e:
        reason, friendly = classify_scrape_exception(e)
        try:
            client.table("price_history").insert({"competitor_id": cid_val, "price": None, "currency": "USD", "scrape_status": "failed", "error_message": friendly}).execute()
        except Exception:
            pass
        return {"competitor_id": cid_val, "retailer": retailer, "price": None, "currency": "USD", "status": "failed", "error_message": friendly, "failure_reason": reason, "retry_count": 0}
