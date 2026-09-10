"""Tests for batched price alert digests and signed webhooks."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.core.security import CurrentUser, get_current_user
from app.db.database import get_user_supabase_client
from app.db.models import AlertSettingsUpdate
from app.services.digest_service import DigestService
from app.services.webhook_service import WebhookDeliveryError, WebhookService
from main import app


class Query:
    def __init__(self, data=None):
        self.data = data or []
        self.operations = []

    def __getattr__(self, name):
        if name == "not_":
            return self
        def method(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self
        return method

    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeClient:
    def __init__(self, settings, alerts):
        self.settings = settings
        self.alerts = alerts
        self.queries = []
        self.rpc_calls = []

    def table(self, name):
        data = self.settings if name == "user_alert_settings" else []
        query = Query(data=data)
        self.queries.append((name, query))
        return query

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return Query(self.alerts)


def alert(alert_id, alert_type, percent):
    return {
        "id": alert_id,
        "alert_type": alert_type,
        "old_price": 100,
        "new_price": 90,
        "price_change_percent": percent,
        "old_currency": "USD",
        "new_currency": "USD",
        "detected_at": "2026-01-01T00:00:00+00:00",
        "product_name": f"Product {alert_id}",
        "competitor_name": "Store",
        "competitor_url": "https://store.example/item",
    }


def make_service(monkeypatch, settings, alerts, email_result=None, webhook_result=None):
    client = FakeClient([settings], alerts)
    monkeypatch.setattr(
        "app.services.digest_service.get_supabase_client", lambda: client
    )
    email_service = Mock()
    email_service.send_price_alert_digest.return_value = email_result or {
        "success": True,
        "error": None,
    }
    webhook_service = Mock()
    webhook_service.send_digest.return_value = webhook_result or {
        "success": True,
        "error": None,
    }
    return DigestService(email_service, webhook_service), client


def test_digest_run_requires_authentication(client):
    response = client.post("/api/alerts/digests/run", json={})
    assert response.status_code == 403


def test_non_admin_cannot_force_digest_run(client):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="owner@example.com", role="authenticated"
    )
    try:
        response = client.post("/api/alerts/digests/run", json={"force": True})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_digest_run_endpoint_returns_counts(client, monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="owner@example.com", role="admin"
    )
    monkeypatch.setattr(
        "app.api.routes.alerts.DigestService.run_for_user",
        lambda self, user_id, email, **kwargs: {
            "user_id": user_id,
            "status": "dry_run",
            "alerts_count": 3,
            "price_drops": 2,
            "price_increases": 1,
            "currency_changes": 0,
            "email_sent": False,
            "webhook_sent": False,
            "dry_run": True,
            "skipped_reason": None,
        },
    )
    try:
        response = client.post(
            "/api/alerts/digests/run", json={"force": True, "dry_run": True}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["alerts_count"] == 3
    assert response.json()["price_drops"] == 2


def test_alert_settings_validate_secure_webhook_configuration():
    with pytest.raises(ValidationError, match="must use HTTPS"):
        AlertSettingsUpdate(webhook_url="http://hooks.example.test")
    with pytest.raises(ValidationError):
        AlertSettingsUpdate(webhook_secret="too-short")

    update = AlertSettingsUpdate(
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/pricehawk",
        webhook_secret="a-secure-secret-value",
    )
    assert update.webhook_enabled is True


class MockAlertSettingsDB:
    def __init__(self, initial_data=None):
        self.data = dict(initial_data or {})

    def table(self, name):
        assert name == "user_alert_settings"
        db = self

        class SettingsQuery:
            def __init__(self):
                self.filters = {}
                self.action = "select"
                self.payload = {}

            def select(self, *args, **kwargs):
                self.action = "select"
                return self

            def eq(self, col, val):
                self.filters[col] = val
                return self

            def limit(self, val):
                return self

            def update(self, payload):
                self.action = "update"
                self.payload = payload
                return self

            def insert(self, payload):
                self.action = "insert"
                self.payload = payload
                return self

            def execute(self):
                if self.action == "select":
                    if db.data:
                        return SimpleNamespace(data=[dict(db.data)])
                    return SimpleNamespace(data=[])
                elif self.action in ("update", "insert"):
                    db.data.update(self.payload)
                    result = {
                        "user_id": "user-1",
                        "email_enabled": True,
                        "digest_frequency_hours": 24,
                        "alert_price_drop": True,
                        "alert_price_increase": True,
                        "webhook_enabled": False,
                        "webhook_url": None,
                        "webhook_secret": None,
                        "last_digest_sent_at": None,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                    result.update(db.data)
                    return SimpleNamespace(data=[result])
                return SimpleNamespace(data=[])

        return SettingsQuery()


def test_enable_webhook_with_new_complete_configuration(client):
    db = MockAlertSettingsDB({"user_id": "user-1", "webhook_enabled": False, "webhook_url": None, "webhook_secret": None})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-1", email="owner@example.com", role="authenticated")
    app.dependency_overrides[get_user_supabase_client] = lambda: db

    try:
        response = client.put(
            "/api/alerts/settings",
            json={
                "webhook_enabled": True,
                "webhook_url": "https://hooks.example.test/alerts",
                "webhook_secret": "a-sufficiently-strong-secret-16chars",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["webhook_enabled"] is True
    assert data["webhook_url"] == "https://hooks.example.test/alerts"
    assert data["webhook_secret_configured"] is True


def test_enable_webhook_with_stored_credentials(client):
    db = MockAlertSettingsDB({
        "user_id": "user-1",
        "webhook_enabled": False,
        "webhook_url": "https://hooks.example.test/stored",
        "webhook_secret": "existing-secret-16chars",
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-1", email="owner@example.com", role="authenticated")
    app.dependency_overrides[get_user_supabase_client] = lambda: db

    try:
        response = client.put(
            "/api/alerts/settings",
            json={"webhook_enabled": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["webhook_enabled"] is True
    assert data["webhook_url"] == "https://hooks.example.test/stored"
    assert data["webhook_secret_configured"] is True


def test_enable_webhook_rejected_when_missing_secret(client):
    db = MockAlertSettingsDB({"user_id": "user-1", "webhook_enabled": False, "webhook_url": None, "webhook_secret": None})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-1", email="owner@example.com", role="authenticated")
    app.dependency_overrides[get_user_supabase_client] = lambda: db

    try:
        response = client.put(
            "/api/alerts/settings",
            json={
                "webhook_enabled": True,
                "webhook_url": "https://hooks.example.test/alerts",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "webhook_secret" in response.json()["detail"]


def test_enable_webhook_rejected_when_missing_url(client):
    db = MockAlertSettingsDB({"user_id": "user-1", "webhook_enabled": False, "webhook_url": None, "webhook_secret": None})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-1", email="owner@example.com", role="authenticated")
    app.dependency_overrides[get_user_supabase_client] = lambda: db

    try:
        response = client.put(
            "/api/alerts/settings",
            json={
                "webhook_enabled": True,
                "webhook_secret": "a-sufficiently-strong-secret-16chars",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "webhook_url" in response.json()["detail"]


def test_digest_summary_counts_and_orders_biggest_drops():
    summary = DigestService.build_summary(
        [
            alert("small", "price_drop", -5),
            alert("increase", "price_increase", 8),
            alert("large", "price_drop", -30),
            alert("currency", "currency_changed", None),
        ]
    )

    assert summary["counts"] == {
        "price_drops": 2,
        "price_increases": 1,
        "currency_changes": 1,
    }
    assert [item["alert_id"] for item in summary["biggest_price_drops"]] == [
        "large",
        "small",
    ]


def test_dry_run_does_not_claim_deliver_or_clear(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "digest_frequency_hours": 24,
    }
    service, client = make_service(monkeypatch, settings, [])
    dry_run_alerts = [alert("a1", "price_drop", -12)]
    monkeypatch.setattr(service, "_load_alerts", lambda *args: dry_run_alerts)

    result = service.run_for_user("user-1", "owner@example.com", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["alerts_count"] == 1
    service.email_service.send_price_alert_digest.assert_not_called()
    service.webhook_service.send_digest.assert_not_called()
    assert client.rpc_calls == []


def test_frequency_window_skips_unforced_digest(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": False,
        "digest_frequency_hours": 24,
        "last_digest_sent_at": (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(),
    }
    service, client = make_service(monkeypatch, settings, [alert("a1", "price_drop", -12)])

    result = service.run_for_user("user-1", "owner@example.com")

    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "frequency_window_not_elapsed"
    assert client.rpc_calls == []


def test_successful_digest_claims_alerts_and_delivers_both_channels(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "webhook_url": "https://hooks.example.test/pricehawk",
        "webhook_secret": "a-secure-secret-value",
        "digest_frequency_hours": 12,
        "last_digest_sent_at": None,
    }
    alerts = [alert("a1", "price_drop", -20), alert("a2", "price_increase", 11)]
    service, client = make_service(monkeypatch, settings, alerts)

    result = service.run_for_user("user-1", "owner@example.com")

    assert result == {
        "user_id": "user-1",
        "status": "sent",
        "alerts_count": 2,
        "price_drops": 1,
        "price_increases": 1,
        "currency_changes": 0,
        "email_sent": True,
        "webhook_sent": True,
        "dry_run": False,
        "skipped_reason": None,
    }
    assert client.rpc_calls[0][0] == "claim_pending_alerts"
    assert client.rpc_calls[0][1]["p_limit"] == 50
    payload = service.webhook_service.send_digest.call_args.args[2]
    assert payload["event"] == "price_alert.digest"
    assert payload["summary"]["counts"]["price_drops"] == 1
    assert [item["id"] for item in payload["alerts"]] == ["a1", "a2"]


def test_delivery_failure_releases_claim_without_advancing_frequency(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": False,
        "digest_frequency_hours": 24,
    }
    service, client = make_service(
        monkeypatch,
        settings,
        [alert("a1", "price_drop", -12)],
        email_result={"success": False, "error": "SMTP unavailable"},
    )

    result = service.run_for_user("user-1", "owner@example.com")

    assert result["status"] == "failed"
    pending_updates = [q for name, q in client.queries if name == "pending_alerts"]
    assert any(
        operation[0] == "update" and operation[1][0] == {"processing_digest_id": None}
        for query in pending_updates
        for operation in query.operations
    )
    settings_updates = [
        q for name, q in client.queries
        if name == "user_alert_settings" and any(op[0] == "update" for op in q.operations)
    ]
    assert settings_updates == []


class StatefulDigestClient:
    def __init__(self, settings, alerts):
        self.settings = [dict(s) for s in settings]
        self.pending_alerts = [
            dict(a, user_id=a.get("user_id", "user-1"), processing_digest_id=None, included_in_digest=False)
            for a in alerts
        ]
        self.alert_history = {}
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name == "claim_pending_alerts":
            digest_id = params["p_digest_id"]
            p_user_id = params["p_user_id"]
            claimed = []
            for a in self.pending_alerts:
                if (
                    a["user_id"] == p_user_id
                    and a["processing_digest_id"] is None
                    and not a["included_in_digest"]
                ):
                    a["processing_digest_id"] = digest_id
                    claimed.append(a)
            return Query(claimed)
        return Query([])

    def table(self, name):
        client = self

        class TableQuery:
            def __init__(self):
                self.table_name = name
                self.filters = []
                self.action = "select"
                self.payload = None

            def __getattr__(self, attr):
                if attr == "not_":
                    return self

                def method(*args, **kwargs):
                    if attr in ("select", "update", "upsert", "insert"):
                        self.action = attr
                        if args:
                            self.payload = args[0]
                    self.filters.append((attr, args, kwargs))
                    return self

                return method

            def execute(self):
                if self.table_name == "user_alert_settings":
                    if self.action == "update":
                        for s in client.settings:
                            s.update(self.payload)
                        return SimpleNamespace(data=client.settings)
                    return SimpleNamespace(data=client.settings)

                elif self.table_name == "alert_history":
                    if self.action in ("upsert", "insert"):
                        client.alert_history[self.payload["id"]] = dict(self.payload)
                        return SimpleNamespace(data=[self.payload])
                    elif self.action == "select":
                        digest_id = None
                        for f, args, _ in self.filters:
                            if f == "eq" and args[0] == "id":
                                digest_id = args[1]
                        if digest_id and digest_id in client.alert_history:
                            return SimpleNamespace(data=[client.alert_history[digest_id]])
                        return SimpleNamespace(data=[])

                elif self.table_name == "pending_alerts":
                    if self.action == "update":
                        target_digest_id = None
                        for f, args, _ in self.filters:
                            if f == "eq" and args[0] == "processing_digest_id":
                                target_digest_id = args[1]
                        for a in client.pending_alerts:
                            if target_digest_id is None or a["processing_digest_id"] == target_digest_id:
                                a.update(self.payload)
                        return SimpleNamespace(data=[])
                    elif self.action == "select":
                        user_id = None
                        for f, args, _ in self.filters:
                            if f == "eq" and args[0] == "user_id":
                                user_id = args[1]
                        res = [
                            a
                            for a in client.pending_alerts
                            if (user_id is None or a["user_id"] == user_id)
                            and not a["included_in_digest"]
                            and a["processing_digest_id"] is not None
                        ]
                        return SimpleNamespace(data=res)

                return SimpleNamespace(data=[])

        return TableQuery()


def test_partial_failure_email_success_webhook_failure_does_not_redeliver_email(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "webhook_url": "https://hooks.example.test/pricehawk",
        "webhook_secret": "a-secure-secret-value",
        "digest_frequency_hours": 12,
        "last_digest_sent_at": None,
    }
    alerts = [alert("a1", "price_drop", -20), alert("a2", "price_increase", 10)]
    client = StatefulDigestClient([settings], alerts)
    monkeypatch.setattr("app.services.digest_service.get_supabase_client", lambda: client)

    email_service = Mock()
    email_service.send_price_alert_digest.return_value = {"success": True, "error": None}
    webhook_service = Mock()
    webhook_service.send_digest.side_effect = [
        WebhookDeliveryError("endpoint timed out"),
        {"success": True, "error": None},
    ]

    service = DigestService(email_service, webhook_service)

    result1 = service.run_for_user("user-1", "owner@example.com")
    assert result1["status"] == "failed"
    assert result1["email_sent"] is True
    assert result1["webhook_sent"] is False
    assert email_service.send_price_alert_digest.call_count == 1
    assert webhook_service.send_digest.call_count == 1

    claimed = [a for a in client.pending_alerts if a["processing_digest_id"] is not None]
    assert len(claimed) == 2
    assert all(not a["included_in_digest"] for a in claimed)
    initial_digest_id = claimed[0]["processing_digest_id"]
    assert client.alert_history[initial_digest_id]["email_status"] == "sent"
    assert client.alert_history[initial_digest_id]["webhook_status"] == "failed"
    assert client.settings[0]["last_digest_sent_at"] is None

    result2 = service.run_for_user("user-1", "owner@example.com")
    assert result2["status"] == "sent"
    assert result2["email_sent"] is True
    assert result2["webhook_sent"] is True
    assert email_service.send_price_alert_digest.call_count == 1
    assert webhook_service.send_digest.call_count == 2

    assert all(a["included_in_digest"] for a in client.pending_alerts)
    assert all(a["processing_digest_id"] is None for a in client.pending_alerts)
    assert client.alert_history[initial_digest_id]["email_status"] == "sent"
    assert client.alert_history[initial_digest_id]["webhook_status"] == "sent"
    assert client.settings[0]["last_digest_sent_at"] is not None


def test_partial_failure_webhook_success_email_failure_does_not_redeliver_webhook(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "webhook_url": "https://hooks.example.test/pricehawk",
        "webhook_secret": "a-secure-secret-value",
        "digest_frequency_hours": 12,
        "last_digest_sent_at": None,
    }
    alerts = [alert("a1", "price_drop", -15)]
    client = StatefulDigestClient([settings], alerts)
    monkeypatch.setattr("app.services.digest_service.get_supabase_client", lambda: client)

    email_service = Mock()
    email_service.send_price_alert_digest.side_effect = [
        {"success": False, "error": "SMTP server unreachable"},
        {"success": True, "error": None},
    ]
    webhook_service = Mock()
    webhook_service.send_digest.return_value = {"success": True, "error": None}

    service = DigestService(email_service, webhook_service)

    result1 = service.run_for_user("user-1", "owner@example.com")
    assert result1["status"] == "failed"
    assert result1["email_sent"] is False
    assert result1["webhook_sent"] is True
    assert webhook_service.send_digest.call_count == 1
    assert email_service.send_price_alert_digest.call_count == 1

    claimed = [a for a in client.pending_alerts if a["processing_digest_id"] is not None]
    assert len(claimed) == 1
    initial_digest_id = claimed[0]["processing_digest_id"]
    assert client.alert_history[initial_digest_id]["email_status"] == "failed"
    assert client.alert_history[initial_digest_id]["webhook_status"] == "sent"

    result2 = service.run_for_user("user-1", "owner@example.com")
    assert result2["status"] == "sent"
    assert result2["email_sent"] is True
    assert result2["webhook_sent"] is True
    assert webhook_service.send_digest.call_count == 1
    assert email_service.send_price_alert_digest.call_count == 2

    assert all(a["included_in_digest"] for a in client.pending_alerts)
    assert client.alert_history[initial_digest_id]["email_status"] == "sent"
    assert client.alert_history[initial_digest_id]["webhook_status"] == "sent"


def test_repeated_task_execution_does_not_redeliver_successful_channel(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "webhook_url": "https://hooks.example.test/pricehawk",
        "webhook_secret": "a-secure-secret-value",
        "digest_frequency_hours": 12,
        "last_digest_sent_at": None,
    }
    alerts = [alert("a1", "price_drop", -10)]
    client = StatefulDigestClient([settings], alerts)
    client.auth = SimpleNamespace(
        admin=SimpleNamespace(
            get_user_by_id=lambda uid: SimpleNamespace(
                user=SimpleNamespace(email="owner@example.com")
            )
        )
    )

    monkeypatch.setattr("app.services.digest_service.get_supabase_client", lambda: client)
    monkeypatch.setattr("app.tasks.scraper_tasks.get_supabase_client", lambda: client)

    email_service = Mock()
    email_service.send_price_alert_digest.return_value = {"success": True, "error": None}
    webhook_service = Mock()
    webhook_service.send_digest.side_effect = [
        WebhookDeliveryError("connection reset"),
        {"success": True, "error": None},
    ]

    monkeypatch.setattr(
        "app.tasks.scraper_tasks.DigestService",
        lambda: DigestService(email_service, webhook_service),
    )

    from app.tasks.scraper_tasks import send_alert_digests

    batch_result1 = send_alert_digests(force=True)
    assert batch_result1["sent"] == 0
    assert batch_result1["failed"] == 1
    assert email_service.send_price_alert_digest.call_count == 1
    assert webhook_service.send_digest.call_count == 1

    batch_result2 = send_alert_digests(force=True)
    assert batch_result2["sent"] == 1
    assert batch_result2["failed"] == 0
    assert email_service.send_price_alert_digest.call_count == 1
    assert webhook_service.send_digest.call_count == 2


def test_crash_immediately_after_claim_resumes_safely_on_later_run(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "webhook_url": "https://hooks.example.test/pricehawk",
        "webhook_secret": "a-secure-secret-value",
        "digest_frequency_hours": 12,
        "last_digest_sent_at": None,
    }
    alerts = [alert("a1", "price_drop", -10), alert("a2", "price_increase", 5)]
    client = StatefulDigestClient([settings], alerts)
    monkeypatch.setattr("app.services.digest_service.get_supabase_client", lambda: client)

    # Simulate worker 1 crashing immediately after claiming
    digest_id = "stranded-digest-id-1234"
    for a in client.pending_alerts:
        a["processing_digest_id"] = digest_id
        a["included_in_digest"] = False

    # At this point, worker crashed: no alert_history exists, alerts have processing_digest_id
    assert client.alert_history == {}

    email_service = Mock()
    email_service.send_price_alert_digest.return_value = {"success": True, "error": None}
    webhook_service = Mock()
    webhook_service.send_digest.return_value = {"success": True, "error": None}

    service = DigestService(email_service, webhook_service)

    # Later run starts (e.g. next Celery run or retry)
    result = service.run_for_user("user-1", "owner@example.com")

    # Verify that stranded alerts were safely resumed and delivered
    assert result["status"] == "sent"
    assert result["alerts_count"] == 2
    assert result["email_sent"] is True
    assert result["webhook_sent"] is True
    assert email_service.send_price_alert_digest.call_count == 1
    assert webhook_service.send_digest.call_count == 1

    # Verify alerts are marked included and claim cleared
    assert all(a["included_in_digest"] for a in client.pending_alerts)
    assert all(a["processing_digest_id"] is None for a in client.pending_alerts)
    assert client.alert_history[digest_id]["email_status"] == "sent"
    assert client.alert_history[digest_id]["webhook_status"] == "sent"


def test_unexpected_exception_after_claim_does_not_strand_alerts(monkeypatch):
    settings = {
        "user_id": "user-1",
        "email_enabled": True,
        "webhook_enabled": True,
        "webhook_url": "https://hooks.example.test/pricehawk",
        "webhook_secret": "a-secure-secret-value",
        "digest_frequency_hours": 12,
        "last_digest_sent_at": None,
    }
    alerts = [alert("a1", "price_drop", -10)]
    client = StatefulDigestClient([settings], alerts)
    monkeypatch.setattr("app.services.digest_service.get_supabase_client", lambda: client)

    email_service = Mock()
    # Simulate email configuration error (e.g. ValueError from missing SMTP_PASSWORD)
    email_service.send_price_alert_digest.side_effect = [
        ValueError("SMTP_PASSWORD not configured"),
        {"success": True, "error": None},
    ]
    webhook_service = Mock()
    webhook_service.send_digest.return_value = {"success": True, "error": None}

    service = DigestService(email_service, webhook_service)

    # First run: unexpected exception during email delivery is gracefully handled,
    # webhook succeeds, partial delivery state is made durable
    result1 = service.run_for_user("user-1", "owner@example.com")
    assert result1["status"] == "failed"
    assert result1["webhook_sent"] is True
    assert result1["email_sent"] is False

    # Second run: once email is fixed, later run resumes digest and delivers email
    # without redelivering webhook
    result2 = service.run_for_user("user-1", "owner@example.com")
    assert result2["status"] == "sent"
    assert result2["webhook_sent"] is True
    assert result2["email_sent"] is True
    assert webhook_service.send_digest.call_count == 1  # Not duplicated!
    assert email_service.send_price_alert_digest.call_count == 2


def test_webhook_signature_covers_timestamp_and_exact_json(monkeypatch):
    captured = {}

    class Response:
        status_code = 202
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def post(self, url, content, headers):
            captured.update(url=url, content=content, headers=headers)
            return Response()

    monkeypatch.setattr("app.services.webhook_service.socket.getaddrinfo", lambda *args: [
        (None, None, None, None, ("93.184.216.34", 443))
    ])
    monkeypatch.setattr("app.services.webhook_service.httpx.Client", Client)
    monkeypatch.setattr("app.services.webhook_service.time.time", lambda: 1700000000)

    payload = {"event": "price_alert.digest", "alerts": [{"id": "a1"}]}
    result = WebhookService().send_digest(
        "https://hooks.example.test/pricehawk", "a-secure-secret-value", payload
    )

    expected_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    expected_signature = hmac.new(
        b"a-secure-secret-value", b"1700000000." + expected_body, hashlib.sha256
    ).hexdigest()
    assert result["success"] is True
    assert captured["content"] == expected_body
    assert captured["headers"]["X-PriceHawk-Signature"] == f"sha256={expected_signature}"
    assert captured["client_kwargs"]["follow_redirects"] is False


def test_webhook_rejects_private_network_targets(monkeypatch):
    monkeypatch.setattr("app.services.webhook_service.socket.getaddrinfo", lambda *args: [
        (None, None, None, None, ("127.0.0.1", 443))
    ])

    with pytest.raises(WebhookDeliveryError, match="private or reserved"):
        WebhookService().send_digest(
            "https://localhost/hooks", "a-secure-secret-value", {"event": "test"}
        )


def test_webhook_dns_rebinding_cannot_reach_private_destination(monkeypatch):
    dns_responses = [
        # 1st call during validation: public address
        [(None, None, None, None, ("93.184.216.34", 443))],
        # 2nd call during connection: private address (DNS rebinding)
        [(None, None, None, None, ("127.0.0.1", 443))],
    ]

    def mock_getaddrinfo(*args, **kwargs):
        if dns_responses:
            return dns_responses.pop(0)
        return [(None, None, None, None, ("127.0.0.1", 443))]

    connection_targets = []

    def mock_create_connection(address, *args, **kwargs):
        connection_targets.append(address)
        raise ConnectionRefusedError("connection blocked")

    monkeypatch.setattr("app.services.webhook_service.socket.getaddrinfo", mock_getaddrinfo)
    monkeypatch.setattr("httpcore._backends.sync.socket.create_connection", mock_create_connection)

    with pytest.raises(WebhookDeliveryError, match="private or reserved|DNS rebinding"):
        WebhookService().send_digest(
            "https://attacker.example.test/webhook",
            "a-secure-secret-value",
            {"event": "test"},
        )

    # Confirms the request cannot reach the second private address
    assert all(addr[0] != "127.0.0.1" for addr in connection_targets)
    assert len(connection_targets) == 0


@pytest.mark.parametrize("private_ip", [
    "::1",
    "fe80::1",
    "fc00::1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "169.254.169.254",
    "10.0.0.1",
])
def test_webhook_rejects_all_address_family_private_destinations(monkeypatch, private_ip):
    monkeypatch.setattr("app.services.webhook_service.socket.getaddrinfo", lambda *args: [
        (None, None, None, None, (private_ip, 443))
    ])

    with pytest.raises(WebhookDeliveryError, match="private or reserved"):
        WebhookService().send_digest(
            "https://unsafe.example.test/webhook", "a-secure-secret-value", {"event": "test"}
        )
