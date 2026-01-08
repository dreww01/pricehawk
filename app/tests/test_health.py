"""Tests for health check endpoint."""

from fastapi.testclient import TestClient
from main import app


client = TestClient(app)


def test_health_check():
    """Health endpoint returns healthy status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_redirects_to_docs():
    """Root endpoint redirects to API docs."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/api/docs"


def test_docs_available():
    """Swagger UI is accessible."""
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_redoc_available():
    """ReDoc is accessible."""
    response = client.get("/api/redoc")
    assert response.status_code == 200


def test_openapi_schema():
    """OpenAPI schema is accessible and valid."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "PriceHawk API"
    assert schema["info"]["version"] == "1.0.0"
