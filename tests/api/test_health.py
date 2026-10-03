"""Tests for health check and API documentation endpoints."""

from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    """Health endpoint returns healthy status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_redirects_to_dashboard(client: TestClient):
    """Root endpoint redirects to dashboard."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_docs_available(client: TestClient):
    """Swagger UI is accessible."""
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_redoc_available(client: TestClient):
    """ReDoc is accessible."""
    response = client.get("/api/redoc")
    assert response.status_code == 200


def test_openapi_schema(client: TestClient):
    """OpenAPI schema is accessible and valid."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "PriceHawk API"
    assert schema["info"]["version"] == "0.1.0"
