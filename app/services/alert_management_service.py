"""Authorized alert commands used by the HTTP presentation layer."""

from collections.abc import Callable
from typing import Any

from supabase import Client

from app.db.models import CheckPriceDropRequest
from app.services.alert_errors import AlertOperationError
from app.services.alert_service import AlertService
from app.services.dashboard_cache import invalidate_dashboard_cache


class AlertManagementService:
    """Check ownership before any privileged alert mutation."""

    def __init__(self, client: Client, user_id: str, service_client: Callable[[], Client]) -> None:
        self.client = client
        self.user_id = user_id
        self.service_client = service_client

    def _require_competitor(self, competitor_id: str, message: str) -> None:
        response = (
            self.client.table("competitors")
            .select("id, product_id, products(user_id)")
            .eq("id", competitor_id)
            .execute()
        )
        if not response.data:
            raise AlertOperationError("Competitor not found", 404)
        if (response.data[0].get("products") or {}).get("user_id") != self.user_id:
            raise AlertOperationError(message, 403)

    async def check_price(
        self, competitor_id: str, body: CheckPriceDropRequest, correlation_id: str | None
    ) -> dict[str, Any]:
        self._require_competitor(competitor_id, "Not authorized for this competitor")
        threshold = (
            body.threshold_percent if body.threshold_percent is not None else body.target_percentage
        )
        return await AlertService().check_price_change_and_alert(
            competitor_id=competitor_id,
            new_price=body.price,
            currency=body.currency,
            correlation_id=correlation_id,
            custom_threshold=threshold,
        )

    def accept_currency(self, competitor_id: str, currency: str) -> dict[str, Any]:
        self._require_competitor(competitor_id, "Not authorized to modify this competitor")
        client = self.service_client()
        client.table("competitors").update({"expected_currency": currency}).eq(
            "id", competitor_id
        ).execute()
        invalidate_dashboard_cache(self.user_id)
        try:
            client.table("pending_alerts").update({"included_in_digest": True}).eq(
                "competitor_id", competitor_id
            ).eq("alert_type", "currency_changed").execute()
        finally:
            invalidate_dashboard_cache(self.user_id)
        return dict(
            success=True,
            message=f"Now tracking prices in {currency}",
            competitor_id=competitor_id,
            new_currency=currency,
        )

    def accept_all_currencies(self) -> dict[str, Any]:
        response = (
            self.client.table("pending_alerts")
            .select("id, competitor_id, new_currency")
            .eq("user_id", self.user_id)
            .eq("alert_type", "currency_changed")
            .eq("included_in_digest", False)
            .execute()
        )
        if not response.data:
            return dict(success=True, message="No pending currency changes", updated_count=0)
        client = self.service_client()
        updated_count = 0
        currency_updated = False
        try:
            for alert in response.data:
                if not alert["new_currency"]:
                    continue
                client.table("competitors").update({"expected_currency": alert["new_currency"]}).eq(
                    "id", alert["competitor_id"]
                ).execute()
                currency_updated = True
                client.table("pending_alerts").update({"included_in_digest": True}).eq(
                    "id", alert["id"]
                ).execute()
                updated_count += 1
        finally:
            # A committed currency update must invalidate even if dismissal fails.
            if currency_updated:
                invalidate_dashboard_cache(self.user_id)
        return dict(
            success=True,
            message=f"Accepted {updated_count} currency changes",
            updated_count=updated_count,
        )
