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
from app.db.models import AlertSettingsUpdate
from app.services.digest_service import DigestService
from app.services.webhook_service import WebhookDeliveryError, WebhookService
from main import app


class Query:
    def __init__(self, data=None):
        self.data = data or []
        self.operations = []

    def __getattr__(self, name):
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
