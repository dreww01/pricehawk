"""Regression coverage for extracted alert commands and read models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.db.models import CheckPriceDropRequest
from app.services.alert_errors import AlertOperationError
from app.services.alert_management_service import AlertManagementService
from app.services.alert_query_service import AlertQueryService


class Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.payload = None
        self.filters = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.client.limit = value
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def execute(self):
        self.client.calls.append((self.table, self.payload, self.filters))
        if self.payload is not None and self.table == self.client.fail_update:
            raise RuntimeError("database unavailable")
        return SimpleNamespace(data=self.client.rows.get(self.table))


class Database:
    def __init__(self, **rows):
        self.rows = rows
        self.calls = []
        self.fail_update = None
        self.limit = None

    def table(self, name):
        return Query(self, name)


@pytest.mark.parametrize(
    "records,code,message",
    [
        ([], 404, "Competitor not found"),
        ([{"products": {"user_id": "other"}}], 403, "Not authorized to modify this competitor"),
        ([{"products": None}], 403, "Not authorized to modify this competitor"),
    ],
)
def test_currency_acceptance_requires_ownership(records, code, message):
    privileged = Mock()
    service = AlertManagementService(Database(competitors=records), "owner", privileged)
    with pytest.raises(AlertOperationError, match=message) as error:
        service.accept_currency("competitor", "EUR")
    assert error.value.status_code == code
    privileged.assert_not_called()


def test_currency_acceptance_dismisses_only_currency_alerts(monkeypatch):
    user_db = Database(competitors=[{"products": {"user_id": "owner"}}])
    service_db = Database()
    invalidate = Mock()
    monkeypatch.setattr(
        "app.services.alert_management_service.invalidate_dashboard_cache", invalidate
    )
    result = AlertManagementService(user_db, "owner", lambda: service_db).accept_currency(
        "c1", "EUR"
    )
    assert result == {
        "success": True,
        "message": "Now tracking prices in EUR",
        "competitor_id": "c1",
        "new_currency": "EUR",
    }
    assert service_db.calls == [
        ("competitors", {"expected_currency": "EUR"}, [("id", "c1")]),
        (
            "pending_alerts",
            {"included_in_digest": True},
            [("competitor_id", "c1"), ("alert_type", "currency_changed")],
        ),
    ]
    invalidate.assert_called_with("owner")


@pytest.mark.parametrize("bulk", [False, True])
def test_currency_write_invalidates_cache_even_when_alert_dismissal_fails(monkeypatch, bulk):
    user_db = Database(
        competitors=[{"products": {"user_id": "owner"}}],
        pending_alerts=[{"id": "a1", "competitor_id": "c1", "new_currency": "EUR"}],
    )
    service_db = Database()
    service_db.fail_update = "pending_alerts"
    invalidate = Mock()
    monkeypatch.setattr(
        "app.services.alert_management_service.invalidate_dashboard_cache", invalidate
    )
    service = AlertManagementService(user_db, "owner", lambda: service_db)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.accept_all_currencies() if bulk else service.accept_currency("c1", "EUR")
    invalidate.assert_called_with("owner")


def test_bulk_currency_acceptance_skips_missing_currency_and_scopes_pending_query(monkeypatch):
    user_db = Database(
        pending_alerts=[
            {"id": "a1", "competitor_id": "c1", "new_currency": None},
            {"id": "a2", "competitor_id": "c2", "new_currency": "GBP"},
        ]
    )
    service_db = Database()
    monkeypatch.setattr("app.services.alert_management_service.invalidate_dashboard_cache", Mock())
    result = AlertManagementService(user_db, "owner", lambda: service_db).accept_all_currencies()
    assert result == {"success": True, "message": "Accepted 1 currency changes", "updated_count": 1}
    assert user_db.calls[0][2] == [
        ("user_id", "owner"),
        ("alert_type", "currency_changed"),
        ("included_in_digest", False),
    ]
    assert service_db.calls == [
        ("competitors", {"expected_currency": "GBP"}, [("id", "c2")]),
        ("pending_alerts", {"included_in_digest": True}, [("id", "a2")]),
    ]


def test_no_pending_currency_changes_do_not_acquire_privileged_client():
    privileged = Mock()
    result = AlertManagementService(
        Database(pending_alerts=[]), "owner", privileged
    ).accept_all_currencies()
    assert result == {"success": True, "message": "No pending currency changes", "updated_count": 0}
    privileged.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "threshold,target,expected", [(0, 20, 0), (None, 20, 20), (None, None, None)]
)
async def test_manual_check_preserves_threshold_precedence(
    monkeypatch, threshold, target, expected
):
    evaluator = AsyncMock(return_value={"alert_created": False, "message": "below threshold"})
    monkeypatch.setattr(
        "app.services.alert_management_service.AlertService.check_price_change_and_alert", evaluator
    )
    service = AlertManagementService(
        Database(competitors=[{"products": {"user_id": "owner"}}]), "owner", Mock()
    )
    result = await service.check_price(
        "c1",
        CheckPriceDropRequest(price=10, threshold_percent=threshold, target_percentage=target),
        "trace",
    )
    assert result == {"alert_created": False, "message": "below threshold"}
    assert evaluator.call_args.kwargs["custom_threshold"] == expected
    assert evaluator.call_args.kwargs["correlation_id"] == "trace"


@pytest.mark.parametrize(
    "email,webhook,status,delivered",
    [
        ("sent", "failed", "delivered", True),
        ("failed", "sent", "delivered", True),
        ("failed", "disabled", "failed", False),
        ("disabled", "disabled", "disabled", False),
    ],
)
def test_history_delivery_outcome_and_status_aliases(email, webhook, status, delivered):
    database = Database(
        alert_history=[
            {
                "id": "h1",
                "digest_sent_at": "2026-01-01T00:00:00Z",
                "alerts_count": 1,
                "email_status": email,
                "webhook_status": webhook,
                "response_code": 202,
            }
        ]
    )
    result = AlertQueryService(database, "owner").history(7)
    assert result["total"] == 1
    row = result["alerts"][0]
    assert row["delivered"] is delivered
    assert row["delivered_status"] == status
    assert row["response_code"] == row["status_code"] == 202
    assert row["timestamp"] == row["digest_sent_at"]
    assert database.limit == 7
    assert database.calls[0][2] == [("user_id", "owner")]


def test_empty_read_models_accept_null_database_results():
    service = AlertQueryService(Database(), "owner")
    assert service.pending() == {"alerts": [], "total": 0}
    assert service.history(20) == {"alerts": [], "total": 0}
