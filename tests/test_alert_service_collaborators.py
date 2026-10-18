"""Regression coverage for the alert facade's injected collaborator dependencies."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.logging import get_correlation_id
from app.services.alert_service import AlertConfig, AlertService
from app.services.webhook_service import WebhookService


class Query:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.operations: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def operation(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return operation

    def execute(self) -> SimpleNamespace:
        return self.response


@pytest.mark.asyncio
async def test_maintenance_resolves_facade_provider_after_service_construction(monkeypatch):
    service = AlertService()
    row = {
        "id": "alert-1",
        "products": {"product_name": "Widget"},
        "competitors": {"retailer_name": None},
        "alert_type": "price_drop",
        "old_price": 100,
        "new_price": 80,
        "price_change_percent": -20,
        "detected_at": "2026-01-01T00:00:00Z",
    }
    query = Query(SimpleNamespace(data=[row]))
    db = Mock()
    db.table.return_value = query
    provider = Mock(return_value=db)
    monkeypatch.setattr("app.services.alert_service.get_supabase_client", provider)

    assert await service.get_pending_alerts_for_user("user-1") == [
        {
            "id": "alert-1",
            "product_name": "Widget",
            "competitor_name": "Unknown Store",
            "alert_type": "price_drop",
            "old_price": Decimal("100"),
            "new_price": Decimal("80"),
            "price_change_percent": Decimal("-20"),
            "currency": "USD",
            "detected_at": row["detected_at"],
        }
    ]
    assert await service.mark_alerts_as_included(["alert-1"]) is True
    service.config.CLEANUP_PENDING_AFTER_DAYS = 2
    assert await service.cleanup_old_pending_alerts() == 1
    assert provider.call_count == 3
    assert ("in_", ("id", ["alert-1"]), {}) in query.operations
    assert ("eq", ("included_in_digest", True), {}) in query.operations
    assert isinstance(service.config, AlertConfig)


@pytest.mark.asyncio
async def test_maintenance_preserves_failure_return_values(monkeypatch):
    service = AlertService()
    monkeypatch.setattr(
        "app.services.alert_service.get_supabase_client", Mock(side_effect=RuntimeError("offline"))
    )
    assert await service.get_pending_alerts_for_user("user-1") == []
    assert await service.mark_alerts_as_included(["alert-1"]) is False
    assert await service.cleanup_old_pending_alerts() == 0
    assert await service.get_users_due_for_digest() == []


@pytest.mark.asyncio
async def test_detection_preserves_correlation_scope_and_error_contract(monkeypatch):
    service = AlertService()
    before = get_correlation_id()
    observed = []

    def fail_provider():
        observed.append(get_correlation_id())
        raise RuntimeError("offline")

    monkeypatch.setattr("app.services.alert_service.get_supabase_client", fail_provider)
    result = await service.check_price_change_and_alert(
        "comp-1", Decimal("80"), correlation_id="trace-1"
    )
    assert result == {
        "alert_created": False,
        "alert_type": None,
        "change_percent": None,
        "message": "Error checking price change: offline",
    }
    assert observed == ["trace-1"]
    assert get_correlation_id() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_price,old_currency,new_currency,expected",
    [
        (0, "USD", "USD", "Previous price was zero"),
        (100, "USD", "EUR", "Currency changed: USD → EUR"),
    ],
)
async def test_detection_currency_and_zero_price_contract(
    monkeypatch, old_price, old_currency, new_currency, expected
):
    service = AlertService()
    competitor = {
        "alert_threshold_percent": 10,
        "products": {"id": "product-1", "user_id": "user-1"},
    }
    queries = {
        "competitors": Query(SimpleNamespace(data=competitor)),
        "price_history": Query(
            SimpleNamespace(data=[{"price": old_price, "currency": old_currency}])
        ),
        "pending_alerts": Query(SimpleNamespace(data=[])),
    }
    # Supabase's not_ filter is a property rather than a method.
    queries["price_history"].not_ = queries["price_history"]
    db = Mock()
    db.table.side_effect = queries.__getitem__
    monkeypatch.setattr("app.services.alert_service.get_supabase_client", lambda: db)
    result = await service.check_price_change_and_alert("comp-1", Decimal("80"), new_currency)
    currency_changed = old_currency != new_currency
    assert result == {
        "alert_created": currency_changed,
        "alert_type": "currency_changed" if currency_changed else None,
        "change_percent": None,
        "message": expected,
    }
    inserts = [
        args[0] for name, args, _ in queries["pending_alerts"].operations if name == "insert"
    ]
    assert len(inserts) == int(currency_changed)
    if currency_changed:
        assert inserts[0]["old_currency"] == old_currency
        assert inserts[0]["new_currency"] == new_currency


@pytest.mark.parametrize("success,status", [(True, 204), (False, 503)])
def test_webhook_fallback_uses_facade_provider_and_records_delivery(monkeypatch, success, status):
    from app.tasks.scraper_tasks import dispatch_webhook_alert

    service = AlertService()
    settings = {
        "webhook_enabled": True,
        "webhook_url": "https://example.com/hook",
        "webhook_secret": "secret",
    }
    settings_query = Query(SimpleNamespace(data=[settings]))
    history_query = Query(SimpleNamespace(data=[]))
    db = Mock()
    db.table.side_effect = lambda table: (
        settings_query if table == "user_alert_settings" else history_query
    )
    provider = Mock(return_value=db)
    monkeypatch.setattr("app.services.alert_service.get_supabase_client", provider)
    monkeypatch.setattr(
        dispatch_webhook_alert, "delay", Mock(side_effect=RuntimeError("queue offline"))
    )
    send = Mock(
        return_value={
            "success": success,
            "status_code": status,
            "error": None if success else "unavailable",
        }
    )
    monkeypatch.setattr(WebhookService, "send_alert", send)

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            assert daemon is True
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("app.services.alert_webhook_dispatch.threading.Thread", ImmediateThread)
    event = {"event": "price_drop", "alert_id": "alert-1"}
    service.dispatch_alert_webhook("user-1", event, "trace-1")
    provider.assert_called_once_with()
    send.assert_called_once_with(
        webhook_url=settings["webhook_url"],
        payload=event,
        webhook_secret="secret",
        correlation_id="trace-1",
    )
    history = next(args[0] for name, args, _ in history_query.operations if name == "insert")
    assert history["webhook_status"] == ("sent" if success else "failed")
    assert history["response_code"] == status
    assert history["price_drops"] == 1
    assert history["alert_ids"] == ["alert-1"]
