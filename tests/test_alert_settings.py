"""Alert settings API contract tests."""

from datetime import datetime, timezone

import pytest

from app.core.security import CurrentUser, get_current_user
from app.db.database import get_user_supabase_client
from main import app


NOW = datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc)


def alert_settings_row(**overrides):
    row = {
        "user_id": "user-123",
        "email_enabled": True,
        "digest_frequency_hours": 24,
        "alert_price_drop": True,
        "alert_price_increase": True,
        "last_digest_sent_at": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    row.update(overrides)
    return row


class FakeAlertSettingsTable:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.update_data = None
        self.insert_data = None

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def update(self, data):
        self.update_data = data
        if self.rows:
            self.rows[0] = {**self.rows[0], **data, "updated_at": NOW.isoformat()}
        return self

    def insert(self, data):
        self.insert_data = data
        self.rows = [alert_settings_row(**data)]
        return self

    def execute(self):
        return type("Response", (), {"data": self.rows})()


class FakeSupabaseClient:
    def __init__(self, table):
        self.alert_settings_table = table

    def table(self, name):
        assert name == "user_alert_settings"
        return self.alert_settings_table


@pytest.fixture
def alert_api(client):
    table = FakeAlertSettingsTable(rows=[alert_settings_row()])
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-123", email="user@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: FakeSupabaseClient(table)
    try:
        yield client, table
    finally:
        app.dependency_overrides.clear()


def test_get_alert_settings_serializes_live_contract(alert_api):
    client, _table = alert_api

    response = client.get("/api/alerts/settings")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-123",
        "email_enabled": True,
        "digest_frequency_hours": 24,
        "alert_price_drop": True,
        "alert_price_increase": True,
        "last_digest_sent_at": None,
        "created_at": "2025-01-10T12:00:00Z",
        "updated_at": "2025-01-10T12:00:00Z",
    }


def test_put_alert_settings_accepts_and_returns_live_contract(alert_api):
    client, table = alert_api

    response = client.put(
        "/api/alerts/settings",
        json={
            "email_enabled": False,
            "digest_frequency_hours": 12,
            "alert_price_drop": False,
            "alert_price_increase": True,
        },
    )

    assert response.status_code == 200
    assert table.update_data == {
        "email_enabled": False,
        "digest_frequency_hours": 12,
        "alert_price_drop": False,
        "alert_price_increase": True,
    }
    assert response.json() == {
        "user_id": "user-123",
        "email_enabled": False,
        "digest_frequency_hours": 12,
        "alert_price_drop": False,
        "alert_price_increase": True,
        "last_digest_sent_at": None,
        "created_at": "2025-01-10T12:00:00Z",
        "updated_at": "2025-01-10T12:00:00Z",
    }
