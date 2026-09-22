"""
Tests for automatic price drop alerts, webhook notifications, test pings,
duplicate alert suppression, and alert history auditing.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import json

import pytest
from fastapi.testclient import TestClient

from app.core.security import CurrentUser, get_current_user
from app.db.database import get_user_supabase_client, get_supabase_client
from app.db.models import CheckPriceDropRequest, WebhookRegisterRequest
from app.services.alert_service import AlertService
from app.services.webhook_service import WebhookDeliveryError, WebhookService
from main import app


class Query:
    def __init__(self, data=None):
        self.data = data or []
        self.operations = []
        self.count = len(self.data)
        self._is_single = False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name == "not_":
            return self

        def method(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return method

    def single(self):
        self._is_single = True
        return self

    def execute(self):
        if self._is_single:
            first_item = self.data[0] if self.data else None
            return SimpleNamespace(data=first_item, count=1 if first_item else 0)
        return SimpleNamespace(data=self.data, count=self.count)


class MockDatabaseClient:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.inserts = []
        self.updates = []
        self.queries = []

    def table(self, name):
        data = self.tables.get(name, [])

        class TableQuery(Query):
            def __init__(sub_self, data):
                super().__init__(data)

            def insert(sub_self, values):
                self.inserts.append((name, values))
                row = dict(values) if isinstance(values, dict) else values
                if name not in self.tables:
                    self.tables[name] = []
                self.tables[name].append(row)
                return TableQuery([row])

            def update(sub_self, values):
                self.updates.append((name, values))
                if self.tables.get(name):
                    for row in self.tables[name]:
                        row.update(values)
                return TableQuery(self.tables.get(name, [values]))

        query = TableQuery(data)
        self.queries.append((name, query))
        return query


# ---------------------------------------------------------------------------
# 1. Webhook Registration Tests
# ---------------------------------------------------------------------------

def test_get_webhook_config_returns_default_when_no_settings():
    db = MockDatabaseClient({"user_alert_settings": []})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.get("/api/alerts/webhook")
        assert res.status_code == 200
        data = res.json()
        assert data["webhook_url"] is None
        assert data["webhook_enabled"] is False
        assert data["webhook_secret_configured"] is False
    finally:
        app.dependency_overrides.clear()


def test_get_webhook_config_returns_existing_configuration():
    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_url": "https://hooks.store.com/price-alerts",
            "webhook_enabled": True,
            "webhook_secret": "my-secret-key-12345",
        }]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.get("/api/alerts/webhook")
        assert res.status_code == 200
        data = res.json()
        assert data["webhook_url"] == "https://hooks.store.com/price-alerts"
        assert data["webhook_enabled"] is True
        assert data["webhook_secret_configured"] is True
    finally:
        app.dependency_overrides.clear()


def test_register_webhook_allows_optional_secret():
    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_url": None,
            "webhook_enabled": False,
            "webhook_secret": None,
        }]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        with patch.object(WebhookService, "_validate_url", return_value={"93.184.216.34"}):
            client = TestClient(app)
            # Register without secret
            res = client.post("/api/alerts/webhook", json={
                "webhook_url": "https://hooks.store.com/alerts",
                "enabled": True,
            })
            assert res.status_code == 200
            data = res.json()
            assert data["webhook_url"] == "https://hooks.store.com/alerts"
            assert data["webhook_enabled"] is True
            assert data["webhook_secret_configured"] is False
            assert "saved successfully" in data["message"].lower()

            # Now update with secret
            res2 = client.put("/api/alerts/webhook", json={
                "webhook_url": "https://hooks.store.com/alerts",
                "webhook_secret": "secure-secret-token",
                "enabled": True,
            })
            assert res2.status_code == 200
            data2 = res2.json()
            assert data2["webhook_secret_configured"] is True
    finally:
        app.dependency_overrides.clear()


def test_register_webhook_rejects_non_https():
    db = MockDatabaseClient({"user_alert_settings": []})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.post("/api/alerts/webhook", json={
            "webhook_url": "http://insecure-endpoint.com/alerts",
            "enabled": True,
        })
        assert res.status_code == 422  # Pydantic validation error for HTTPS
    finally:
        app.dependency_overrides.clear()


def test_register_webhook_rejects_ssrf_destination():
    db = MockDatabaseClient({"user_alert_settings": []})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        # Attempt to target localhost / internal network
        res = client.post("/api/alerts/webhook", json={
            "webhook_url": "https://127.0.0.1:8000/hook",
            "enabled": True,
        })
        assert res.status_code == 400
        assert "private or reserved" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_delete_webhook_disables_endpoint():
    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_url": "https://hooks.store.com/price-alerts",
            "webhook_enabled": True,
            "webhook_secret": "secret",
        }]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.delete("/api/alerts/webhook")
        assert res.status_code == 200
        data = res.json()
        assert data["webhook_url"] is None
        assert data["webhook_enabled"] is False
        assert data["webhook_secret_configured"] is False
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 2. Test Webhook Ping Action Tests
# ---------------------------------------------------------------------------

def test_send_test_webhook_ping_success(monkeypatch):
    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_url": "https://hooks.store.com/test",
            "webhook_secret": "secret-123",
            "webhook_enabled": True,
        }],
        "alert_history": [],
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    monkeypatch.setattr("app.api.routes.alerts.get_supabase_client", lambda: db)

    mock_send = Mock(return_value={"success": True, "status_code": 200, "error": None})
    monkeypatch.setattr(WebhookService, "send_test_ping", mock_send)

    try:
        client = TestClient(app)
        res = client.post("/api/alerts/test-webhook", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["status_code"] == 200
        assert data["response_code"] == 200

        # Verify audit record inserted into alert_history
        assert len(db.tables["alert_history"]) == 1
        audit_entry = db.tables["alert_history"][0]
        assert audit_entry["user_id"] == "user-1"
        assert audit_entry["webhook_status"] == "sent"
        assert audit_entry["response_code"] == 200
    finally:
        app.dependency_overrides.clear()


def test_send_test_webhook_alternative_route_and_custom_body(monkeypatch):
    db = MockDatabaseClient({
        "user_alert_settings": [],
        "alert_history": [],
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    monkeypatch.setattr("app.api.routes.alerts.get_supabase_client", lambda: db)

    mock_send = Mock(return_value={"success": True, "status_code": 204, "error": None})
    monkeypatch.setattr(WebhookService, "send_test_ping", mock_send)

    try:
        client = TestClient(app)
        res = client.post("/api/alerts/webhook/test", json={
            "webhook_url": "https://hooks.store.com/custom-test",
            "webhook_secret": "temp-secret",
        })
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert res.json()["response_code"] == 204
    finally:
        app.dependency_overrides.clear()


def test_send_test_webhook_missing_url_returns_400():
    db = MockDatabaseClient({"user_alert_settings": []})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.post("/api/alerts/test-webhook", json={})
        assert res.status_code == 400
        assert "no webhook url" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. Webhook Delivery & Secret HMAC Signature Tests
# ---------------------------------------------------------------------------

def test_webhook_send_alert_without_secret_omits_signature(monkeypatch):
    wh = WebhookService()
    monkeypatch.setattr(wh, "_validate_url", lambda url: {"93.184.216.34"})

    captured_headers = {}

    def mock_post(url, content, headers):
        captured_headers.update(headers)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        return mock_resp

    with patch("httpx.Client.post", side_effect=mock_post):
        result = wh.send_alert(
            webhook_url="https://hooks.example.com/alerts",
            payload={"event": "price_drop", "new_price": 50.0},
            webhook_secret=None,
        )
        assert result["success"] is True
        assert "X-PriceHawk-Signature" not in captured_headers
        assert "X-PriceHawk-Timestamp" in captured_headers


def test_webhook_send_alert_with_secret_includes_hmac_signature(monkeypatch):
    import hmac
    import hashlib

    wh = WebhookService()
    monkeypatch.setattr(wh, "_validate_url", lambda url: {"93.184.216.34"})

    captured = {}

    def mock_post(url, content, headers):
        captured["content"] = content
        captured["headers"] = headers
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        return mock_resp

    with patch("httpx.Client.post", side_effect=mock_post):
        secret = "super-secret-signature-key"
        payload = {"event": "price_drop", "old_price": 100.0, "new_price": 75.0}
        result = wh.send_alert(
            webhook_url="https://hooks.example.com/alerts",
            payload=payload,
            webhook_secret=secret,
        )
        assert result["success"] is True
        headers = captured["headers"]
        assert "X-PriceHawk-Signature" in headers
        timestamp = headers["X-PriceHawk-Timestamp"]

        # Verify signature validity
        signed_bytes = timestamp.encode("ascii") + b"." + captured["content"]
        expected_sig = hmac.new(secret.encode("utf-8"), signed_bytes, hashlib.sha256).hexdigest()
        assert headers["X-PriceHawk-Signature"] == f"sha256={expected_sig}"


# ---------------------------------------------------------------------------
# 4. Automatic Price Drop Conditions & Duplicate Suppression Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_price_drop_triggers_alert_when_threshold_crossed(monkeypatch):
    comp_id = "comp-1"
    user_id = "user-1"
    prod_id = "prod-1"

    db = MockDatabaseClient({
        "competitors": [{
            "id": comp_id,
            "url": "https://store.example/item",
            "retailer_name": "Competitor Store",
            "alert_threshold_percent": Decimal("10.00"),
            "product_id": prod_id,
            "products": {
                "id": prod_id,
                "product_name": "Awesome Sneakers",
                "user_id": user_id,
            }
        }],
        "price_history": [
            {"competitor_id": comp_id, "price": Decimal("100.00"), "currency": "USD", "scrape_status": "success"},
        ],
        "user_alert_settings": [{
            "user_id": user_id,
            "email_enabled": True,
            "webhook_enabled": True,
            "webhook_url": "https://hooks.store.com/incoming",
            "webhook_secret": "sec",
            "alert_price_drop": True,
            "alert_price_increase": True,
        }],
        "pending_alerts": [],
        "alert_history": [],
    })

    monkeypatch.setattr("app.services.alert_service.get_supabase_client", lambda: db)
    dispatched_events = []
    monkeypatch.setattr(
        AlertService,
        "dispatch_alert_webhook",
        lambda self, user_id, alert_event, correlation_id=None: dispatched_events.append(alert_event)
    )

    svc = AlertService()
    # Price drops from $100 to $80 (20% drop, > 10% threshold)
    res = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("80.00"),
        currency="USD",
    )

    assert res["alert_created"] is True
    assert res["alert_type"] == "price_drop"
    assert res["change_percent"] == Decimal("-20.00")
    assert res["webhook_dispatched"] is True
    assert len(dispatched_events) == 1
    event = dispatched_events[0]
    assert event["event"] == "price_drop"
    assert event["old_price"] == 100.0
    assert event["new_price"] == 80.0
    assert event["product_name"] == "Awesome Sneakers"


@pytest.mark.asyncio
async def test_price_drop_does_not_trigger_when_below_threshold(monkeypatch):
    comp_id = "comp-1"
    user_id = "user-1"
    prod_id = "prod-1"

    db = MockDatabaseClient({
        "competitors": [{
            "id": comp_id,
            "url": "https://store.example/item",
            "retailer_name": "Store",
            "alert_threshold_percent": Decimal("10.00"),
            "product_id": prod_id,
            "products": {"id": prod_id, "product_name": "Sneakers", "user_id": user_id}
        }],
        "price_history": [
            {"competitor_id": comp_id, "price": Decimal("100.00"), "currency": "USD", "scrape_status": "success"},
        ],
        "user_alert_settings": [{
            "user_id": user_id,
            "email_enabled": True,
            "alert_price_drop": True,
        }],
        "pending_alerts": [],
    })

    monkeypatch.setattr("app.services.alert_service.get_supabase_client", lambda: db)
    svc = AlertService()
    # Price drops from $100 to $98 (2% drop, threshold is 10%, change is $2 < MIN $5)
    res = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("98.00"),
        currency="USD",
    )
    assert res["alert_created"] is False
    assert "below threshold" in res["message"].lower()


@pytest.mark.asyncio
async def test_price_drop_matches_custom_target_percentage(monkeypatch):
    comp_id = "comp-1"
    user_id = "user-1"
    prod_id = "prod-1"

    db = MockDatabaseClient({
        "competitors": [{
            "id": comp_id,
            "url": "https://store.example/item",
            "retailer_name": "Store",
            "alert_threshold_percent": Decimal("5.00"),  # Competitor default is 5%
            "product_id": prod_id,
            "products": {"id": prod_id, "product_name": "Sneakers", "user_id": user_id}
        }],
        "price_history": [
            {"competitor_id": comp_id, "price": Decimal("100.00"), "currency": "USD", "scrape_status": "success"},
        ],
        "user_alert_settings": [{"user_id": user_id, "email_enabled": True, "alert_price_drop": True}],
        "pending_alerts": [],
    })

    monkeypatch.setattr("app.services.alert_service.get_supabase_client", lambda: db)
    svc = AlertService()

    # If user specifies target 15%: price drop from 100 to 92 (8% drop) should NOT alert,
    # even though 8% > 5% competitor default!
    res_below = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("92.00"),
        currency="USD",
        custom_threshold=Decimal("15.00"),
    )
    assert res_below["alert_created"] is False

    # Price drop from 100 to 80 (20% drop) crosses the 15% target:
    res_above = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("80.00"),
        currency="USD",
        custom_threshold=Decimal("15.00"),
    )
    assert res_above["alert_created"] is True
    assert res_above["alert_type"] == "price_drop"


@pytest.mark.asyncio
async def test_duplicate_alert_suppression_prevents_alert_fatigue(monkeypatch):
    comp_id = "comp-1"
    user_id = "user-1"
    prod_id = "prod-1"

    db = MockDatabaseClient({
        "competitors": [{
            "id": comp_id,
            "url": "https://store.example/item",
            "retailer_name": "Store",
            "alert_threshold_percent": Decimal("10.00"),
            "product_id": prod_id,
            "products": {"id": prod_id, "product_name": "Sneakers", "user_id": user_id}
        }],
        "price_history": [
            {"competitor_id": comp_id, "price": Decimal("100.00"), "currency": "USD", "scrape_status": "success"},
        ],
        "user_alert_settings": [{"user_id": user_id, "email_enabled": True, "alert_price_drop": True}],
        # Existing alert already dispatched for $80
        "pending_alerts": [{
            "id": "existing-alert-1",
            "competitor_id": comp_id,
            "alert_type": "price_drop",
            "new_price": Decimal("80.00"),
            "detected_at": "2026-03-01T10:00:00Z",
            "included_in_digest": False,
        }],
    })

    monkeypatch.setattr("app.services.alert_service.get_supabase_client", lambda: db)
    svc = AlertService()

    # 1. Same price ($80): should be suppressed as duplicate
    res1 = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("80.00"),
        currency="USD",
    )
    assert res1["alert_created"] is False
    assert res1.get("suppressed") is True
    assert "duplicate alert suppressed" in res1["message"].lower()

    # 2. Slight insignificant drop to $79.50 (drop from $80 is 0.625% and $0.50): suppressed
    res2 = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("79.50"),
        currency="USD",
    )
    assert res2["alert_created"] is False
    assert res2.get("suppressed") is True

    # 3. Substantial further drop to $65 (drop from $80 is 18.75% > 10%): NOT suppressed, creates new alert!
    res3 = await svc.check_price_change_and_alert(
        competitor_id=comp_id,
        new_price=Decimal("65.00"),
        currency="USD",
    )
    assert res3["alert_created"] is True
    assert res3["alert_type"] == "price_drop"


# ---------------------------------------------------------------------------
# 5. Alert History Audit Log Tests
# ---------------------------------------------------------------------------

def test_get_alert_history_includes_delivered_status_and_response_codes():
    db = MockDatabaseClient({
        "alert_history": [
            {
                "id": "h-1",
                "user_id": "user-1",
                "digest_sent_at": "2026-03-01T12:00:00Z",
                "alerts_count": 1,
                "price_drops": 1,
                "price_increases": 0,
                "currency_changes": 0,
                "email_status": "disabled",
                "webhook_status": "sent",
                "response_code": 200,
                "error_message": None,
            },
            {
                "id": "h-2",
                "user_id": "user-1",
                "digest_sent_at": "2026-03-01T11:00:00Z",
                "alerts_count": 1,
                "price_drops": 1,
                "price_increases": 0,
                "currency_changes": 0,
                "email_status": "disabled",
                "webhook_status": "failed",
                "response_code": 503,
                "error_message": "HTTP 503: Service Unavailable",
            }
        ]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.get("/api/alerts/history")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 2
        items = data["alerts"]

        # Item 1: Delivered
        assert items[0]["delivered"] is True
        assert items[0]["delivered_status"] == "delivered"
        assert items[0]["response_code"] == 200
        assert items[0]["status_code"] == 200
        assert items[0]["timestamp"] is not None

        # Item 2: Failed
        assert items[1]["delivered"] is False
        assert items[1]["delivered_status"] == "failed"
        assert items[1]["response_code"] == 503
        assert "503" in items[1]["error_message"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 6. Background Webhook Dispatch & Scraper Tasks Tests
# ---------------------------------------------------------------------------

def test_dispatch_webhook_alert_task_delivers_and_records_history(monkeypatch):
    from app.tasks.scraper_tasks import dispatch_webhook_alert

    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_enabled": True,
            "webhook_url": "https://hooks.store.com/price-drops",
            "webhook_secret": "my-secret-key",
        }],
        "alert_history": [],
    })

    monkeypatch.setattr("app.tasks.scraper_tasks.get_supabase_client", lambda: db)
    mock_send = Mock(return_value={"success": True, "status_code": 200, "error": None})
    monkeypatch.setattr(WebhookService, "send_alert", mock_send)

    alert_event = {
        "event": "price_drop",
        "alert_id": "a-123",
        "old_price": 100.0,
        "new_price": 70.0,
        "product_id": "p-1",
    }

    result = dispatch_webhook_alert(user_id="user-1", alert_data=alert_event)
    assert result["success"] is True
    assert result["status_code"] == 200

    # Ensure alert_history entry was created
    assert len(db.tables["alert_history"]) == 1
    record = db.tables["alert_history"][0]
    assert record["webhook_status"] == "sent"
    assert record["response_code"] == 200
    assert record["price_drops"] == 1


def test_dispatch_webhook_alert_task_records_failure_on_remote_error(monkeypatch):
    from app.tasks.scraper_tasks import dispatch_webhook_alert

    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_enabled": True,
            "webhook_url": "https://hooks.store.com/price-drops",
            "webhook_secret": "my-secret-key",
        }],
        "alert_history": [],
    })

    monkeypatch.setattr("app.tasks.scraper_tasks.get_supabase_client", lambda: db)
    mock_send = Mock(return_value={"success": False, "status_code": 500, "error": "Internal Server Error"})
    monkeypatch.setattr(WebhookService, "send_alert", mock_send)

    alert_event = {
        "event": "price_drop",
        "alert_id": "a-124",
        "old_price": 50.0,
        "new_price": 30.0,
    }

    result = dispatch_webhook_alert(user_id="user-1", alert_data=alert_event)
    assert result["success"] is False
    assert result["status_code"] == 500

    # Verify failed history record
    assert len(db.tables["alert_history"]) == 1
    record = db.tables["alert_history"][0]
    assert record["webhook_status"] == "failed"
    assert record["response_code"] == 500


def test_api_check_price_drop_endpoint(monkeypatch):
    comp_id = "comp-api-1"
    user_id = "user-1"
    db = MockDatabaseClient({
        "competitors": [{
            "id": comp_id,
            "product_id": "p-1",
            "products": {"user_id": user_id}
        }]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db

    mock_alert_eval = AsyncMock(return_value={
        "alert_created": True,
        "alert_type": "price_drop",
        "change_percent": Decimal("-25.00"),
        "message": "Alert created: price_drop of 25.00%",
        "suppressed": False,
    })
    monkeypatch.setattr(AlertService, "check_price_change_and_alert", mock_alert_eval)

    try:
        client = TestClient(app)
        res = client.post(f"/api/alerts/check/{comp_id}", json={
            "price": 75.00,
            "currency": "USD",
            "threshold_percent": 20.0,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["alert_created"] is True
        assert data["alert_type"] == "price_drop"
        assert float(data["change_percent"]) == -25.0
        assert data["suppressed"] is False
    finally:
        app.dependency_overrides.clear()


def test_api_check_price_drop_not_found():
    db = MockDatabaseClient({"competitors": []})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.post("/api/alerts/check/comp-nonexistent", json={"price": 50.0})
        assert res.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_api_check_price_drop_forbidden_for_other_user():
    db = MockDatabaseClient({
        "competitors": [{
            "id": "comp-other",
            "product_id": "p-other",
            "products": {"user_id": "other-user-999"}
        }]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.post("/api/alerts/check/comp-other", json={"price": 50.0})
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_register_webhook_empty_secret_clears_secret():
    db = MockDatabaseClient({
        "user_alert_settings": [{
            "user_id": "user-1",
            "webhook_url": "https://hooks.store.com/alerts",
            "webhook_secret": "old-secret",
            "webhook_enabled": True,
        }]
    })
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        with patch.object(WebhookService, "_validate_url", return_value={"93.184.216.34"}):
            client = TestClient(app)
            res = client.post("/api/alerts/webhook", json={
                "webhook_url": "https://hooks.store.com/alerts",
                "webhook_secret": "   ",
                "enabled": True,
            })
            assert res.status_code == 200
            assert res.json()["webhook_secret_configured"] is False
    finally:
        app.dependency_overrides.clear()


def test_send_test_webhook_ssrf_rejected():
    db = MockDatabaseClient({"user_alert_settings": []})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="user-1", email="store@example.com", role="authenticated"
    )
    app.dependency_overrides[get_user_supabase_client] = lambda: db
    try:
        client = TestClient(app)
        res = client.post("/api/alerts/test-webhook", json={
            "webhook_url": "https://169.254.169.254/latest/meta-data",
        })
        assert res.status_code == 400
        assert "private or reserved" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()

