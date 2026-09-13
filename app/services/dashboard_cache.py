"""
Dashboard caching service for high-frequency views.

Provides lightweight, short-lived caching for:
1. Dashboard summary numbers (products, competitors, alerts, insights counts)
2. Recent activity feeds (latest 10 price changes / alerts)
3. Recent product counts & summaries

Supports Redis when available with seamless thread-safe in-memory fallback,
automatic invalidation hooks, configurable TTLs, and cache hit/miss observability.
"""

import copy
import json
import logging
import threading
import time
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

STATS_PREFIX = "dashboard:stats"
ACTIVITY_PREFIX = "dashboard:activity"
PRODUCTS_PREFIX = "dashboard:products"


class DashboardCache:
    """
    Lightweight, thread-safe dashboard cache manager.
    Supports Redis backed with in-memory fallback.
    """

    def __init__(
        self,
        default_ttl: Optional[int] = None,
        redis_client: Optional[Any] = None,
        backend: Literal["auto", "redis", "memory"] = "auto",
        enabled: Optional[bool] = None,
    ) -> None:
        self.backend = backend
        self._custom_default_ttl = default_ttl
        self._redis_client = redis_client
        self._redis_disabled = False if backend != "memory" else True
        self._custom_enabled = enabled

        self._lock = threading.Lock()
        # In-memory storage: key -> (value, expire_at_timestamp)
        self._memory_store: dict[str, tuple[Any, float]] = {}
        # Track registered keys per user for O(1) invalidation: user_id -> set of keys
        self._user_keys: dict[str, set[str]] = {}

        # Observability metrics
        self._hits = 0
        self._misses = 0
        self._invalidations = 0

    @property
    def enabled(self) -> bool:
        """Check if dashboard caching is enabled."""
        if self._custom_enabled is not None:
            return self._custom_enabled
        try:
            from app.core.config import get_settings
            return get_settings().dashboard_cache_enabled
        except Exception:
            return True

    @property
    def default_ttl(self) -> int:
        """Get the default cache TTL in seconds."""
        if self._custom_default_ttl is not None:
            return self._custom_default_ttl
        try:
            from app.core.config import get_settings
            return get_settings().dashboard_cache_ttl_seconds
        except Exception:
            return 60

    def get_stats_ttl(self) -> int:
        """Get TTL specifically for stats card numbers."""
        try:
            from app.core.config import get_settings
            settings = get_settings()
            return settings.dashboard_cache_stats_ttl or self.default_ttl
        except Exception:
            return self.default_ttl

    def get_activity_ttl(self) -> int:
        """Get TTL specifically for recent activity feed."""
        try:
            from app.core.config import get_settings
            settings = get_settings()
            return settings.dashboard_cache_activity_ttl or self.default_ttl
        except Exception:
            return self.default_ttl

    def get_products_ttl(self) -> int:
        """Get TTL specifically for recent products list."""
        try:
            from app.core.config import get_settings
            settings = get_settings()
            return settings.dashboard_cache_products_ttl or self.default_ttl
        except Exception:
            return self.default_ttl

    def _get_redis(self) -> Optional[Any]:
        """Obtain Redis client or fall back cleanly if unavailable."""
        if self.backend == "memory" or self._redis_disabled:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            import redis
            from app.core.config import get_settings
            settings = get_settings()
            client = redis.from_url(
                settings.redis_url,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            client.ping()
            self._redis_client = client
            return client
        except Exception as exc:
            logger.debug(f"Redis not available for dashboard cache, falling back to memory: {exc}")
            if self.backend == "redis":
                raise
            self._redis_disabled = True
            return None

    def _extract_user_id(self, key: str) -> Optional[str]:
        """Extract user_id from key format 'prefix:user_id'."""
        parts = key.split(":")
        if len(parts) >= 3:
            return parts[2]
        return None

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache (checking Redis first, then in-memory store).
        Returns deserialized data on HIT, None on MISS.
        """
        if not self.enabled:
            return None

        # 1. Try Redis if enabled
        redis_conn = self._get_redis()
        if redis_conn is not None:
            try:
                raw = redis_conn.get(key)
                if raw is not None:
                    with self._lock:
                        self._hits += 1
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    return json.loads(raw)
            except Exception as exc:
                logger.debug(f"Redis get failed for {key}, checking in-memory: {exc}")

        # 2. Check in-memory store
        now = time.time()
        with self._lock:
            entry = self._memory_store.get(key)
            if entry is not None:
                val, expire_at = entry
                if expire_at > now:
                    self._hits += 1
                    return copy.deepcopy(val)
                else:
                    # Expired
                    del self._memory_store[key]

            self._misses += 1
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Store value in cache with specified TTL in seconds.
        Writes to both Redis (if available) and in-memory store.
        """
        if not self.enabled:
            return

        effective_ttl = ttl if ttl is not None else self.default_ttl
        expire_at = time.time() + effective_ttl

        # 1. Write to Redis if available
        redis_conn = self._get_redis()
        if redis_conn is not None:
            try:
                serialized = json.dumps(value)
                redis_conn.setex(key, effective_ttl, serialized)
            except Exception as exc:
                logger.debug(f"Redis set failed for {key}: {exc}")

        # 2. Write to in-memory store
        with self._lock:
            self._memory_store[key] = (copy.deepcopy(value), expire_at)
            user_id = self._extract_user_id(key)
            if user_id:
                if user_id not in self._user_keys:
                    self._user_keys[user_id] = set()
                self._user_keys[user_id].add(key)

    def delete(self, key: str) -> bool:
        """Delete a single key from cache."""
        deleted = False
        redis_conn = self._get_redis()
        if redis_conn is not None:
            try:
                res = redis_conn.delete(key)
                if res:
                    deleted = True
            except Exception as exc:
                logger.debug(f"Redis delete failed for {key}: {exc}")

        with self._lock:
            if key in self._memory_store:
                del self._memory_store[key]
                deleted = True
            user_id = self._extract_user_id(key)
            if user_id and user_id in self._user_keys:
                self._user_keys[user_id].discard(key)

        return deleted

    def invalidate(self, user_id: str) -> int:
        """
        Invalidate all dashboard cache entries for a specific user.
        Returns count of keys invalidated.
        """
        if not user_id:
            return 0

        standard_keys = [
            f"{STATS_PREFIX}:{user_id}",
            f"{ACTIVITY_PREFIX}:{user_id}",
            f"{PRODUCTS_PREFIX}:{user_id}",
        ]

        with self._lock:
            self._invalidations += 1
            tracked_keys = self._user_keys.pop(user_id, set())
            all_keys = set(standard_keys).union(tracked_keys)
            count = 0
            for k in all_keys:
                if k in self._memory_store:
                    del self._memory_store[k]
                    count += 1

        redis_conn = self._get_redis()
        if redis_conn is not None:
            try:
                if all_keys:
                    redis_conn.delete(*all_keys)
            except Exception as exc:
                logger.debug(f"Redis invalidation failed for user {user_id}: {exc}")

        return max(count, len(standard_keys))

    # -----------------------------------------------------------------------
    # Convenience Methods for Dashboard Routes
    # -----------------------------------------------------------------------

    def get_stats_data(self, user_id: str) -> Optional[dict[str, Any]]:
        """Get cached stats card metrics for a user."""
        return self.get(f"{STATS_PREFIX}:{user_id}")

    def set_stats_data(self, user_id: str, data: dict[str, Any], ttl: Optional[int] = None) -> None:
        """Store stats card metrics for a user."""
        self.set(f"{STATS_PREFIX}:{user_id}", data, ttl=ttl or self.get_stats_ttl())

    def get_activity_data(self, user_id: str) -> Optional[list[dict[str, Any]]]:
        """Get cached recent activity feed for a user."""
        return self.get(f"{ACTIVITY_PREFIX}:{user_id}")

    def set_activity_data(self, user_id: str, data: list[dict[str, Any]], ttl: Optional[int] = None) -> None:
        """Store recent activity feed for a user."""
        self.set(f"{ACTIVITY_PREFIX}:{user_id}", data, ttl=ttl or self.get_activity_ttl())

    def get_products_data(self, user_id: str) -> Optional[list[dict[str, Any]]]:
        """Get cached recent products list for a user."""
        return self.get(f"{PRODUCTS_PREFIX}:{user_id}")

    def set_products_data(self, user_id: str, data: list[dict[str, Any]], ttl: Optional[int] = None) -> None:
        """Store recent products list for a user."""
        self.set(f"{PRODUCTS_PREFIX}:{user_id}", data, ttl=ttl or self.get_products_ttl())

    # -----------------------------------------------------------------------
    # Metrics & Maintenance
    # -----------------------------------------------------------------------

    def get_metrics(self) -> dict[str, Any]:
        """Return cache hit rate, counts, and active key metrics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total_requests": total,
                "hit_rate": round(hit_rate, 4),
                "hit_percentage": round(hit_rate * 100, 2),
                "invalidations": self._invalidations,
                "in_memory_keys": len(self._memory_store),
                "backend": self.backend,
                "enabled": self.enabled,
            }

    def reset_metrics(self) -> None:
        """Reset cache hit/miss/invalidation counters (useful between test runs)."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._invalidations = 0

    def clear_all(self) -> None:
        """Purge all cached dashboard data and reset counters."""
        with self._lock:
            self._memory_store.clear()
            self._user_keys.clear()
            self._hits = 0
            self._misses = 0
            self._invalidations = 0

        redis_conn = self._get_redis()
        if redis_conn is not None:
            try:
                # Scan and delete pricehawk dashboard keys
                cursor = 0
                while True:
                    cursor, keys = redis_conn.scan(cursor=cursor, match="dashboard:*", count=100)
                    if keys:
                        redis_conn.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as exc:
                logger.debug(f"Redis clear_all failed: {exc}")


# Global singleton instance
dashboard_cache = DashboardCache()


def get_dashboard_cache() -> DashboardCache:
    """Get the active DashboardCache singleton."""
    return dashboard_cache


def invalidate_dashboard_cache(user_id: str) -> None:
    """Convenience helper to invalidate dashboard cache for a user."""
    if user_id:
        dashboard_cache.invalidate(user_id)


def invalidate_dashboard_cache_for_product(product_id: str, client: Optional[Any] = None) -> None:
    """
    Look up the user owning the given product_id and invalidate their dashboard cache.
    Safely ignores errors and null lookups.
    """
    if not product_id:
        return
    try:
        if client is None:
            from app.db.database import get_supabase_client
            client = get_supabase_client()
        res = client.table("products").select("user_id").eq("id", product_id).execute()
        data = res.data
        user_id = None
        if isinstance(data, list) and len(data) > 0:
            user_id = data[0].get("user_id")
        elif isinstance(data, dict):
            user_id = data.get("user_id")
        if user_id:
            invalidate_dashboard_cache(user_id)
    except Exception as exc:
        logger.debug(f"Failed to invalidate dashboard cache for product {product_id}: {exc}")


def invalidate_dashboard_cache_for_competitor(competitor_id: str, client: Optional[Any] = None) -> None:
    """
    Look up the user owning the given competitor_id and invalidate their dashboard cache.
    Safely ignores errors and null lookups.
    """
    if not competitor_id:
        return
    try:
        if client is None:
            from app.db.database import get_supabase_client
            client = get_supabase_client()
        res = client.table("competitors").select("product_id").eq("id", competitor_id).execute()
        data = res.data
        product_id = None
        if isinstance(data, list) and len(data) > 0:
            product_id = data[0].get("product_id")
        elif isinstance(data, dict):
            product_id = data.get("product_id")
        if product_id:
            invalidate_dashboard_cache_for_product(product_id, client=client)
    except Exception as exc:
        logger.debug(f"Failed to invalidate dashboard cache for competitor {competitor_id}: {exc}")
