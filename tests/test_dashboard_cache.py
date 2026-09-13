"""
Automated test suite for dashboard statistics and activity caching.

Verifies:
1. Cache hit rates and response acceleration across high-frequency dashboard views
2. Configurable TTL expirations and cache bypass controls
3. User isolation (no cross-tenant leakage or invalidation spillover)
4. Immediate cache invalidation on:
   - Product addition / tracking (/api/stores/discover/track)
   - Product updates and soft deletions (/api/products and /api/tracked-products)
   - Price scrape persistence and alert detection (Celery workers and scraper service)
   - Account deletion and currency alerts
5. Robustness under concurrent access and Redis connection failures
"""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services.dashboard_cache import (
    DashboardCache,
    get_dashboard_cache,
    invalidate_dashboard_cache,
    invalidate_dashboard_cache_for_competitor,
    invalidate_dashboard_cache_for_product,
)
from tests.test_pages import create_token


@pytest.fixture(autouse=True)
def clean_dashboard_cache():
    """Ensure dashboard cache is clean before and after each test."""
    cache = get_dashboard_cache()
    cache.clear_all()
    yield
    cache.clear_all()


# ============================================================================
# 1. Core Unit Tests: DashboardCache In-Memory & Metrics
# ============================================================================

def test_cache_set_get_and_hit_rate_metrics():
    """Verify set/get operations, hit/miss counting, and hit rate calculation."""
    cache = DashboardCache(default_ttl=60, backend="memory")

    # Initial state
    metrics = cache.get_metrics()
    assert metrics["hits"] == 0
    assert metrics["misses"] == 0
    assert metrics["hit_rate"] == 0.0

    # First access is a miss
    val = cache.get("dashboard:stats:user-1")
    assert val is None
    metrics = cache.get_metrics()
    assert metrics["hits"] == 0
    assert metrics["misses"] == 1
    assert metrics["hit_rate"] == 0.0

    # Store value
    test_data = {"products": 10, "competitors": 25, "alerts": 2, "insights": 5}
    cache.set("dashboard:stats:user-1", test_data, ttl=60)

    # Subsequent 3 accesses are hits
    for _ in range(3):
        res = cache.get("dashboard:stats:user-1")
        assert res == test_data

    metrics = cache.get_metrics()
    assert metrics["hits"] == 3
    assert metrics["misses"] == 1
    assert metrics["total_requests"] == 4
    # 3 hits out of 4 total = 0.75 (75%)
    assert metrics["hit_rate"] == 0.75
    assert metrics["hit_percentage"] == 75.0


def test_cache_ttl_expiration():
    """Verify items expire after configured TTL."""
    cache = DashboardCache(default_ttl=1, backend="memory")
    cache.set("dashboard:stats:user-exp", {"products": 5}, ttl=0.05)

    # Immediate access hits
    assert cache.get("dashboard:stats:user-exp") == {"products": 5}

    # Wait for TTL expiry
    time.sleep(0.06)

    # Now a miss
    assert cache.get("dashboard:stats:user-exp") is None


def test_cache_user_invalidation_clears_all_user_views():
    """Invalidating a user purges stats, activity, and product feeds for that user."""
    cache = DashboardCache(default_ttl=60, backend="memory")

    # Populate stats, activity, and products for user-alpha
    cache.set_stats_data("user-alpha", {"products": 4})
    cache.set_activity_data("user-alpha", [{"id": "act-1", "type": "price_drop"}])
    cache.set_products_data("user-alpha", [{"id": "p-1", "product_name": "Widget"}])

    # Populate data for user-beta
    cache.set_stats_data("user-beta", {"products": 99})

    # Verify alpha has cached data
    assert cache.get_stats_data("user-alpha") is not None
    assert cache.get_activity_data("user-alpha") is not None
    assert cache.get_products_data("user-alpha") is not None

    # Invalidate user-alpha
    cache.invalidate("user-alpha")

    # Alpha data is cleared
    assert cache.get_stats_data("user-alpha") is None
    assert cache.get_activity_data("user-alpha") is None
    assert cache.get_products_data("user-alpha") is None

    # Beta data remains intact
    assert cache.get_stats_data("user-beta") == {"products": 99}


def test_cache_disabled_toggle():
    """When caching is disabled, get returns None and set is a no-op."""
    cache = DashboardCache(default_ttl=60, enabled=False, backend="memory")

    cache.set("dashboard:stats:user-1", {"products": 10})
    assert cache.get("dashboard:stats:user-1") is None


def test_cache_thread_safety_under_concurrent_load():
    """Verify concurrent reads, writes, and invalidations do not raise exceptions."""
    cache = DashboardCache(default_ttl=60, backend="memory")
    num_threads = 8
    ops_per_thread = 50

    def worker(thread_idx: int):
        user_id = f"user-{thread_idx % 4}"
        for i in range(ops_per_thread):
            cache.set(f"dashboard:stats:{user_id}", {"count": i})
            _ = cache.get(f"dashboard:stats:{user_id}")
            if i % 10 == 0:
                cache.invalidate(user_id)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, idx) for idx in range(num_threads)]
        for f in futures:
            f.result()

    metrics = cache.get_metrics()
    assert metrics["total_requests"] > 0
    assert metrics["invalidations"] > 0


def test_redis_connection_failure_falls_back_to_memory():
    """If Redis connection fails or raises, cache transparently falls back to in-memory."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = ConnectionError("Redis down")
    mock_redis.setex.side_effect = ConnectionError("Redis down")

    cache = DashboardCache(default_ttl=60, redis_client=mock_redis, backend="auto")

    # Setting value should not crash; writes to in-memory fallback
    cache.set("dashboard:stats:user-fallback", {"products": 42}, ttl=60)

    # Getting value falls back to in-memory
    val = cache.get("dashboard:stats:user-fallback")
    assert val == {"products": 42}


def test_worker_invalidation_prevents_web_process_serving_stale_cache():
    """
    REV-01 Regression Test:
    When Redis is the active backend, invalidation by a worker process (separate cache instance)
    must remove the key from Redis, and subsequent reads by the web process (separate cache instance)
    must NOT return any previously cached or process-local value.
    """
    class SharedRedisFake:
        def __init__(self):
            self.store = {}

        def get(self, key):
            return self.store.get(key)

        def setex(self, key, ttl, val):
            self.store[key] = val

        def delete(self, *keys):
            count = 0
            for k in keys:
                if self.store.pop(k, None) is not None:
                    count += 1
            return count

    shared_redis = SharedRedisFake()
    web_cache = DashboardCache(default_ttl=60, redis_client=shared_redis, backend="redis")
    worker_cache = DashboardCache(default_ttl=60, redis_client=shared_redis, backend="redis")

    user_id = "user-proc-coherence"
    key = f"dashboard:stats:{user_id}"
    initial_stats = {"products": 5, "competitors": 10}

    # 1. Web process writes to cache
    web_cache.set(key, initial_stats)
    assert web_cache.get(key) == initial_stats

    # Even if web process had an old in-memory copy lingering
    web_cache._memory_store[key] = ({"products": "stale_local_copy"}, time.time() + 60)

    # 2. Worker process invalidates the user's dashboard cache
    worker_cache.invalidate(user_id)

    # 3. Web process reads the key: Redis misses, must NOT return stale local copy
    res = web_cache.get(key)
    assert res is None, "Web process must not return stale local data after worker invalidation"

    # 4. Web process can write fresh data
    fresh_stats = {"products": 6, "competitors": 12}
    web_cache.set(key, fresh_stats)
    assert web_cache.get(key) == fresh_stats


# ============================================================================
# 2. Integration Tests: Dashboard API Caching & Hit Rate Measurement
# ============================================================================

def test_dashboard_stats_caching_and_hit_rate(client: TestClient):
    """
    Demonstrate cache acceleration on /api/dashboard/stats:
    - 1st request: MISS (queries database)
    - 2nd-10th requests: HIT (zero database queries)
    - Verifies hit rate = 90% (9 hits / 10 total)
    """
    user_id = "user-hit-rate-test"
    token = create_token(sub=user_id)
    cookies = {"access_token": token}

    mock_sb = MagicMock()

    def table_router(tbl_name):
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.gte.return_value = m
        if tbl_name == "products":
            m.execute.return_value = MagicMock(count=12, data=[])
        elif tbl_name == "competitors":
            m.execute.return_value = MagicMock(data=[{"id": "c1"}, {"id": "c2"}])
        elif tbl_name == "pending_alerts":
            m.execute.return_value = MagicMock(count=3, data=[])
        elif tbl_name == "insights":
            m.execute.return_value = MagicMock(count=5, data=[])
        return m

    mock_sb.table.side_effect = table_router

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        # 1st request -> Cache MISS
        resp1 = client.get("/api/dashboard/stats", cookies=cookies)
        assert resp1.status_code == 200
        assert resp1.headers.get("X-Cache") == "MISS"
        assert resp1.json() == {"products": 12, "competitors": 2, "alerts": 3, "insights": 5}

        # Verify DB queries were made on 1st request
        initial_db_calls = mock_sb.table.call_count
        assert initial_db_calls >= 4

        # 2nd to 10th requests -> Cache HITS
        for _ in range(9):
            resp = client.get("/api/dashboard/stats", cookies=cookies)
            assert resp.status_code == 200
            assert resp.headers.get("X-Cache") == "HIT"
            assert resp.json() == {"products": 12, "competitors": 2, "alerts": 3, "insights": 5}

        # Verify ZERO additional database calls were made during the 9 repeated requests
        assert mock_sb.table.call_count == initial_db_calls

    # Verify cache hit rate metrics endpoint
    metrics_resp = client.get("/api/dashboard/cache/metrics", cookies=cookies)
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert metrics["hits"] == 9
    assert metrics["misses"] == 1
    assert metrics["total_requests"] == 10
    assert metrics["hit_rate"] == 0.9
    assert metrics["hit_percentage"] == 90.0


def test_dashboard_activity_caching(client: TestClient):
    """Verify /api/dashboard/activity caches recent activity feed."""
    user_id = "user-activity-cache-test"
    token = create_token(sub=user_id)
    cookies = {"access_token": token}

    activity_row = {
        "id": "alert-1",
        "alert_type": "price_drop",
        "old_price": 50.0,
        "new_price": 40.0,
        "price_change_percent": -20.0,
        "detected_at": "2026-09-13T12:00:00Z",
        "products": {"id": "prod-1", "product_name": "Keyboard"},
        "competitors": {"retailer_name": "bestbuy.com", "url": "https://bestbuy.com/kb"},
    }

    mock_sb = MagicMock()
    mock_sb.table("pending_alerts").select().eq().order().limit().execute.return_value = MagicMock(data=[activity_row])

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        # 1st request -> MISS
        r1 = client.get("/api/dashboard/activity", cookies=cookies)
        assert r1.status_code == 200
        assert r1.headers.get("X-Cache") == "MISS"
        assert len(r1.json()["activity"]) == 1
        assert r1.json()["activity"][0]["product_name"] == "Keyboard"
        assert r1.json()["activity"][0]["retailer"] == "bestbuy.com"

        call_count_before = mock_sb.table.call_count

        # 2nd request -> HIT
        r2 = client.get("/api/dashboard/activity", cookies=cookies)
        assert r2.status_code == 200
        assert r2.headers.get("X-Cache") == "HIT"
        assert r2.json() == r1.json()

        # Database was not touched on HIT
        assert mock_sb.table.call_count == call_count_before


def test_dashboard_products_caching(client: TestClient):
    """Verify /api/dashboard/products caches recent products overview."""
    user_id = "user-products-cache-test"
    token = create_token(sub=user_id)
    cookies = {"access_token": token}

    product_row = {
        "id": "prod-42",
        "product_name": "Monitor",
        "is_active": True,
        "created_at": "2026-09-13T10:00:00Z",
    }

    mock_sb = MagicMock()
    mock_sb.table("products").select().eq().order().limit().execute.return_value = MagicMock(data=[product_row])
    mock_sb.table("competitors").select().eq().execute.return_value = MagicMock(count=3)

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        # 1st request -> MISS
        r1 = client.get("/api/dashboard/products", cookies=cookies)
        assert r1.status_code == 200
        assert r1.headers.get("X-Cache") == "MISS"
        assert r1.json()["products"][0]["product_name"] == "Monitor"
        assert r1.json()["products"][0]["competitor_count"] == 3

        call_count_before = mock_sb.table.call_count

        # 2nd request -> HIT
        r2 = client.get("/api/dashboard/products", cookies=cookies)
        assert r2.status_code == 200
        assert r2.headers.get("X-Cache") == "HIT"
        assert r2.json() == r1.json()

        assert mock_sb.table.call_count == call_count_before


def test_dashboard_response_cache_control_headers_require_server_reach(client: TestClient):
    """
    REV-02 Regression Test:
    Dashboard endpoints (/api/dashboard/stats, /api/dashboard/activity, /api/dashboard/products)
    must send Cache-Control headers that prevent clients/browsers from retaining responses without
    contacting the server, both on HIT, MISS, and when dashboard cache is disabled.
    """
    user_id = "user-cache-control-test"
    token = create_token(sub=user_id)
    cookies = {"access_token": token}

    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(count=1, data=[])
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(count=1, data=[])

    endpoints = [
        "/api/dashboard/stats",
        "/api/dashboard/activity",
        "/api/dashboard/products",
    ]

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        # 1. Enabled mode: verify MISS and HIT have no-cache directives and no max-age
        for endpoint in endpoints:
            # MISS
            res_miss = client.get(endpoint, cookies=cookies)
            assert res_miss.status_code == 200
            assert res_miss.headers.get("X-Cache") == "MISS"
            cc_miss = res_miss.headers.get("Cache-Control", "")
            assert "no-cache" in cc_miss
            assert "max-age" not in cc_miss

            # HIT
            res_hit = client.get(endpoint, cookies=cookies)
            assert res_hit.status_code == 200
            assert res_hit.headers.get("X-Cache") == "HIT"
            cc_hit = res_hit.headers.get("Cache-Control", "")
            assert "no-cache" in cc_hit
            assert "max-age" not in cc_hit

        # 2. Disabled mode: verify endpoints still return no-cache directives and no max-age
        cache = get_dashboard_cache()
        cache.clear_all()
        with patch("app.services.dashboard_cache.DashboardCache.enabled", new_callable=PropertyMock) as mock_enabled:
            mock_enabled.return_value = False
            for endpoint in endpoints:
                res = client.get(endpoint, cookies=cookies)
                assert res.status_code == 200
                assert res.headers.get("X-Cache") == "MISS"
                cc = res.headers.get("Cache-Control", "")
                assert "no-cache" in cc
                assert "max-age" not in cc


# ============================================================================
# 3. Invalidation Tests: Product Add / Edit / Delete
# ============================================================================

def test_cache_invalidation_on_product_tracking(client: TestClient):
    """Adding new products via /api/stores/track immediately invalidates cache."""
    user_id = "user-track-invalidation"
    token = create_token(sub=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Populate stats cache
    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 1, "competitors": 1, "alerts": 0, "insights": 0})
    assert cache.get_stats_data(user_id) is not None

    # 2. Mock tracking route database queries
    mock_prod = MagicMock()
    mock_prod.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "new-prod-id", "product_name": "Headphones"}]
    )

    mock_comp = MagicMock()
    mock_comp.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "new-comp-id", "url": "https://store.com/hp"}]
    )

    mock_ph = MagicMock()
    mock_ph.insert.return_value.execute.return_value = MagicMock(data=[])

    mock_sb = MagicMock()
    mock_sb.table.side_effect = lambda name: (
        mock_prod if name == "products" else (mock_comp if name == "competitors" else mock_ph)
    )

    payload = {
        "group_name": "Headphones",
        "alert_threshold_percent": 5.0,
        "products": [
            {"title": "Headphones", "url": "https://store.com/hp", "price": 99.0, "currency": "USD"}
        ],
    }

    with patch("app.api.routes.discovery.get_supabase_client", return_value=mock_sb):
        resp = client.post("/api/stores/track", json=payload, headers=headers)
        assert resp.status_code == 201

    # 3. Verify dashboard cache was immediately invalidated
    assert cache.get_stats_data(user_id) is None


def test_cache_invalidation_on_product_update(client: TestClient):
    """Modifying a product via PUT /api/products/{id} immediately invalidates dashboard cache."""
    user_id = "user-update-invalidation"
    token = create_token(sub=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    product_id = "prod-update-123"

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 5})
    cache.set_products_data(user_id, [{"id": product_id, "product_name": "Old Name"}])

    mock_sb = MagicMock()
    mock_sb.table("products").select().eq().eq().execute.return_value = MagicMock(
        data=[{"id": product_id}]
    )
    mock_sb.table("products").update().eq().eq().execute.return_value = MagicMock(
        data=[{
            "id": product_id,
            "product_name": "New Name",
            "is_active": True,
            "created_at": "2026-09-13T00:00:00Z",
            "updated_at": "2026-09-13T01:00:00Z",
        }]
    )
    mock_sb.table("competitors").select().eq().execute.return_value = MagicMock(data=[])

    with patch("app.api.routes.products.get_supabase_client", return_value=mock_sb):
        resp = client.put(
            f"/api/products/{product_id}",
            json={"product_name": "New Name"},
            headers=headers,
        )
        assert resp.status_code == 200

    # Cache must be immediately invalidated
    assert cache.get_stats_data(user_id) is None
    assert cache.get_products_data(user_id) is None


def test_cache_invalidation_on_tracked_product_delete(client: TestClient):
    """Soft-deleting a product via DELETE /api/tracked-products/{id} immediately invalidates cache."""
    user_id = "user-delete-invalidation"
    token = create_token(sub=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    product_id = "prod-del-789"

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 3})

    mock_sb = MagicMock()
    mock_sb.table("products").select().eq().eq().execute.return_value = MagicMock(
        data=[{"id": product_id}]
    )
    mock_sb.table("products").update().eq().eq().execute.return_value = MagicMock(data=[{}])

    with patch("app.api.routes.tracked_products.get_supabase_client", return_value=mock_sb):
        resp = client.delete(f"/api/tracked-products/{product_id}", headers=headers)
        assert resp.status_code == 204

    # Cache must be immediately invalidated
    assert cache.get_stats_data(user_id) is None


# ============================================================================
# 4. Invalidation Tests: Price Scrape Updates & Alert Triggers
# ============================================================================

@pytest.mark.asyncio
async def test_cache_invalidation_on_price_scrape_completion():
    """When a competitor URL is scraped and persisted, dashboard cache is invalidated."""
    competitor_id = "comp-scrape-123"
    product_id = "prod-scrape-456"
    user_id = "user-scrape-owner"

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 1, "competitors": 1, "alerts": 0, "insights": 0})
    cache.set_activity_data(user_id, [])

    mock_db = MagicMock()

    def db_table_router(table_name: str):
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.single.return_value = m
        m.insert.return_value = m
        if table_name == "competitors":
            m.execute.return_value = MagicMock(
                data={"id": competitor_id, "url": "https://store.example.com/item", "product_id": product_id}
            )
        elif table_name == "products":
            m.execute.return_value = MagicMock(
                data={"id": product_id, "user_id": user_id}
            )
        elif table_name == "price_history":
            m.execute.return_value = MagicMock(data=[])
        return m

    mock_db.table.side_effect = db_table_router

    from app.services.scraper_service import ScrapeResult, scrape_and_check_alerts

    fake_scrape = ScrapeResult(
        price=Decimal("79.99"),
        currency="USD",
        status="success",
    )

    with patch("app.db.database.get_supabase_client", return_value=mock_db), \
         patch("app.services.scraper_service.scrape_url", AsyncMock(return_value=fake_scrape)), \
         patch("app.services.alert_service.AlertService.check_price_change_and_alert", AsyncMock(return_value=None)):

        result = await scrape_and_check_alerts(competitor_id)

    assert result["scrape_result"]["status"] == "success"
    assert result["scrape_result"]["price"] == 79.99

    # Cache for the product owner must be invalidated
    assert cache.get_stats_data(user_id) is None
    assert cache.get_activity_data(user_id) is None


@pytest.mark.asyncio
async def test_cache_invalidation_on_alert_creation():
    """When price change triggers a new alert, the user's dashboard cache is immediately invalidated."""
    competitor_id = "comp-alert-123"
    user_id = "user-alert-owner"

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"alerts": 0})
    cache.set_activity_data(user_id, [])

    mock_comp = MagicMock()
    mock_comp.select.return_value = mock_comp
    mock_comp.eq.return_value = mock_comp
    mock_comp.single.return_value = mock_comp
    mock_comp.execute.return_value = MagicMock(
        data={
            "id": competitor_id,
            "url": "https://store.com/item",
            "retailer_name": "Store",
            "alert_threshold_percent": 5.0,
            "products": {"id": "prod-1", "product_name": "Watch", "user_id": user_id},
        }
    )

    mock_ph = MagicMock()
    mock_ph.select.return_value = mock_ph
    mock_ph.eq.return_value = mock_ph
    mock_ph.not_ = mock_ph
    mock_ph.is_.return_value = mock_ph
    mock_ph.order.return_value = mock_ph
    mock_ph.limit.return_value = mock_ph
    mock_ph.execute.return_value = MagicMock(
        data=[{"price": 80.0, "currency": "USD"}, {"price": 100.0, "currency": "USD"}]
    )

    mock_alerts = MagicMock()
    mock_alerts.select.return_value = mock_alerts
    mock_alerts.eq.return_value = mock_alerts
    mock_alerts.insert.return_value = mock_alerts
    mock_alerts.execute.return_value = MagicMock(count=0, data=[])

    mock_db = MagicMock()

    def alert_table_router(table_name: str):
        if table_name == "competitors":
            return mock_comp
        elif table_name == "price_history":
            return mock_ph
        elif table_name == "pending_alerts":
            return mock_alerts
        return MagicMock()

    mock_db.table.side_effect = alert_table_router

    from app.services.alert_service import AlertService
    alert_service = AlertService()

    with patch("app.services.alert_service.get_supabase_client", return_value=mock_db):
        res = await alert_service.check_price_change_and_alert(
            competitor_id=competitor_id,
            new_price=Decimal("80.00"),
            currency="USD",
        )

    assert res["alert_created"] is True, f"Result was {res}"
    assert res["alert_type"] == "price_drop"

    # Cache must be invalidated so dashboard immediately displays the new alert
    assert cache.get_stats_data(user_id) is None
    assert cache.get_activity_data(user_id) is None


def test_cache_invalidation_helpers_robust_against_missing_entities():
    """Invalidation helpers handle missing entities or exceptions cleanly without raising."""
    mock_db = MagicMock()
    mock_db.table("products").select().eq().execute.return_value = MagicMock(data=[])
    mock_db.table("competitors").select().eq().execute.return_value = MagicMock(data=[])

    # Should not raise
    invalidate_dashboard_cache_for_product("nonexistent-prod", client=mock_db)
    invalidate_dashboard_cache_for_competitor("nonexistent-comp", client=mock_db)
    invalidate_dashboard_cache("")
    invalidate_dashboard_cache(None)  # type: ignore


# ============================================================================
# 5. Failure-Path Invalidation Tests (REV-03)
# ============================================================================

def test_cache_invalidation_on_product_tracking_failure_after_primary_commit(client: TestClient):
    """
    REV-03 Regression Test:
    When product tracking commits the product group and competitors but subsequent
    price-history insertion fails, the user's dashboard cache must still be invalidated.
    """
    user_id = "user-track-fail-path"
    token = create_token(sub=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 10, "competitors": 20})
    cache.set_products_data(user_id, [{"id": "p1", "product_name": "Old Product"}])
    assert cache.get_stats_data(user_id) is not None

    mock_prod = MagicMock()
    mock_prod.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "new-prod-id", "product_name": "Tracked Item"}]
    )

    mock_comp = MagicMock()
    mock_comp.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "new-comp-id", "url": "https://store.com/item"}]
    )

    # Force price_history insert to raise an exception
    mock_ph = MagicMock()
    mock_ph.insert.return_value.execute.side_effect = RuntimeError("Price history insert failed")

    mock_sb = MagicMock()
    mock_sb.table.side_effect = lambda name: (
        mock_prod if name == "products" else (mock_comp if name == "competitors" else mock_ph)
    )

    payload = {
        "group_name": "Tracked Item",
        "alert_threshold_percent": 5.0,
        "products": [
            {"title": "Tracked Item", "url": "https://store.com/item", "price": 49.99, "currency": "USD"}
        ],
    }

    with patch("app.api.routes.discovery.get_supabase_client", return_value=mock_sb):
        with pytest.raises(RuntimeError, match="Price history insert failed"):
            client.post("/api/stores/track", json=payload, headers=headers)

    # Cache must still be invalidated despite downstream failure
    assert cache.get_stats_data(user_id) is None
    assert cache.get_products_data(user_id) is None


def test_cache_invalidation_on_product_update_failure_after_primary_commit(client: TestClient):
    """
    REV-03 Regression Test:
    When updating a product commits the update but subsequent competitor fetching
    or response building raises an exception, the user's dashboard cache must still be invalidated.
    """
    user_id = "user-update-fail-path"
    token = create_token(sub=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    product_id = "prod-fail-123"

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 5})
    cache.set_products_data(user_id, [{"id": product_id, "product_name": "Initial Name"}])
    assert cache.get_stats_data(user_id) is not None

    mock_sb = MagicMock()
    mock_sb.table("products").update().eq().eq().execute.return_value = MagicMock(
        data=[{
            "id": product_id,
            "product_name": "Updated Name",
            "is_active": True,
            "created_at": "2026-09-13T00:00:00Z",
            "updated_at": "2026-09-13T01:00:00Z",
        }]
    )
    # Force competitor query after commit to fail
    mock_sb.table("competitors").select().eq().execute.side_effect = RuntimeError("Competitors query failed")

    with patch("app.api.routes.products.get_supabase_client", return_value=mock_sb):
        with pytest.raises(RuntimeError, match="Competitors query failed"):
            client.put(
                f"/api/products/{product_id}",
                json={"product_name": "Updated Name"},
                headers=headers,
            )

    # Cache must still be invalidated despite downstream failure
    assert cache.get_stats_data(user_id) is None
    assert cache.get_products_data(user_id) is None


@pytest.mark.asyncio
async def test_cache_invalidation_on_persisted_scrape_when_alert_handling_raises():
    """
    REV-03 Regression Test:
    When a scrape result and price history have successfully committed to the database,
    even if subsequent alert handling raises an unexpected exception, the user's dashboard
    cache must still be invalidated.
    """
    competitor_id = "comp-fail-scrape-123"
    product_id = "prod-fail-scrape-456"
    user_id = "user-fail-scrape-owner"

    cache = get_dashboard_cache()
    cache.set_stats_data(user_id, {"products": 3, "competitors": 7})
    cache.set_activity_data(user_id, [{"id": "act-1"}])
    assert cache.get_stats_data(user_id) is not None

    mock_db = MagicMock()

    def db_table_router(table_name: str):
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.single.return_value = m
        m.insert.return_value = m
        if table_name == "competitors":
            m.execute.return_value = MagicMock(
                data={"id": competitor_id, "url": "https://store.example.com/item", "product_id": product_id}
            )
        elif table_name == "products":
            m.execute.return_value = MagicMock(
                data={"id": product_id, "user_id": user_id}
            )
        elif table_name == "price_history":
            m.execute.return_value = MagicMock(data=[])
        return m

    mock_db.table.side_effect = db_table_router

    from app.services.scraper_service import ScrapeResult, scrape_and_check_alerts

    fake_scrape = ScrapeResult(
        price=Decimal("45.00"),
        currency="USD",
        status="success",
    )

    # Simulate AlertService.check_price_change_and_alert raising an exception
    with patch("app.db.database.get_supabase_client", return_value=mock_db), \
         patch("app.services.scraper_service.scrape_url", AsyncMock(return_value=fake_scrape)), \
         patch("app.services.alert_service.AlertService.check_price_change_and_alert", AsyncMock(side_effect=RuntimeError("Alert processing failed"))):

        result = await scrape_and_check_alerts(competitor_id)

    # Scrape result status is success and price was persisted
    assert result["scrape_result"]["status"] == "success"
    # Cache must still be invalidated
    assert cache.get_stats_data(user_id) is None
    assert cache.get_activity_data(user_id) is None
