"""
Account lifecycle and deletion cleanup service.

Ensures that when an account is deleted:
- All personal data, notification credentials, and tracked products are purged across all storage tables.
- Active and queued background scraping tasks are cancelled and prevented from continuing.
- In-flight and cached dashboard state is invalidated.
- Active user sessions and tokens are revoked immediately.
"""

from collections import defaultdict
import logging
import threading
from typing import Any, Optional

from app.db.database import get_supabase_client
from app.services.dashboard_cache import invalidate_dashboard_cache, get_dashboard_cache

logger = logging.getLogger(__name__)

# Concurrency control for in-process tracking fallbacks
_account_lock = threading.Lock()
_deleted_users: set[str] = set()
_deleted_products: set[str] = set()
_active_tasks_by_user: dict[str, set[str]] = defaultdict(set)
_active_tasks_by_product: dict[str, set[str]] = defaultdict(set)

DELETION_TTL_SECONDS = 86400  # 24 hours retention for deletion markers in Redis


def _get_redis_conn() -> Optional[Any]:
    """Retrieve active Redis client via dashboard cache fallback manager."""
    try:
        return get_dashboard_cache()._get_redis()
    except Exception as exc:
        logger.debug(f"Redis not reachable for account service: {exc}")
        return None


def register_active_scrape_task(user_id: str, product_id: str, task_id: str) -> None:
    """
    Register an active background scrape task to enable revocation upon account deletion.

    Records the association in Redis when available, maintaining an in-memory fallback.
    """
    if not task_id:
        return

    with _account_lock:
        if user_id:
            _active_tasks_by_user[user_id].add(task_id)
        if product_id:
            _active_tasks_by_product[product_id].add(task_id)

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            if user_id:
                user_key = f"active_tasks:user:{user_id}"
                redis_conn.sadd(user_key, task_id)
                redis_conn.expire(user_key, 3600)
            if product_id:
                prod_key = f"active_tasks:product:{product_id}"
                redis_conn.sadd(prod_key, task_id)
                redis_conn.expire(prod_key, 3600)
        except Exception as exc:
            logger.debug(f"Failed to register task {task_id} in Redis: {exc}")


def mark_user_deleted(user_id: str) -> None:
    """Record that a user account has been deleted to guard background workers and token auth."""
    if not user_id:
        return

    with _account_lock:
        _deleted_users.add(user_id)

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            redis_conn.setex(f"deleted_user:{user_id}", DELETION_TTL_SECONDS, "1")
        except Exception as exc:
            logger.debug(f"Failed to set deleted_user in Redis for {user_id}: {exc}")

    # Also invalidate user sessions across security layer
    try:
        from app.core.security import revoke_user_sessions
        revoke_user_sessions(user_id, ttl_seconds=DELETION_TTL_SECONDS)
    except Exception as exc:
        logger.debug(f"Failed to invoke revoke_user_sessions for {user_id}: {exc}")


def is_user_deleted(user_id: str) -> bool:
    """Check whether a user account has been marked deleted."""
    if not user_id:
        return False

    with _account_lock:
        if user_id in _deleted_users:
            return True

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            val = redis_conn.get(f"deleted_user:{user_id}")
            if val:
                with _account_lock:
                    _deleted_users.add(user_id)
                return True
        except Exception as exc:
            logger.debug(f"Failed to query deleted_user in Redis for {user_id}: {exc}")

    # Cross-check session revocation marker
    try:
        from app.core.security import is_session_revoked
        if is_session_revoked(user_id):
            return True
    except Exception:
        pass

    return False


def mark_product_deleted(product_id: str) -> None:
    """Record that a product group has been deleted to halt background scraping."""
    if not product_id:
        return

    with _account_lock:
        _deleted_products.add(product_id)

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            redis_conn.setex(f"deleted_product:{product_id}", DELETION_TTL_SECONDS, "1")
        except Exception as exc:
            logger.debug(f"Failed to set deleted_product in Redis for {product_id}: {exc}")


def is_product_deleted(product_id: str) -> bool:
    """Check whether a product group has been marked deleted."""
    if not product_id:
        return False

    with _account_lock:
        if product_id in _deleted_products:
            return True

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            val = redis_conn.get(f"deleted_product:{product_id}")
            if val:
                with _account_lock:
                    _deleted_products.add(product_id)
                return True
        except Exception as exc:
            logger.debug(f"Failed to query deleted_product in Redis for {product_id}: {exc}")

    return False


def cancel_tasks_for_user(
    user_id: str,
    product_ids: list[str] | None = None,
    competitor_ids: list[str] | None = None,
) -> list[str]:
    """
    Cancel all queued and in-flight scraping tasks for a user and their products.

    - Marks user and products as deleted to trigger fast exits in workers
    - Revokes Celery tasks using control broadcast
    - Marks progress keys in Redis as cancelled
    - Returns the list of revoked task IDs
    """
    product_ids = product_ids or []
    mark_user_deleted(user_id)
    for pid in product_ids:
        mark_product_deleted(pid)

    task_ids: set[str] = set()

    # 1. Collect in-memory tracked tasks
    with _account_lock:
        if user_id in _active_tasks_by_user:
            task_ids.update(_active_tasks_by_user.pop(user_id, set()))
        for pid in product_ids:
            if pid in _active_tasks_by_product:
                task_ids.update(_active_tasks_by_product.pop(pid, set()))

    # 2. Collect Redis tracked tasks
    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            user_key = f"active_tasks:user:{user_id}"
            tasks = redis_conn.smembers(user_key)
            if tasks:
                task_ids.update(t if isinstance(t, str) else t.decode("utf-8") for t in tasks)
            redis_conn.delete(user_key)

            for pid in product_ids:
                prod_key = f"active_tasks:product:{pid}"
                ptasks = redis_conn.smembers(prod_key)
                if ptasks:
                    task_ids.update(t if isinstance(t, str) else t.decode("utf-8") for t in ptasks)
                redis_conn.delete(prod_key)
        except Exception as exc:
            logger.debug(f"Error fetching active tasks from Redis for user {user_id}: {exc}")

    # 3. Revoke via Celery control
    try:
        from app.tasks.celery_app import celery_app
        for tid in task_ids:
            try:
                celery_app.control.revoke(tid, terminate=True)
                logger.info(f"Revoked Celery task {tid} for deleted user {user_id}")
            except Exception as rev_exc:
                logger.debug(f"Failed to revoke task {tid}: {rev_exc}")
    except Exception as exc:
        logger.debug(f"Celery app control not available for revocation: {exc}")

    # 4. Update scrape progress keys in Redis to terminal 'cancelled'
    for tid in task_ids:
        try:
            from app.tasks.scraper_tasks import set_scrape_progress
            set_scrape_progress(tid, {
                "status": "cancelled",
                "completed": 0,
                "total": 0,
                "results": [],
                "error": "Account or product deleted",
            })
        except Exception as prog_exc:
            logger.debug(f"Failed to set progress cancelled for task {tid}: {prog_exc}")

    return list(task_ids)


def delete_user_account(user_id: str, client: Any = None) -> dict[str, Any]:
    """
    Execute comprehensive account deletion with cascading cleanup across all storage tables.

    Storage tables cleaned up:
    1. price_history (records for competitor URLs belonging to user products)
    2. competitors (monitored items belonging to user products)
    3. insights (AI insight summaries belonging to user products)
    4. tracking_jobs (progress state for user tracking jobs)
    5. pending_alerts (unprocessed price change alerts)
    6. alert_history (audit history of sent digests)
    7. user_alert_settings (notification channel preferences and webhook delivery credentials)
    8. products (tracked product groups)
    9. auth.users (Supabase identity record)

    Background jobs and sessions:
    - Cancels and revokes Celery tasks
    - Invalidates dashboard caches
    - Revokes active sessions and tokens
    """
    if not user_id:
        raise ValueError("user_id must be provided for account deletion")

    if client is None:
        client = get_supabase_client()

    summary: dict[str, Any] = {
        "user_id": user_id,
        "products_found": 0,
        "competitors_found": 0,
        "tasks_revoked": 0,
        "tables_cleaned": [],
    }

    # Step 1: Discover products and competitors owned by the user
    product_ids: list[str] = []
    try:
        products_res = client.table("products").select("id").eq("user_id", user_id).execute()
        if products_res and products_res.data:
            product_ids = [
                p["id"] for p in products_res.data
                if isinstance(p, dict) and "id" in p and p["id"]
            ]
        summary["products_found"] = len(product_ids)
    except Exception as exc:
        logger.warning(f"Failed to list products for user {user_id}: {exc}")

    competitor_ids: list[str] = []
    if product_ids:
        try:
            comp_res = client.table("competitors").select("id").in_("product_id", product_ids).execute()
            if comp_res and comp_res.data:
                competitor_ids = [
                    c["id"] for c in comp_res.data
                    if isinstance(c, dict) and "id" in c and c["id"]
                ]
            summary["competitors_found"] = len(competitor_ids)
        except Exception as exc:
            logger.warning(f"Failed to list competitors for user {user_id}: {exc}")

    # Step 2: Stop background tasks and mark entities deleted before starting DB deletions
    revoked_tasks = cancel_tasks_for_user(
        user_id=user_id,
        product_ids=product_ids,
        competitor_ids=competitor_ids,
    )
    summary["tasks_revoked"] = len(revoked_tasks)

    # Step 3: Delete dependent child tables first, then parents to guarantee zero orphaned rows

    # 3a. price_history (grandchild table of products via competitors)
    if competitor_ids:
        try:
            client.table("price_history").delete().in_("competitor_id", competitor_ids).execute()
            summary["tables_cleaned"].append("price_history")
        except Exception as exc:
            logger.error(f"Failed to delete price_history for user {user_id}: {exc}")
            raise

    # 3b. competitors (child table of products)
    if product_ids:
        try:
            client.table("competitors").delete().in_("product_id", product_ids).execute()
            summary["tables_cleaned"].append("competitors")
        except Exception as exc:
            logger.error(f"Failed to delete competitors for user {user_id}: {exc}")
            raise

    # 3c. insights (child table of products)
    if product_ids:
        try:
            client.table("insights").delete().in_("product_id", product_ids).execute()
            summary["tables_cleaned"].append("insights")
        except Exception as exc:
            logger.error(f"Failed to delete insights for user {user_id}: {exc}")
            raise

    # 3d. tracking_jobs (direct user table and optional product group relation)
    try:
        client.table("tracking_jobs").delete().eq("user_id", user_id).execute()
        summary["tables_cleaned"].append("tracking_jobs")
    except Exception as exc:
        logger.error(f"Failed to delete tracking_jobs for user {user_id}: {exc}")
        raise

    # 3e. pending_alerts (direct user table referencing products and competitors)
    try:
        client.table("pending_alerts").delete().eq("user_id", user_id).execute()
        summary["tables_cleaned"].append("pending_alerts")
    except Exception as exc:
        logger.error(f"Failed to delete pending_alerts for user {user_id}: {exc}")
        raise

    # 3f. alert_history (direct user table recording sent digests)
    try:
        client.table("alert_history").delete().eq("user_id", user_id).execute()
        summary["tables_cleaned"].append("alert_history")
    except Exception as exc:
        logger.error(f"Failed to delete alert_history for user {user_id}: {exc}")
        raise

    # 3g. user_alert_settings (direct user table holding notification credentials & webhook secret)
    try:
        client.table("user_alert_settings").delete().eq("user_id", user_id).execute()
        summary["tables_cleaned"].append("user_alert_settings")
    except Exception as exc:
        logger.error(f"Failed to delete user_alert_settings for user {user_id}: {exc}")
        raise

    # 3h. products (direct user table)
    try:
        client.table("products").delete().eq("user_id", user_id).execute()
        summary["tables_cleaned"].append("products")
    except Exception as exc:
        logger.error(f"Failed to delete products for user {user_id}: {exc}")
        raise

    # Step 4: Remove identity from Supabase Auth admin API if accessible
    try:
        admin_auth = getattr(getattr(client, "auth", None), "admin", None)
        if admin_auth and hasattr(admin_auth, "delete_user"):
            admin_auth.delete_user(user_id)
            summary["tables_cleaned"].append("auth.users")
        else:
            logger.debug("Admin auth delete_user not supported on current client instance")
    except Exception as exc:
        logger.warning(f"Could not delete auth user {user_id} via Supabase admin API: {exc}")

    # Step 5: Invalidate dashboard cache immediately
    try:
        invalidate_dashboard_cache(user_id)
    except Exception as exc:
        logger.debug(f"Dashboard cache invalidation error for {user_id}: {exc}")

    # Step 6: Revoke active session tokens
    try:
        from app.core.security import revoke_user_sessions
        revoke_user_sessions(user_id)
    except Exception as exc:
        logger.debug(f"Session revocation failed for {user_id}: {exc}")

    return summary


def clear_account_deletion_state() -> None:
    """Clear in-memory state tracking (intended for unit and integration test teardown)."""
    with _account_lock:
        _deleted_users.clear()
        _deleted_products.clear()
        _active_tasks_by_user.clear()
        _active_tasks_by_product.clear()
