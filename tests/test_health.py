"""Tests for health & system status endpoints."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.api.routes.health import check_database_connection, HealthResponse


def test_health_check_success(client):
    """Test /api/health returns 200 with status ok and connected database."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["error"] is None
    # Validate timestamp is a parseable ISO datetime
    assert datetime.fromisoformat(data["timestamp"]) is not None


def test_health_check_root_path_success(client):
    """Test /health returns 200 with status ok."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_health_check_trailing_slash(client):
    """Test /api/health/ and /health/ return 200."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        res1 = client.get("/api/health/")
        res2 = client.get("/health/")

    assert res1.status_code == 200
    assert res1.json()["status"] == "ok"
    assert res2.status_code == 200
    assert res2.json()["status"] == "ok"


def test_health_check_database_failure(client):
    """Test /api/health returns 503 when database query fails."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("DB connection timeout")

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        response = client.get("/api/health")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "error"
    assert data["database"] == "disconnected"
    assert "DB connection timeout" in data["error"]
    assert datetime.fromisoformat(data["timestamp"]) is not None


def test_health_check_root_path_database_failure(client):
    """Test /health and /health/ return 503 when database query fails."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("DB connection refused")

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        res1 = client.get("/health")
        res2 = client.get("/health/")

    assert res1.status_code == 503
    assert res1.json()["status"] == "error"
    assert res1.json()["database"] == "disconnected"
    assert res2.status_code == 503
    assert res2.json()["status"] == "error"
    assert res2.json()["database"] == "disconnected"


def test_health_check_client_init_failure(client):
    """Test /api/health returns 503 when Supabase client creation throws."""
    with patch("app.api.routes.health.get_supabase_client", side_effect=Exception("Missing config")):
        response = client.get("/api/health")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "error"
    assert data["database"] == "disconnected"
    assert "Missing config" in data["error"]


def test_check_database_connection_unit():
    """Unit test for check_database_connection helper function."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    with patch("app.api.routes.health.get_supabase_client", return_value=mock_client):
        ok, err = check_database_connection()
        assert ok is True
        assert err is None

    with patch("app.api.routes.health.get_supabase_client", side_effect=RuntimeError("Supabase down")):
        ok, err = check_database_connection()
        assert ok is False
        assert "Supabase down" in err


def test_root_redirects_to_dashboard(client):
    """Test root path redirects to dashboard."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"
