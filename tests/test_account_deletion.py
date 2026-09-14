"""
Integration and regression tests for user account deletion cleanup,
cascading guarantees, background task cancellation, and session invalidation.
"""

from decimal import Decimal
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import is_session_revoked, revoke_user_sessions
from app.services.account_service import (
    cancel_tasks_for_user,
    delete_user_account,
    is_product_deleted,
    is_user_deleted,
    mark_product_deleted,
    mark_user_deleted,
    register_active_scrape_task,
    unmark_product_deleted,
    unregister_active_scrape_task,
)
from app.tasks.scraper_tasks import scrape_all_products, scrape_product_manual, scrape_single_competitor
from main import app


def _create_token(user_id: str = "user-del-test-123", email: str = "deltest@example.com") -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, settings.sb_jwt_secret, algorithm="HS256")


class MockTableQuery:
    """Mock for chained Supabase table queries."""

    def __init__(self, table_name: str, parent_db: "MockSupabaseDB"):
        self.table_name = table_name
        self.parent_db = parent_db
        self._action = "select"
        self._filters: dict = {}
        self._in_filters: dict = {}
        self._update_data = None

    def select(self, *args, **kwargs):
        self._action = "select"
        return self

    def insert(self, data, *args, **kwargs):
        self._action = "insert"
        self._insert_data = data
        return self

    def update(self, data, *args, **kwargs):
        self._action = "update"
        self._update_data = data
        return self

    def delete(self, *args, **kwargs):
        self._action = "delete"
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def in_(self, column, values):
        self._in_filters[column] = list(values)
        return self

    def gte(self, column, value):
        return self

    def lte(self, column, value):
        return self

    def gt(self, column, value):
        return self

    def lt(self, column, value):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return self.parent_db._execute_table(self)


class MockSupabaseDB:
    """Simulated in-memory Supabase storage with table-level tracking."""

    def __init__(self):
        self.tables = {
            "products": [],
            "competitors": [],
            "price_history": [],
            "insights": [],
            "tracking_jobs": [],
            "pending_alerts": [],
            "user_alert_settings": [],
            "alert_history": [],
        }
        self.auth_users = {}
        self.deleted_counts = {k: 0 for k in self.tables}
        self.deleted_counts["auth.users"] = 0
        self.auth_admin_deleted = []

        # Setup mock auth admin
        self.auth = MagicMock()
        self.auth.admin = MagicMock()
        self.auth.admin.delete_user.side_effect = self._admin_delete_user

    def _admin_delete_user(self, user_id: str):
        self.auth_admin_deleted.append(user_id)
        self.deleted_counts["auth.users"] += 1
        if user_id in self.auth_users:
            del self.auth_users[user_id]
        return {"message": "User deleted"}

    def table(self, table_name: str) -> MockTableQuery:
        return MockTableQuery(table_name, self)

    def _execute_table(self, query: MockTableQuery):
        table_rows = self.tables.get(query.table_name, [])

        if query._action == "select":
            filtered = []
            for row in table_rows:
                match = True
                for k, v in query._filters.items():
                    if row.get(k) != v:
                        match = False
                        break
                for k, vals in query._in_filters.items():
                    if row.get(k) not in vals:
                        match = False
                        break
                if match:
                    filtered.append(row)
            return MagicMock(data=filtered, count=len(filtered))

        elif query._action == "delete":
            remaining = []
            deleted_count = 0
            for row in table_rows:
                should_delete = True
                for k, v in query._filters.items():
                    if row.get(k) != v:
                        should_delete = False
                        break
                for k, vals in query._in_filters.items():
                    if row.get(k) not in vals:
                        should_delete = False
                        break
                if should_delete:
                    deleted_count += 1
                else:
                    remaining.append(row)

            self.tables[query.table_name] = remaining
            self.deleted_counts[query.table_name] += deleted_count
            return MagicMock(data=[{"id": "del"}] * deleted_count, count=deleted_count)

        elif query._action == "update":
            return MagicMock(data=[], count=0)

        return MagicMock(data=[], count=0)


# ============================================================================
# 1. Comprehensive Storage Cleanup Integration Tests
# ============================================================================

def test_account_deletion_end_to_end_cleans_all_storage_tables(client: TestClient):
    """
    Integration test: DELETE /api/account/delete thoroughly purges all records
    across products, competitors, price history, insights, tracking jobs,
    alerts, alert history, delivery credentials (webhook settings), and auth user.
    """
    user_id = "user-delete-all-999"
    email = "deleteall@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()

    # Seed all tables for this user
    prod1 = "prod-del-1"
    prod2 = "prod-del-2"
    comp1 = "comp-del-1"
    comp2 = "comp-del-2"

    db.tables["products"] = [
        {"id": prod1, "user_id": user_id, "product_name": "Product 1", "is_active": True},
        {"id": prod2, "user_id": user_id, "product_name": "Product 2", "is_active": True},
        {"id": "prod-other", "user_id": "other-user", "product_name": "Other", "is_active": True},
    ]
    db.tables["competitors"] = [
        {"id": comp1, "product_id": prod1, "url": "https://store.com/item1"},
        {"id": comp2, "product_id": prod2, "url": "https://store.com/item2"},
        {"id": "comp-other", "product_id": "prod-other", "url": "https://store.com/other"},
    ]
    db.tables["price_history"] = [
        {"id": "ph-1", "competitor_id": comp1, "price": Decimal("29.99"), "scrape_status": "success"},
        {"id": "ph-2", "competitor_id": comp2, "price": Decimal("49.99"), "scrape_status": "success"},
        {"id": "ph-other", "competitor_id": "comp-other", "price": Decimal("99.99"), "scrape_status": "success"},
    ]
    db.tables["insights"] = [
        {"id": "ins-1", "product_id": prod1, "insight_text": "Price dropped"},
        {"id": "ins-other", "product_id": "prod-other", "insight_text": "Normal trend"},
    ]
    db.tables["tracking_jobs"] = [
        {"id": "job-1", "user_id": user_id, "status": "processing", "total_items": 5},
        {"id": "job-other", "user_id": "other-user", "status": "completed", "total_items": 2},
    ]
    db.tables["pending_alerts"] = [
        {"id": "alert-1", "user_id": user_id, "product_id": prod1, "competitor_id": comp1},
        {"id": "alert-other", "user_id": "other-user", "product_id": "prod-other", "competitor_id": "comp-other"},
    ]
    db.tables["user_alert_settings"] = [
        {
            "id": "settings-1",
            "user_id": user_id,
            "webhook_enabled": True,
            "webhook_url": "https://mywebhook.com/alerts",
            "webhook_secret": "super-secret-key-123456",
            "email_enabled": True,
        },
        {
            "id": "settings-other",
            "user_id": "other-user",
            "webhook_enabled": True,
            "webhook_url": "https://other.com/hook",
            "webhook_secret": "other-secret-key-123456",
        },
    ]
    db.tables["alert_history"] = [
        {"id": "hist-1", "user_id": user_id, "alerts_count": 3, "email_status": "sent"},
        {"id": "hist-other", "user_id": "other-user", "alerts_count": 1, "email_status": "sent"},
    ]
    db.auth_users[user_id] = {"id": user_id, "email": email}

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        response = client.delete(
            "/api/account/delete",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "Account data deleted successfully" in data["message"]

        # Cookie invalidation guarantee: response includes Set-Cookie clearing access_token
        set_cookie = response.headers.get("set-cookie", "")
        assert "access_token" in set_cookie
        assert 'max-age=0' in set_cookie.lower() or 'expires=' in set_cookie.lower()

        # Storage cleanup guarantees: verify user's records were completely removed
        assert len(db.tables["products"]) == 1
        assert db.tables["products"][0]["user_id"] == "other-user"

        assert len(db.tables["competitors"]) == 1
        assert db.tables["competitors"][0]["id"] == "comp-other"

        assert len(db.tables["price_history"]) == 1
        assert db.tables["price_history"][0]["id"] == "ph-other"

        assert len(db.tables["insights"]) == 1
        assert db.tables["insights"][0]["id"] == "ins-other"

        assert len(db.tables["tracking_jobs"]) == 1
        assert db.tables["tracking_jobs"][0]["user_id"] == "other-user"

        assert len(db.tables["pending_alerts"]) == 1
        assert db.tables["pending_alerts"][0]["user_id"] == "other-user"

        # Notification credentials (webhook_url, webhook_secret) purged
        assert len(db.tables["user_alert_settings"]) == 1
        assert db.tables["user_alert_settings"][0]["user_id"] == "other-user"

        assert len(db.tables["alert_history"]) == 1
        assert db.tables["alert_history"][0]["user_id"] == "other-user"

        # Auth admin user deletion invoked
        assert user_id in db.auth_admin_deleted


# ============================================================================
# 2. Session and Cookie Invalidation Guarantees
# ============================================================================

def test_session_and_token_invalidated_immediately_after_deletion(client: TestClient):
    """
    Guarantee: After account deletion, the issued Bearer token and any ambient cookie
    are immediately rejected on subsequent requests with HTTP 401 Unauthorized.
    """
    user_id = "user-session-inval-456"
    email = "sessioninval@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        # Pre-deletion: user can access /api/auth/me
        pre_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert pre_resp.status_code == 200
        assert pre_resp.json()["id"] == user_id

        # Execute deletion
        del_resp = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert del_resp.status_code == 200

        # Post-deletion 1: Immediate call to /api/auth/me with Bearer token is rejected with 401
        post_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert post_resp.status_code == 401
        assert "Session invalidated or account deleted" in post_resp.json()["detail"]

        # Post-deletion 2: Account settings endpoint also rejects the revoked session
        settings_resp = client.get("/api/account/settings", headers={"Authorization": f"Bearer {token}"})
        assert settings_resp.status_code == 401
        assert "Session invalidated or account deleted" in settings_resp.json()["detail"]

        # Post-deletion 3: Cookie-based request to protected dashboard redirects to login
        page_resp = client.get("/dashboard", cookies={"access_token": token}, follow_redirects=False)
        assert page_resp.status_code == 303
        assert "/login" in page_resp.headers["location"]


# ============================================================================
# 3. Background Task Cancellation & Orphaned Scraper Prevention
# ============================================================================

def test_scrape_product_manual_aborts_immediately_if_account_deleted():
    """
    Guarantee: A manual scrape task does not scrape any competitor URLs if the
    product or account is marked deleted.
    """
    user_id = "user-task-abort-1"
    product_id = "prod-task-abort-1"

    # Mark user deleted
    mark_user_deleted(user_id)
    mark_product_deleted(product_id)

    mock_scrape = AsyncMock()

    with patch("app.tasks.scraper_tasks.scrape_url", mock_scrape), \
         patch("app.tasks.scraper_tasks.set_scrape_progress") as mock_progress:

        result = scrape_product_manual(product_id)

        assert result["status"] == "cancelled"
        # Guarantee: No external scrape requests were made
        mock_scrape.assert_not_called()
        mock_progress.assert_called()


def test_scrape_product_manual_cancels_mid_run_when_deletion_occurs():
    """
    Guarantee: If an account is deleted while a multi-competitor scrape is running,
    the loop halts immediately and does not process remaining competitors.
    """
    user_id = "user-midrun-cancel"
    product_id = "prod-midrun-cancel"

    competitors = [
        {"id": "comp-1", "url": "https://store.com/1", "retailer_name": "Store 1"},
        {"id": "comp-2", "url": "https://store.com/2", "retailer_name": "Store 2"},
        {"id": "comp-3", "url": "https://store.com/3", "retailer_name": "Store 3"},
    ]

    mock_db = MagicMock()
    # Mock product select
    mock_prod_q = MagicMock()
    mock_prod_q.select.return_value = mock_prod_q
    mock_prod_q.eq.return_value = mock_prod_q
    mock_prod_q.execute.return_value = MagicMock(data=[{"id": product_id, "user_id": user_id, "is_active": True}])

    # Mock competitors select
    mock_comp_q = MagicMock()
    mock_comp_q.select.return_value = mock_comp_q
    mock_comp_q.eq.return_value = mock_comp_q
    mock_comp_q.execute.return_value = MagicMock(data=competitors)

    # Mock price history insert
    mock_ph_q = MagicMock()
    mock_ph_q.insert.return_value = mock_ph_q
    mock_ph_q.execute.return_value = MagicMock()

    def mock_table(name):
        if name == "products":
            return mock_prod_q
        elif name == "competitors":
            return mock_comp_q
        return mock_ph_q

    mock_db.table.side_effect = mock_table

    scraped_urls = []
    async def mock_scrape(url):
        scraped_urls.append(url)
        # Simulate user deleting account after the first competitor is scraped
        mark_user_deleted(user_id)
        return MagicMock(price=Decimal("19.99"), currency="USD", status="success", error_message=None, failure_reason=None, retry_count=0)

    with patch("app.tasks.scraper_tasks.get_supabase_client", return_value=mock_db), \
         patch("app.tasks.scraper_tasks.scrape_url", side_effect=mock_scrape), \
         patch("app.tasks.scraper_tasks.set_scrape_progress"):

        result = scrape_product_manual(product_id)

        # Scraped only the first one, cancelled before scraping comp-2 and comp-3
        assert len(scraped_urls) == 1
        assert result["status"] == "cancelled"
        assert "cancelled" in result.get("error", "").lower() or len(result["results"]) == 1


def test_scrape_single_competitor_cancels_if_product_or_user_deleted():
    """
    Guarantee: Celery scrape_single_competitor task skips scraping and returns
    cancelled status if the competitor's parent product or user was deleted.
    """
    user_id = "user-single-cancel"
    product_id = "prod-single-cancel"
    competitor_id = "comp-single-cancel"

    # Mark user deleted
    mark_user_deleted(user_id)

    mock_db = MagicMock()
    mock_comp_q = MagicMock()
    mock_comp_q.select.return_value = mock_comp_q
    mock_comp_q.eq.return_value = mock_comp_q
    mock_comp_q.execute.return_value = MagicMock(
        data=[{
            "id": competitor_id,
            "product_id": product_id,
            "products": {"id": product_id, "user_id": user_id, "is_active": True}
        }]
    )
    mock_db.table.return_value = mock_comp_q

    mock_scrape = AsyncMock()

    with patch("app.tasks.scraper_tasks.get_supabase_client", return_value=mock_db), \
         patch("app.services.scraper_service.scrape_url", mock_scrape):

        result = scrape_single_competitor(competitor_id)

        assert result["status"] == "cancelled"
        assert result["reason"] == "user_deleted"
        # Guarantee: No external scrape call made
        mock_scrape.assert_not_called()


def test_cancel_tasks_for_user_revokes_registered_celery_tasks():
    """
    Guarantee: cancel_tasks_for_user retrieves registered task IDs, revokes them via Celery control,
    and updates progress keys in Redis.
    """
    user_id = "user-celery-revoke-123"
    prod_id = "prod-celery-revoke-456"
    task_id = "celery-task-id-789"

    register_active_scrape_task(user_id=user_id, product_id=prod_id, task_id=task_id)

    with patch("app.tasks.celery_app.celery_app.control.revoke") as mock_revoke, \
         patch("app.tasks.scraper_tasks.set_scrape_progress") as mock_set_progress:

        revoked = cancel_tasks_for_user(user_id=user_id, product_ids=[prod_id])

        assert task_id in revoked
        mock_revoke.assert_called_with(task_id, terminate=True)
        mock_set_progress.assert_called_with(
            task_id,
            {
                "status": "cancelled",
                "completed": 0,
                "total": 0,
                "results": [],
                "error": "Account or product deleted",
            }
        )


# ============================================================================
# 4. Cache Invalidation and Error Resilience Tests
# ============================================================================

def test_dashboard_cache_invalidated_on_account_deletion():
    """Guarantee: Deleting an account invalidates all cached dashboard views."""
    user_id = "user-cache-inval-777"
    db = MockSupabaseDB()

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db), \
         patch("app.services.account_service.invalidate_dashboard_cache") as mock_cache_inval:

        summary = delete_user_account(user_id, client=db)

        assert summary["user_id"] == user_id
        mock_cache_inval.assert_called_with(user_id)


def test_account_deletion_with_empty_account_succeeds_cleanly():
    """Guarantee: Deleting an account with no products or data succeeds without errors."""
    user_id = "user-empty-data-000"
    db = MockSupabaseDB()

    summary = delete_user_account(user_id, client=db)

    assert summary["user_id"] == user_id
    assert summary["products_found"] == 0
    assert summary["competitors_found"] == 0
    assert is_user_deleted(user_id)
    assert is_session_revoked(user_id)


def test_account_deletion_unauthenticated_rejected(client: TestClient):
    """Guarantee: Unauthenticated requests to DELETE /api/account/delete are rejected."""
    response = client.delete("/api/account/delete")
    assert response.status_code in (401, 403)


def test_account_deletion_database_error_raises_http_400(client: TestClient):
    """Guarantee: When a database error occurs during deletion, an HTTP 400 with friendly message is returned."""
    user_id = "user-db-err-1"
    token = _create_token(user_id=user_id, email="dberr@example.com")

    with patch("app.services.account_service.delete_user_account", side_effect=RuntimeError("Database failure")):
        response = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
        assert "Unable to delete account" in response.json()["detail"]


@pytest.mark.asyncio
async def test_scrape_and_check_alerts_skips_external_scrape_if_product_or_user_deleted():
    """
    Guarantee: scrape_and_check_alerts aborts before initiating external scrape
    when the competitor's parent product or user was deleted.
    """
    from app.services.scraper_service import scrape_and_check_alerts

    competitor_id = "comp-alert-check-1"
    product_id = "prod-alert-check-1"
    user_id = "user-alert-check-1"

    mock_db = MagicMock()
    mock_comp_q = MagicMock()
    mock_comp_q.select.return_value = mock_comp_q
    mock_comp_q.eq.return_value = mock_comp_q
    mock_comp_q.single.return_value = mock_comp_q
    mock_comp_q.execute.return_value = MagicMock(
        data={
            "id": competitor_id,
            "url": "https://competitor.com/item",
            "product_id": product_id,
            "products": {"id": product_id, "user_id": user_id, "is_active": True},
        }
    )
    mock_db.table.return_value = mock_comp_q

    # Mark user deleted
    mark_user_deleted(user_id)

    mock_scrape = AsyncMock()

    with patch("app.db.database.get_supabase_client", return_value=mock_db), \
         patch("app.services.scraper_service.scrape_url", mock_scrape):

        result = await scrape_and_check_alerts(competitor_id)

        assert result["scrape_result"]["status"] == "cancelled"
        assert result["scrape_result"]["error"] == "Account deleted"
        assert result["alert_result"] is None
        # External scrape must NOT be invoked
        mock_scrape.assert_not_called()


def test_account_deletion_preserves_other_users_data(client: TestClient):
    """
    Multi-tenant isolation: Deleting User A must leave User B's products,
    competitors, price history, pending alerts, and credentials intact.
    """
    user_a = "user-tenant-a"
    user_b = "user-tenant-b"
    token_a = _create_token(user_id=user_a, email="user_a@example.com")

    db = MockSupabaseDB()

    # User A data
    db.tables["products"] = [
        {"id": "prod-a", "user_id": user_a, "product_name": "Product A"},
        {"id": "prod-b", "user_id": user_b, "product_name": "Product B"},
    ]
    db.tables["competitors"] = [
        {"id": "comp-a", "product_id": "prod-a", "url": "https://a.com"},
        {"id": "comp-b", "product_id": "prod-b", "url": "https://b.com"},
    ]
    db.tables["user_alert_settings"] = [
        {"id": "s-a", "user_id": user_a, "webhook_url": "https://a.com/hook", "webhook_secret": "secret-a-12345678"},
        {"id": "s-b", "user_id": user_b, "webhook_url": "https://b.com/hook", "webhook_secret": "secret-b-12345678"},
    ]
    db.tables["pending_alerts"] = [
        {"id": "alert-a", "user_id": user_a, "product_id": "prod-a", "competitor_id": "comp-a"},
        {"id": "alert-b", "user_id": user_b, "product_id": "prod-b", "competitor_id": "comp-b"},
    ]

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        response = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token_a}"})
        assert response.status_code == 200

        # User B's entities must all remain unchanged
        assert len(db.tables["products"]) == 1
        assert db.tables["products"][0]["id"] == "prod-b"
        assert db.tables["products"][0]["user_id"] == user_b

        assert len(db.tables["competitors"]) == 1
        assert db.tables["competitors"][0]["id"] == "comp-b"

        assert len(db.tables["user_alert_settings"]) == 1
        assert db.tables["user_alert_settings"][0]["user_id"] == user_b
        assert db.tables["user_alert_settings"][0]["webhook_secret"] == "secret-b-12345678"

        assert len(db.tables["pending_alerts"]) == 1
        assert db.tables["pending_alerts"][0]["user_id"] == user_b


# ============================================================================
# 5. Auth Identity Deletion & Retryability Tests (REV-01)
# ============================================================================

def test_account_deletion_fails_when_auth_admin_unavailable(client: TestClient):
    """
    REV-01: When Supabase Auth admin deletion API is unavailable,
    the endpoint must return HTTP 400 (never 200) and preserve safe retryability.
    """
    user_id = "user-auth-unavail-1"
    email = "authunavail@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    db.auth.admin = None  # Admin auth unavailable

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        response = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
        assert "Unable to delete account" in response.json()["detail"]

        # Guarantee: user session was NOT permanently revoked on failure, keeping retry open
        assert not is_user_deleted(user_id)
        assert not is_session_revoked(user_id)


def test_account_deletion_fails_when_auth_admin_raises_error(client: TestClient):
    """
    REV-01: When auth.admin.delete_user raises an error, the endpoint must
    return HTTP 400 (never 200) and allow the user to safely retry.
    """
    user_id = "user-auth-err-1"
    email = "autherr@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    db.auth.admin.delete_user.side_effect = RuntimeError("Supabase Auth API connection failure")

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        response = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
        assert "Unable to delete account" in response.json()["detail"]

        # Identity was not deleted in auth.users
        assert user_id not in db.auth_admin_deleted
        # User is not permanently locked out
        assert not is_user_deleted(user_id)
        assert not is_session_revoked(user_id)


def test_account_deletion_fails_when_auth_admin_unconfirmed(client: TestClient):
    """
    REV-01: When auth.admin.delete_user does not confirm deletion (e.g. returns error or False),
    the endpoint must return HTTP 400 and preserve retryability.
    """
    user_id = "user-auth-unconf-1"
    email = "authunconf@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    db.auth.admin.delete_user.side_effect = None
    db.auth.admin.delete_user.return_value = {"error": "User deletion unconfirmed", "success": False}

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        response = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
        assert "Unable to delete account" in response.json()["detail"]

        assert not is_user_deleted(user_id)
        assert not is_session_revoked(user_id)


def test_account_deletion_auth_failure_allows_safe_retry(client: TestClient):
    """
    REV-01: Proves that an initial Auth deletion failure can be safely retried
    with the same credentials once the service recovers, resulting in a successful 200 response.
    """
    user_id = "user-auth-retry-1"
    email = "authretry@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    # First attempt: Auth admin fails
    db.auth.admin.delete_user.side_effect = RuntimeError("Transient Auth API timeout")

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        # First attempt fails with 400
        resp1 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp1.status_code == 400

        # Token is still valid (not rejected with 401 Unauthorized)
        # Service recovers:
        db.auth.admin.delete_user.side_effect = db._admin_delete_user

        # Second attempt succeeds with 200
        resp2 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200
        assert "Account data deleted successfully" in resp2.json()["message"]
        assert user_id in db.auth_admin_deleted
        assert is_user_deleted(user_id)
        assert is_session_revoked(user_id)


# ============================================================================
# 6. Database Deletion Failure Recovery & Safe Retry Tests (REV-02)
# ============================================================================

def test_cancel_tasks_does_not_prematurely_revoke_user_sessions():
    """
    REV-02: cancel_tasks_for_user halts background scraping tasks by setting
    deletion-in-progress, but does NOT invalidate active authentication tokens.
    """
    user_id = "user-no-pre-revoke"
    prod_id = "prod-no-pre-revoke"

    cancel_tasks_for_user(user_id=user_id, product_ids=[prod_id])

    # Background workers see the user and product as halting/deleted
    assert is_user_deleted(user_id)
    assert is_product_deleted(prod_id)

    # BUT authentication tokens are NOT revoked prematurely
    assert not is_session_revoked(user_id)


def test_deletion_fails_on_product_discovery_error_and_does_not_strand_data(client: TestClient):
    """
    REV-02: When product discovery fails due to a database error, the deletion must
    abort with HTTP 400, not return success, not strand data, and allow safe retry.
    """
    user_id = "user-disc-err-1"
    email = "discerr@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    prod_id = "prod-disc-1"
    comp_id = "comp-disc-1"
    db.tables["products"] = [{"id": prod_id, "user_id": user_id, "product_name": "P1"}]
    db.tables["competitors"] = [{"id": comp_id, "product_id": prod_id, "url": "https://p1.com"}]
    db.tables["price_history"] = [{"id": "ph-disc-1", "competitor_id": comp_id, "price": Decimal("10")}]
    db.tables["user_alert_settings"] = [{"id": "s-disc-1", "user_id": user_id}]

    # Simulate database error on product discovery
    orig_table = db.table
    def failing_table(table_name: str):
        if table_name == "products":
            mock_q = MockTableQuery(table_name, db)
            def failing_execute():
                if mock_q._action == "select":
                    raise RuntimeError("Transient PostgreSQL connection failure on products select")
                return db._execute_table(mock_q)
            mock_q.execute = failing_execute
            return mock_q
        return orig_table(table_name)

    db.table = failing_table

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        # First attempt: failure on discovery
        resp1 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp1.status_code == 400
        assert "Unable to delete account" in resp1.json()["detail"]

        # Data is preserved and not stranded or partially cleaned
        assert len(db.tables["products"]) == 1
        assert len(db.tables["competitors"]) == 1
        assert len(db.tables["price_history"]) == 1
        assert len(db.tables["user_alert_settings"]) == 1

        # User is not locked out
        assert not is_session_revoked(user_id)
        assert not is_user_deleted(user_id)

        # Database recovers
        db.table = orig_table

        # Retry succeeds completely
        resp2 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200
        assert len(db.tables["products"]) == 0
        assert len(db.tables["competitors"]) == 0
        assert len(db.tables["price_history"]) == 0
        assert len(db.tables["user_alert_settings"]) == 0
        assert is_user_deleted(user_id)
        assert is_session_revoked(user_id)


def test_deletion_fails_on_intermediate_table_cleanup_and_allows_safe_retry(client: TestClient):
    """
    REV-02: When a database error occurs during child table cleanup (e.g. competitors),
    the endpoint returns HTTP 400, leaves the user token valid, and allows a subsequent
    retry to complete remaining table deletions cleanly without stranding data.
    """
    user_id = "user-inter-err-1"
    email = "intererr@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    prod_id = "prod-inter-1"
    comp_id = "comp-inter-1"
    db.tables["products"] = [{"id": prod_id, "user_id": user_id, "product_name": "P2"}]
    db.tables["competitors"] = [{"id": comp_id, "product_id": prod_id, "url": "https://p2.com"}]
    db.tables["price_history"] = [{"id": "ph-inter-1", "competitor_id": comp_id, "price": Decimal("20")}]
    db.tables["insights"] = [{"id": "ins-inter-1", "product_id": prod_id, "insight_text": "Good"}]

    # Fail on delete from competitors table
    orig_table = db.table
    def failing_comp_table(table_name: str):
        if table_name == "competitors":
            mock_q = MockTableQuery(table_name, db)
            def failing_execute():
                if mock_q._action == "delete":
                    raise RuntimeError("Competitors delete failed due to deadlock")
                return db._execute_table(mock_q)
            mock_q.execute = failing_execute
            return mock_q
        return orig_table(table_name)

    db.table = failing_comp_table

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        resp1 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp1.status_code == 400

        # User is NOT locked out and product is not marked deleted
        assert not is_session_revoked(user_id)
        assert not is_user_deleted(user_id)
        assert not is_product_deleted(prod_id)

        # Database recovers
        db.table = orig_table

        # Retry succeeds
        resp2 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200
        assert len(db.tables["products"]) == 0
        assert len(db.tables["competitors"]) == 0
        assert len(db.tables["price_history"]) == 0
        assert len(db.tables["insights"]) == 0
        assert is_user_deleted(user_id)
        assert is_session_revoked(user_id)
        assert is_product_deleted(prod_id)


def test_deletion_fails_on_user_table_cleanup_and_allows_safe_retry(client: TestClient):
    """
    REV-02: When a database error occurs during direct user table cleanup (e.g. user_alert_settings),
    the endpoint returns HTTP 400 and preserves safe retryability.
    """
    user_id = "user-direct-err-1"
    email = "directerr@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    prod_id = "prod-direct-1"
    db.tables["products"] = [{"id": prod_id, "user_id": user_id, "product_name": "P3"}]
    db.tables["user_alert_settings"] = [{"id": "s-direct-1", "user_id": user_id, "webhook_url": "https://h.com"}]

    orig_table = db.table
    def failing_settings_table(table_name: str):
        if table_name == "user_alert_settings":
            mock_q = MockTableQuery(table_name, db)
            def failing_execute():
                if mock_q._action == "delete":
                    raise RuntimeError("Settings table lock timeout")
                return db._execute_table(mock_q)
            mock_q.execute = failing_execute
            return mock_q
        return orig_table(table_name)

    db.table = failing_settings_table

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db):

        resp1 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp1.status_code == 400

        # User is NOT locked out and product is not marked deleted
        assert not is_session_revoked(user_id)
        assert not is_user_deleted(user_id)
        assert not is_product_deleted(prod_id)

        # Database recovers
        db.table = orig_table

        # Retry succeeds
        resp2 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200
        assert len(db.tables["user_alert_settings"]) == 0
        assert len(db.tables["products"]) == 0
        assert is_user_deleted(user_id)
        assert is_session_revoked(user_id)
        assert is_product_deleted(prod_id)


def test_intermediate_cleanup_failure_restores_surviving_product_markers_and_allows_scrape_and_retry(client: TestClient):
    """
    REV-04: When account deletion fails during intermediate cleanup (e.g. competitors table delete):
    - Deletion markers are restored for surviving products belonging to the user.
    - Both in-memory and Redis markers are cleared for the surviving products.
    - Markers for products that were independently deleted outside this operation are NOT cleared.
    - Surviving products are no longer considered deleted and can be scraped again before retry.
    - A subsequent account-deletion retry completes successfully and deletes all products.
    """
    user_id = "user-rev04-1"
    email = "rev04@example.com"
    token = _create_token(user_id=user_id, email=email)

    db = MockSupabaseDB()
    surviving_prod_id = "prod-rev04-surv"
    indep_prod_id = "prod-rev04-indep"
    comp_id = "comp-rev04-1"

    # User owns two products: one surviving active product, and one independently deleted
    db.tables["products"] = [
        {"id": surviving_prod_id, "user_id": user_id, "product_name": "Surviving Product", "is_active": True},
        {"id": indep_prod_id, "user_id": user_id, "product_name": "Independently Deleted Product", "is_active": False},
    ]
    db.tables["competitors"] = [
        {"id": comp_id, "product_id": surviving_prod_id, "url": "https://competitor.com/item", "retailer_name": "Store"}
    ]

    # Pre-mark indep_prod_id as deleted outside this account-deletion operation
    mark_product_deleted(indep_prod_id)
    assert is_product_deleted(indep_prod_id)
    assert not is_product_deleted(surviving_prod_id)

    # Setup a mock Redis client to track key operations across account_service
    mock_redis_storage = {
        f"deleted_product:{indep_prod_id}": "1"
    }

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: mock_redis_storage.get(k)
    def mock_setex(k, ttl, v):
        mock_redis_storage[k] = v
    mock_redis.setex.side_effect = mock_setex
    def mock_delete(*keys):
        for k in keys:
            mock_redis_storage.pop(k, None)
    mock_redis.delete.side_effect = mock_delete
    mock_redis.smembers.return_value = set()

    # Intermediate cleanup failure on competitors table
    orig_table = db.table
    def failing_comp_table(table_name: str):
        if table_name == "competitors":
            mock_q = MockTableQuery(table_name, db)
            def failing_execute():
                if mock_q._action == "delete":
                    raise RuntimeError("Competitors deletion failed midway")
                return db._execute_table(mock_q)
            mock_q.execute = failing_execute
            return mock_q
        return orig_table(table_name)

    db.table = failing_comp_table

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db), \
         patch("app.tasks.scraper_tasks.get_supabase_client", return_value=db), \
         patch("app.services.account_service._get_redis_conn", return_value=mock_redis):

        # 1. First account deletion attempt fails during intermediate table cleanup
        resp1 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp1.status_code == 400

        # 2. User markers are cleared and session is intact
        assert not is_user_deleted(user_id)
        assert not is_session_revoked(user_id)

        # 3. Surviving product marker is restored in both memory and Redis
        assert not is_product_deleted(surviving_prod_id)
        assert f"deleted_product:{surviving_prod_id}" not in mock_redis_storage

        # 4. Independently deleted product marker was NOT cleared (preserved in memory and Redis)
        assert is_product_deleted(indep_prod_id)
        assert f"deleted_product:{indep_prod_id}" in mock_redis_storage

        # 5. Verify surviving product can be scraped again before retry
        mock_scrape_url = AsyncMock()
        mock_scrape_url.return_value = MagicMock(
            price=Decimal("15.99"),
            currency="USD",
            status="success",
            error_message=None,
            failure_reason=None,
            retry_count=0,
        )

        with patch("app.tasks.scraper_tasks.scrape_url", mock_scrape_url), \
             patch("app.tasks.scraper_tasks.set_scrape_progress"):

            # Scraping the surviving product succeeds and executes scrape_url
            scrape_res = scrape_product_manual(surviving_prod_id)
            assert scrape_res["status"] != "cancelled"
            mock_scrape_url.assert_called_once()

            # In contrast, attempting to scrape the independently deleted product is blocked
            mock_scrape_url.reset_mock()
            indep_scrape_res = scrape_product_manual(indep_prod_id)
            assert indep_scrape_res["status"] == "cancelled"
            mock_scrape_url.assert_not_called()

        # 6. Database recovers and subsequent retry completes successfully
        db.table = orig_table
        resp2 = client.delete("/api/account/delete", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200

        # All tables cleaned and account deletion finalized
        assert len(db.tables["products"]) == 0
        assert len(db.tables["competitors"]) == 0
        assert is_user_deleted(user_id)
        assert is_session_revoked(user_id)
        assert is_product_deleted(surviving_prod_id)


# ============================================================================
# 7. Scheduled Task Tracking, Revocation & Cleanup Tests (REV-03)
# ============================================================================

def test_scrape_all_products_tracks_scheduled_tasks_with_associations():
    """
    REV-03: scrape_all_products must associate queued scrape_single_competitor
    task IDs with owning user, product, and competitor in task registration.
    """
    db = MockSupabaseDB()
    user_id = "user-sched-owner-1"
    prod_id = "prod-sched-1"
    comp_id = "comp-sched-1"

    db.tables["products"] = [{"id": prod_id, "user_id": user_id, "is_active": True}]
    db.tables["competitors"] = [{"id": comp_id, "product_id": prod_id, "url": "https://sched.com/item"}]

    mock_task = MagicMock()
    mock_task.id = "scheduled-celery-task-999"

    with patch("app.tasks.scraper_tasks.get_supabase_client", return_value=db), \
         patch("app.tasks.scraper_tasks.scrape_single_competitor.delay", return_value=mock_task), \
         patch("app.tasks.scraper_tasks.register_active_scrape_task") as mock_reg:

        result = scrape_all_products()

        assert result["total"] == 1
        assert result["queued"] == 1
        mock_reg.assert_called_once_with(
            user_id=user_id,
            product_id=prod_id,
            task_id=mock_task.id,
            competitor_id=comp_id,
        )


def test_scheduled_competitor_task_revoked_on_account_deletion_and_prevents_scrape():
    """
    REV-03: Integration test covering a queued or active scrape_single_competitor task.
    Verifies that deleting the owner revokes the task and prevents further external
    scraping or persistence.
    """
    user_id = "user-sched-del-1"
    email = "scheddel@example.com"
    prod_id = "prod-sched-del-1"
    comp_id = "comp-sched-del-1"
    task_id = "celery-sched-task-456"

    db = MockSupabaseDB()
    db.tables["products"] = [{"id": prod_id, "user_id": user_id, "product_name": "Sched Product", "is_active": True}]
    db.tables["competitors"] = [{"id": comp_id, "product_id": prod_id, "url": "https://sched-target.com/p"}]
    db.tables["price_history"] = []

    # 1. Scheduled task is queued and registered under user, product, and competitor
    register_active_scrape_task(
        user_id=user_id,
        product_id=prod_id,
        task_id=task_id,
        competitor_id=comp_id,
    )

    mock_scrape = AsyncMock()

    with patch("app.db.database.get_supabase_client", return_value=db), \
         patch("app.services.account_service.get_supabase_client", return_value=db), \
         patch("app.tasks.scraper_tasks.get_supabase_client", return_value=db), \
         patch("app.services.scraper_service.scrape_url", mock_scrape), \
         patch("app.tasks.celery_app.celery_app.control.revoke") as mock_revoke:

        # 2. Deleting the owner account triggers cancellation and revocation
        summary = delete_user_account(user_id, client=db)

        # Celery control broadcast must have revoked the scheduled task
        mock_revoke.assert_called_with(task_id, terminate=True)
        assert task_id in [t for t in summary.get("tables_cleaned", []) or []] or summary["tasks_revoked"] >= 1

        # 3. Simulate Celery worker attempting to execute the revoked task
        task_result = scrape_single_competitor(comp_id)

        # 4. Guarantee: Task is cancelled, no external network scrape, no persistence
        assert task_result["status"] == "cancelled"
        mock_scrape.assert_not_called()
        assert len(db.tables["price_history"]) == 0


def test_task_registration_and_cleanup_lifecycle():
    """
    REV-03: Verifies that completed tasks unregister properly to keep active task tracking clean.
    """
    user_id = "user-lifecycle-1"
    prod_id = "prod-lifecycle-1"
    comp_id = "comp-lifecycle-1"
    task_id = "lifecycle-task-001"

    register_active_scrape_task(
        user_id=user_id,
        product_id=prod_id,
        task_id=task_id,
        competitor_id=comp_id,
    )

    # Cancel should find it before unregistration
    # Unregister completes
    unregister_active_scrape_task(
        task_id=task_id,
        user_id=user_id,
        product_id=prod_id,
        competitor_id=comp_id,
    )

    # Subsequent cancellation should find nothing for this task
    revoked = cancel_tasks_for_user(user_id=user_id, product_ids=[prod_id], competitor_ids=[comp_id])
    assert task_id not in revoked

