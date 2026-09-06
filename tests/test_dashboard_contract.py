"""
Route contract tests for dashboard helper endpoints and route inventory synchronization.
"""

from unittest.mock import MagicMock, patch
import re
from fastapi.testclient import TestClient

from main import app
from app.core.security import CurrentUser, get_current_user


def test_dashboard_stats_requires_auth(client):
    """Test /api/dashboard/stats requires authentication."""
    response = client.get("/api/dashboard/stats")
    assert response.status_code in (401, 403)


def test_dashboard_activity_requires_auth(client):
    """Test /api/dashboard/activity requires authentication."""
    response = client.get("/api/dashboard/activity")
    assert response.status_code in (401, 403)


def test_dashboard_products_requires_auth(client):
    """Test /api/dashboard/products requires authentication."""
    response = client.get("/api/dashboard/products")
    assert response.status_code in (401, 403)


def test_insights_feed_requires_auth(client):
    """Test /api/insights requires authentication."""
    response = client.get("/api/insights")
    assert response.status_code in (401, 403)


def test_dashboard_stats_contract(client, auth_headers):
    """Test /api/dashboard/stats response contract when authenticated."""
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()

    # Mock products count
    mock_products = MagicMock()
    mock_products.count = 5
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_products

    # Mock competitors data
    mock_competitors = MagicMock()
    mock_competitors.data = [{"id": "c1"}, {"id": "c2"}]

    # Mock alerts count
    mock_alerts = MagicMock()
    mock_alerts.count = 3

    # Mock insights count
    mock_insights = MagicMock()
    mock_insights.count = 7

    def table_router(table_name):
        mock_t = MagicMock()
        if table_name == "products":
            mock_t.select.return_value.eq.return_value.execute.return_value = mock_products
        elif table_name == "competitors":
            mock_t.select.return_value.eq.return_value.execute.return_value = mock_competitors
        elif table_name == "pending_alerts":
            mock_t.select.return_value.eq.return_value.gte.return_value.execute.return_value = mock_alerts
        elif table_name == "insights":
            mock_t.select.return_value.eq.return_value.execute.return_value = mock_insights
        return mock_t

    mock_sb.table.side_effect = table_router

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
            response = client.get("/api/dashboard/stats", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "products" in data
            assert "competitors" in data
            assert "alerts" in data
            assert "insights" in data
            assert isinstance(data["products"], int)
            assert isinstance(data["competitors"], int)
            assert isinstance(data["alerts"], int)
            assert isinstance(data["insights"], int)
    finally:
        app.dependency_overrides.clear()


def test_dashboard_activity_contract(client, auth_headers):
    """Test /api/dashboard/activity response contract when authenticated."""
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()

    mock_activity = MagicMock()
    mock_activity.data = [
        {
            "id": "alert-1",
            "alert_type": "price_drop",
            "products": {"id": "prod-1", "product_name": "Test Product"},
            "competitors": {"retailer_name": "Example Retailer", "url": "https://example.com"},
            "old_price": "99.99",
            "new_price": "89.99",
            "price_change_percent": "-10.0",
            "detected_at": "2026-09-02T02:15:00Z",
        }
    ]
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_activity

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
            response = client.get("/api/dashboard/activity", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "activity" in data
            assert isinstance(data["activity"], list)
            assert len(data["activity"]) == 1
            item = data["activity"][0]
            assert item["id"] == "alert-1"
            assert item["type"] == "price_drop"
            assert item["product_id"] == "prod-1"
            assert item["product_name"] == "Test Product"
            assert item["retailer"] == "Example Retailer"
            assert item["old_price"] == 99.99
            assert item["new_price"] == 89.99
            assert item["change_percent"] == -10.0
            assert item["detected_at"] == "2026-09-02T02:15:00Z"
    finally:
        app.dependency_overrides.clear()


def test_dashboard_products_contract(client, auth_headers):
    """Test /api/dashboard/products response contract when authenticated."""
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()

    mock_products = MagicMock()
    mock_products.data = [
        {
            "id": "prod-1",
            "product_name": "Test Product",
            "is_active": True,
            "created_at": "2026-09-02T02:15:00Z",
        }
    ]
    mock_comp = MagicMock()
    mock_comp.count = 2

    def table_router(table_name):
        mock_t = MagicMock()
        if table_name == "products":
            mock_t.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_products
        elif table_name == "competitors":
            mock_t.select.return_value.eq.return_value.execute.return_value = mock_comp
        return mock_t

    mock_sb.table.side_effect = table_router

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
            response = client.get("/api/dashboard/products", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "products" in data
            assert isinstance(data["products"], list)
            assert len(data["products"]) == 1
            item = data["products"][0]
            assert item["id"] == "prod-1"
            assert item["product_name"] == "Test Product"
            assert item["is_active"] is True
            assert item["competitor_count"] == 2
    finally:
        app.dependency_overrides.clear()


def test_insights_feed_contract(client, auth_headers):
    """Test /api/insights response contract when authenticated."""
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()

    mock_insights = MagicMock()
    mock_insights.data = [
        {
            "id": "insight-1",
            "product_id": "prod-1",
            "insight_text": "Price dropped by 10% on weekends.",
            "insight_type": "pattern",
            "confidence_score": 0.9,
            "generated_at": "2026-09-02T02:15:00Z",
            "products": {"user_id": "test-user-id-1234", "product_name": "Test Product"},
        }
    ]
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_insights

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
            response = client.get("/api/insights", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "insights" in data
            assert "total" in data
            assert isinstance(data["insights"], list)
            assert data["total"] == 1
            item = data["insights"][0]
            assert item["id"] == "insight-1"
            assert item["product_id"] == "prod-1"
            assert item["product_name"] == "Test Product"
            assert item["insight_text"] == "Price dropped by 10% on weekends."
            assert item["insight_type"] == "pattern"
            assert item["generated_at"] == "2026-09-02T02:15:00Z"
    finally:
        app.dependency_overrides.clear()


def test_all_documented_routes_in_api_md_match_registered_routes():
    """Ensure all documented routes in docs/API.md exist and match registered routes."""
    with open("docs/API.md", "r") as f:
        content = f.read()

    documented_routes = set(re.findall(r"`(GET|POST|PUT|DELETE|PATCH)\s+([^`]+)`", content))
    schema = app.openapi()
    registered_routes = set()
    for path, methods in schema["paths"].items():
        for method in methods:
            if path.startswith("/api"):
                registered_routes.add((method.upper(), path))

    # All documented routes must be registered in OpenAPI
    missing_in_app = documented_routes - registered_routes
    assert not missing_in_app, f"Documented routes missing from app: {missing_in_app}"

    # All API endpoints in app must be documented in docs/API.md
    missing_in_docs = registered_routes - documented_routes
    assert not missing_in_docs, f"API endpoints missing from docs/API.md: {missing_in_docs}"
