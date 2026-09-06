"""
Tests for alert endpoints and response serialization.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.core.security import CurrentUser, get_current_user
from app.db.database import get_user_supabase_client
from app.db.models import AlertHistoryResponse
from main import app


def test_alert_history_requires_auth(client):
    """Test /api/alerts/history requires authentication."""
    response = client.get("/api/alerts/history")
    assert response.status_code in (401, 403)


def test_alert_history_non_empty_serialization(client, auth_headers):
    """Test /api/alerts/history serializes non-empty digest records correctly."""
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [
        {
            "id": "9e0f1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b",
            "digest_sent_at": "2026-09-02T03:00:00Z",
            "alerts_count": 3,
            "email_status": "sent",
            "error_message": None,
        },
        {
            "id": "8d0e1a2b-2c3d-4e5f-6a7b-8c9d0e1f2a3b",
            "digest_sent_at": "2026-09-01T03:00:00Z",
            "alerts_count": 1,
            "email_status": "failed",
            "error_message": "SMTP connection timed out",
        },
    ]

    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = (
        mock_response
    )
    mock_sb.table.return_value = mock_table

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_user_supabase_client] = lambda: mock_sb
    try:
        response = client.get("/api/alerts/history?limit=10", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["alerts"]) == 2

        first = data["alerts"][0]
        assert first["id"] == "9e0f1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b"
        assert first["digest_sent_at"] == "2026-09-02T03:00:00Z"
        assert first["alerts_count"] == 3
        assert first["email_status"] == "sent"
        assert first["error_message"] is None

        second = data["alerts"][1]
        assert second["id"] == "8d0e1a2b-2c3d-4e5f-6a7b-8c9d0e1f2a3b"
        assert second["digest_sent_at"] == "2026-09-01T03:00:00Z"
        assert second["alerts_count"] == 1
        assert second["email_status"] == "failed"
        assert second["error_message"] == "SMTP connection timed out"
    finally:
        app.dependency_overrides.clear()


def test_alert_history_documentation_contract(client, auth_headers):
    """
    Ensure docs/API.md alert-history JSON example matches AlertHistoryResponse model
    and the actual serialized response from GET /api/alerts/history.
    """
    docs_text = Path("docs/API.md").read_text(encoding="utf-8")

    section_match = re.search(
        r"### 11\.4 Get Alert History[\s\S]*?`GET /api/alerts/history`[\s\S]*?```json\s*\n([\s\S]*?)\n\s*```",
        docs_text,
    )
    assert section_match is not None, "Could not find GET /api/alerts/history JSON example in docs/API.md"
    doc_json = json.loads(section_match.group(1))

    assert "alerts" in doc_json
    assert "total" in doc_json
    assert isinstance(doc_json["alerts"], list)
    assert len(doc_json["alerts"]) > 0

    documented_alert = doc_json["alerts"][0]
    expected_fields = set(AlertHistoryResponse.model_fields.keys())

    assert set(documented_alert.keys()) == expected_fields, (
        f"Documented alert history fields {set(documented_alert.keys())} do not match "
        f"AlertHistoryResponse model fields {expected_fields}"
    )

    # Validate against live route serialization
    mock_user = CurrentUser(
        id="test-user-id-1234",
        email="test@example.com",
        role="authenticated",
    )
    mock_sb = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [documented_alert]
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = (
        mock_response
    )

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_user_supabase_client] = lambda: mock_sb
    try:
        response = client.get("/api/alerts/history", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["alerts"]) == 1
        live_alert = data["alerts"][0]
        assert set(live_alert.keys()) == set(documented_alert.keys())
    finally:
        app.dependency_overrides.clear()
