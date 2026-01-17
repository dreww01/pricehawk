"""
Health endpoint tests.
"""


def test_health_check(client):
    """Test health check endpoint returns healthy."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_redirects_to_dashboard(client):
    """Test root path redirects to dashboard."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"
