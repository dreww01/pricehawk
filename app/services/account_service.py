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
_deleting_users: set[str] = set()
_deleted_products: set[str] = set()
_active_tasks_by_user: dict[str, set[str]] = defaultdict(set)
_active_tasks_by_product: dict[str, set[str]] = defaultdict(set)
_active_tasks_by_competitor: dict[str, set[str]] = defaultdict(set)

DELETION_TTL_SECONDS = 86400  # 24 hours retention for deletion markers in Redis
DELETING_IN_PROGRESS_TTL_SECONDS = 300  # 5 minutes transient retention for in-flight deletion


def _get_redis_conn() -> Optional[Any]:
    """Retrieve active Redis client via dashboard cache fallback manager."""
    try:
        return get_dashboard_cache()._get_redis()
    except Exception as exc:
        logger.debug(f"Redis not reachable for account service: {exc}")
        return None


def register_active_scrape_task(
    user_id: str | None = None,
    product_id: str | None = None,
    task_id: str = "",
    competitor_id: str | None = None,
) -> None:
    """
    Register an active background scrape task to enable revocation upon account deletion.

    Records the association across user, product, and competitor in Redis and memory.
    """
    if not task_id:
        return

    with _account_lock:
        if user_id:
            _active_tasks_by_user[user_id].add(task_id)
        if product_id:
            _active_tasks_by_product[product_id].add(task_id)
        if competitor_id:
            _active_tasks_by_competitor[competitor_id].add(task_id)

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
            if competitor_id:
                comp_key = f"active_tasks:competitor:{competitor_id}"
                redis_conn.sadd(comp_key, task_id)
                redis_conn.expire(comp_key, 3600)
        except Exception as exc:
            logger.debug(f"Failed to register task {task_id} in Redis: {exc}")


def unregister_active_scrape_task(
    task_id: str,
    user_id: str | None = None,
    product_id: str | None = None,
    competitor_id: str | None = None,
) -> None:
    """
    Unregister a completed or aborted background scrape task to keep active task sets clean.
    """
    if not task_id:
        return

    with _account_lock:
        if user_id and user_id in _active_tasks_by_user:
            _active_tasks_by_user[user_id].discard(task_id)
            if not _active_tasks_by_user[user_id]:
                del _active_tasks_by_user[user_id]
        if product_id and product_id in _active_tasks_by_product:
            _active_tasks_by_product[product_id].discard(task_id)
            if not _active_tasks_by_product[product_id]:
                del _active_tasks_by_product[product_id]
        if competitor_id and competitor_id in _active_tasks_by_competitor:
            _active_tasks_by_competitor[competitor_id].discard(task_id)
            if not _active_tasks_by_competitor[competitor_id]:
                del _active_tasks_by_competitor[competitor_id]

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            if user_id:
                redis_conn.srem(f"active_tasks:user:{user_id}", task_id)
            if product_id:
                redis_conn.srem(f"active_tasks:product:{product_id}", task_id)
            if competitor_id:
                redis_conn.srem(f"active_tasks:competitor:{competitor_id}", task_id)
        except Exception as exc:
            logger.debug(f"Failed to unregister task {task_id} in Redis: {exc}")


def mark_deletion_in_progress(user_id: str) -> None:
    """Record that an account deletion is in progress to guard background tasks without revoking sessions."""
    if not user_id:
        return

    with _account_lock:
        _deleting_users.add(user_id)

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            redis_conn.setex(f"deleting_user:{user_id}", DELETING_IN_PROGRESS_TTL_SECONDS, "1")
        except Exception as exc:
            logger.debug(f"Failed to set deleting_user in Redis for {user_id}: {exc}")


def clear_deletion_in_progress(user_id: str) -> None:
    """Clear deletion-in-progress state upon completion or rollback."""
    if not user_id:
        return

    with _account_lock:
        _deleting_users.discard(user_id)

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            redis_conn.delete(f"deleting_user:{user_id}")
        except Exception as exc:
            logger.debug(f"Failed to clear deleting_user in Redis for {user_id}: {exc}")


def is_deletion_in_progress(user_id: str) -> bool:
    """Check whether account deletion is currently underway for a user."""
    if not user_id:
        return False

    with _account_lock:
        if user_id in _deleting_users:
            return True

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            val = redis_conn.get(f"deleting_user:{user_id}")
            if val:
                with _account_lock:
                    _deleting_users.add(user_id)
                return True
        except Exception as exc:
            logger.debug(f"Failed to query deleting_user in Redis for {user_id}: {exc}")

    return False


def mark_user_deleted(user_id: str) -> None:
    """Record that a user account has been permanently deleted to guard background workers."""
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


def unmark_user_deleted(user_id: str) -> None:
    """Clear deletion and revocation markers for a user if deletion fails, keeping retries safe."""
    if not user_id:
        return

    with _account_lock:
        _deleted_users.discard(user_id)
        _deleting_users.discard(user_id)

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            redis_conn.delete(f"deleted_user:{user_id}")
            redis_conn.delete(f"deleting_user:{user_id}")
        except Exception as exc:
            logger.debug(f"Failed to clear deleted/deleting user in Redis for {user_id}: {exc}")

    try:
        from app.core.security import unrevoke_user_sessions
        unrevoke_user_sessions(user_id)
    except Exception as exc:
        logger.debug(f"Failed to invoke unrevoke_user_sessions for {user_id}: {exc}")


def is_user_deleted(user_id: str) -> bool:
    """Check whether a user account has been marked deleted or is currently undergoing deletion."""
    if not user_id:
        return False

    with _account_lock:
        if user_id in _deleted_users or user_id in _deleting_users:
            return True

    redis_conn = _get_redis_conn()
    if redis_conn:
        try:
            val = redis_conn.get(f"deleted_user:{user_id}") or redis_conn.get(f"deleting_user:{user_id}")
            if val:
                return True
        except Exception as exc:
            logger.debug(f"Failed to query deleted/deleting user in Redis for {user_id}: {exc}")

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
    competitor_ids = competitor_ids or []
    mark_deletion_in_progress(user_id)
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
        for cid in competitor_ids:
            if cid in _active_tasks_by_competitor:
                task_ids.update(_active_tasks_by_competitor.pop(cid, set()))

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

            for cid in competitor_ids:
                comp_key = f"active_tasks:competitor:{cid}"
                ctasks = redis_conn.smembers(comp_key)
                if ctasks:
                    task_ids.update(t if isinstance(t, str) else t.decode("utf-8") for t in ctasks)
                redis_conn.delete(comp_key)
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

    try:
        # Mark deletion in progress to guard background tasks
        mark_deletion_in_progress(user_id)

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
            logger.error(f"Failed to list products for user {user_id}: {exc}")
            raise RuntimeError(f"Database error discovering products for {user_id}: {exc}") from exc

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
                logger.error(f"Failed to list competitors for user {user_id}: {exc}")
                raise RuntimeError(f"Database error discovering competitors for {user_id}: {exc}") from exc

        # Step 2: Stop background tasks
        revoked_tasks = cancel_tasks_for_user(
            user_id=user_id,
            product_ids=product_ids,
            competitor_ids=competitor_ids,
        )
        summary["tasks_revoked"] = len(revoked_tasks)

        # Attempt atomic server-side deletion RPC if supported
        atomic_success = False
        if hasattr(client, "rpc") and callable(getattr(client, "rpc", None)):
            try:
                rpc_res = client.rpc("delete_user_account_atomic", {"target_user_id": user_id}).execute()
                if rpc_res and not getattr(rpc_res, "error", None):
                    summary["tables_cleaned"].extend([
                        "price_history", "competitors", "insights", "tracking_jobs",
                        "pending_alerts", "alert_history", "user_alert_settings", "products"
                    ])
                    atomic_success = True
            except Exception as rpc_exc:
                logger.debug(f"Server-side atomic deletion RPC not executed, proceeding with ordered cascade: {rpc_exc}")

        if not atomic_success:
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

        # Step 4: Remove identity from Supabase Auth admin API (required and verified)
        admin_auth = getattr(getattr(client, "auth", None), "admin", None)
        if not admin_auth or not callable(getattr(admin_auth, "delete_user", None)):
            error_msg = f"Supabase Auth admin deletion API is unavailable for user {user_id}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        try:
            delete_res = admin_auth.delete_user(user_id)
        except Exception as exc:
            logger.error(f"Failed to delete auth user {user_id} via Supabase admin API: {exc}")
            raise RuntimeError(f"Could not delete auth user {user_id} via Supabase admin API: {exc}") from exc

        if delete_res is False:
            error_msg = f"Supabase Auth admin deletion was not confirmed for user {user_id}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if isinstance(delete_res, dict):
            if delete_res.get("error"):
                error_msg = f"Supabase Auth admin deletion error: {delete_res.get('error')}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            if delete_res.get("success") is False or delete_res.get("deleted") is False:
                error_msg = f"Supabase Auth admin deletion was not confirmed for user {user_id}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
        elif hasattr(delete_res, "error") and getattr(delete_res, "error", None):
            error_msg = f"Supabase Auth admin deletion error: {getattr(delete_res, 'error')}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        elif hasattr(delete_res, "status_code") and getattr(delete_res, "status_code", 200) >= 400:
            error_msg = f"Supabase Auth admin deletion failed with status {delete_res.status_code}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        summary["tables_cleaned"].append("auth.users")

        # Step 5: Invalidate dashboard cache immediately
        try:
            invalidate_dashboard_cache(user_id)
        except Exception as exc:
            logger.debug(f"Dashboard cache invalidation error for {user_id}: {exc}")

        # Step 6: Mark user permanently deleted and clear deletion-in-progress
        mark_user_deleted(user_id)
        clear_deletion_in_progress(user_id)

        # Step 7: Revoke active session tokens across security layer
        try:
            from app.core.security import revoke_user_sessions
            revoke_user_sessions(user_id)
        except Exception as exc:
            logger.debug(f"Session revocation failed for {user_id}: {exc}")

        return summary

    except Exception:
        clear_deletion_in_progress(user_id)
        unmark_user_deleted(user_id)
        raise


def clear_account_deletion_state() -> None:
    """Clear in-memory state tracking (intended for unit and integration test teardown)."""
    with _account_lock:
        _deleted_users.clear()
        _deleting_users.clear()
        _deleted_products.clear()
        _active_tasks_by_user.clear()
        _active_tasks_by_product.clear()
        _active_tasks_by_competitor.clear()
