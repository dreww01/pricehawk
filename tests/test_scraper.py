"""
Scraper endpoint tests.
"""

from unittest.mock import MagicMock, patch
from app.core.security import CurrentUser, get_current_user
from main import app


def test_manual_scrape_requires_auth(client):
    """Test manual scrape requires authentication."""
    response = client.post("/api/scraper/scrape/manual/test-product-id")
    assert response.status_code in (401, 403)


def test_manual_scrape_authenticated_returns_202_accepted(client, auth_headers):
    """Test manual scrape returns HTTP 202 Accepted and task_id."""
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()
    mock_product_res = MagicMock()
    mock_product_res.data = [{"id": "test-product-id"}]
    mock_comp_res = MagicMock()
    mock_comp_res.data = [{"id": "c1"}, {"id": "c2"}]

    def table_mock(table_name):
        t = MagicMock()
        if table_name == "products":
            t.select.return_value.eq.return_value.execute.return_value = mock_product_res
        elif table_name == "competitors":
            t.select.return_value.eq.return_value.execute.return_value = mock_comp_res
        return t

    mock_sb.table.side_effect = table_mock
    mock_task = MagicMock()
    mock_task.id = "mock-task-uuid-1234"

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.api.routes.scraper.get_supabase_client", return_value=mock_sb), patch(
            "app.api.routes.scraper.scrape_product_manual.delay", return_value=mock_task
        ):
            response = client.post(
                "/api/scraper/scrape/manual/test-product-id",
                headers=auth_headers,
            )
            assert response.status_code == 202
            data = response.json()
            assert data["task_id"] == "mock-task-uuid-1234"
            assert data["status"] == "queued"
    finally:
        app.dependency_overrides.clear()


def test_price_history_requires_auth(client):
    """Test price history requires authentication."""
    response = client.get("/api/scraper/prices/test-product-id/history")
    assert response.status_code in (401, 403)


def test_latest_price_requires_auth(client):
    """Test latest price requires authentication."""
    response = client.get("/api/scraper/prices/latest/test-competitor-id")
    assert response.status_code in (401, 403)


def test_chart_data_requires_auth(client):
    """Test chart data requires authentication."""
    response = client.get("/api/scraper/prices/test-product-id/chart-data")
    assert response.status_code in (401, 403)


def test_worker_health_no_auth(client):
    """Test worker health endpoint is accessible without auth."""
    response = client.get("/api/scraper/scrape/worker-health")
    assert response.status_code == 200
    assert "worker_status" in response.json()
