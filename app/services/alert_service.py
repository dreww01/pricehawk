"""Public service facade for price-change alerts and digest maintenance."""

from decimal import Decimal
from typing import Any

from app.core.logging import correlation_context
from app.db.database import get_supabase_client
from app.services.alert_detection import AlertDetector
from app.services.alert_maintenance import AlertMaintenance
from app.services.alert_webhook_dispatch import AlertWebhookDispatcher


class AlertConfig:
    """Centralized alert thresholds, queue limits, and retention settings."""

    MIN_SIGNIFICANT_CHANGE_AMOUNT = Decimal("5.00")
    MAX_PENDING_ALERTS_PER_USER = 100
    CLEANUP_PENDING_AFTER_DAYS = 7


class AlertService:
    """Detect changes, queue notifications, and manage pending digest alerts."""

    def __init__(self) -> None:
        self.config = AlertConfig()

    @staticmethod
    def _client_provider() -> Any:
        # Resolve at call time: service instances may outlive a provider override.
        return get_supabase_client()

    async def check_price_change_and_alert(
        self,
        competitor_id: str,
        new_price: Decimal | float,
        currency: str = "USD",
        correlation_id: str | None = None,
        custom_threshold: Decimal | float | None = None,
    ) -> dict[str, Any]:
        """Compare the latest successful price and create an eligible pending alert."""
        if correlation_id:
            with correlation_context(correlation_id):
                return await self._check_price_change_and_alert_impl(
                    competitor_id, new_price, currency, custom_threshold=custom_threshold
                )
        return await self._check_price_change_and_alert_impl(
            competitor_id, new_price, currency, custom_threshold=custom_threshold
        )

    async def _check_price_change_and_alert_impl(
        self,
        competitor_id: str,
        new_price: Decimal | float,
        currency: str = "USD",
        custom_threshold: Decimal | float | None = None,
    ) -> dict[str, Any]:
        detector = AlertDetector(self._client_provider, self.config, self.dispatch_alert_webhook)
        return await detector.check_price_change_and_alert(
            competitor_id, new_price, currency, custom_threshold=custom_threshold
        )

    def dispatch_alert_webhook(
        self,
        user_id: str,
        alert_event: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        """Dispatch outgoing webhook delivery without waiting for the remote server."""
        AlertWebhookDispatcher(self._client_provider).dispatch_alert_webhook(
            user_id, alert_event, correlation_id
        )

    async def get_pending_alerts_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return pending digest alerts with product and competitor details."""
        return await AlertMaintenance(
            self._client_provider, self.config
        ).get_pending_alerts_for_user(user_id)

    async def mark_alerts_as_included(self, alert_ids: list[str]) -> bool:
        """Mark the given alert IDs as included in a digest."""
        return await AlertMaintenance(self._client_provider, self.config).mark_alerts_as_included(
            alert_ids
        )

    async def cleanup_old_pending_alerts(self) -> int:
        """Delete included alerts older than the configured retention period."""
        return await AlertMaintenance(
            self._client_provider, self.config
        ).cleanup_old_pending_alerts()

    async def get_users_due_for_digest(self) -> list[dict[str, Any]]:
        """Return email-enabled users with pending alerts and a due digest."""
        return await AlertMaintenance(self._client_provider, self.config).get_users_due_for_digest()
