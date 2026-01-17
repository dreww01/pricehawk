"""
Scraper endpoint tests.
"""


def test_manual_scrape_requires_auth(client):
    """Test manual scrape requires authentication."""
    response = client.post("/api/scraper/scrape/manual/test-product-id")
    assert response.status_code == 403


def test_price_history_requires_auth(client):
    """Test price history requires authentication."""
    response = client.get("/api/scraper/prices/test-product-id/history")
    assert response.status_code == 403


def test_latest_price_requires_auth(client):
    """Test latest price requires authentication."""
    response = client.get("/api/scraper/prices/latest/test-competitor-id")
    assert response.status_code == 403


def test_chart_data_requires_auth(client):
    """Test chart data requires authentication."""
    response = client.get("/api/scraper/prices/test-product-id/chart-data")
    assert response.status_code == 403


def test_worker_health_no_auth(client):
    """Test worker health endpoint is accessible without auth."""
    response = client.get("/api/scraper/scrape/worker-health")
    assert response.status_code == 200
    assert "worker_status" in response.json()
