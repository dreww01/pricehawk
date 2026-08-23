"""Tests for health check endpoint."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from app.api.routes.health import check_database_connection

client = TestClient(app)


def test_health_check_success():
    """Health endpoint returns status ok and database connected."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["error"] is None
    assert datetime.fromisoformat(data["timestamp"]) is not None


def test_health_check_database_error():
    """Health endpoint returns 503 error when database check fails."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("DB connection refused")

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        response = client.get("/api/health")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "error"
    assert data["database"] == "disconnected"
    assert "DB connection refused" in data["error"]


def test_root_redirects_to_dashboard():
    """Root endpoint redirects to dashboard."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


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
    assert schema["info"]["version"] == "0.1.0"
    assert "/api/health" in schema["paths"]
