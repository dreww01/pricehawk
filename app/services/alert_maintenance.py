"""Pending-alert queries, digest scheduling, and retention maintenance."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.alert_service import AlertConfig

logger = logging.getLogger(__name__)


class AlertMaintenance:
    def __init__(self, client_provider: Callable[[], Any], config: "AlertConfig") -> None:
        self.client_provider = client_provider
        self.config = config

    async def get_pending_alerts_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return pending alerts with product and competitor display details."""
        try:
            sb = self.client_provider()
            response = (
                sb.table("pending_alerts")
                .select(
                    "id, alert_type, old_price, new_price, price_change_percent, detected_at, "
                    "products(product_name), competitors(retailer_name, url)"
                )
                .eq("user_id", user_id)
                .eq("included_in_digest", False)
                .order("detected_at", desc=True)
                .execute()
            )
            alerts = []
            for row in response.data:
                alerts.append(
                    {
                        "id": row["id"],
                        "product_name": row["products"]["product_name"],
                        "competitor_name": row["competitors"]["retailer_name"] or "Unknown Store",
                        "alert_type": row["alert_type"],
                        "old_price": Decimal(str(row["old_price"])),
                        "new_price": Decimal(str(row["new_price"])),
                        "price_change_percent": Decimal(str(row["price_change_percent"])),
                        "currency": "USD",
                        "detected_at": row["detected_at"],
                    }
                )
            return alerts
        except Exception as exc:
            logger.error(f"Error fetching pending alerts: {exc}")
            return []

    async def mark_alerts_as_included(self, alert_ids: list[str]) -> bool:
        """Mark pending alerts as included in a digest."""
        try:
            sb = self.client_provider()
            sb.table("pending_alerts").update({"included_in_digest": True}).in_(
                "id", alert_ids
            ).execute()
            return True
        except Exception as exc:
            logger.error(f"Error marking alerts as included: {exc}")
            return False

    async def cleanup_old_pending_alerts(self) -> int:
        """Delete included alerts older than the configured retention period."""
        try:
            sb = self.client_provider()
            cutoff_date = datetime.now() - timedelta(days=self.config.CLEANUP_PENDING_AFTER_DAYS)
            response = (
                sb.table("pending_alerts")
                .delete()
                .eq("included_in_digest", True)
                .lt("detected_at", cutoff_date.isoformat())
                .execute()
            )
            return len(response.data) if response.data else 0
        except Exception as exc:
            logger.error(f"Error cleaning up old alerts: {exc}")
            return 0

    async def get_users_due_for_digest(self) -> list[dict[str, Any]]:
        """Return users with enabled email, a due digest, and pending alerts."""
        try:
            sb = self.client_provider()
            now = datetime.now()
            settings_response = (
                sb.table("user_alert_settings")
                .select("user_id, digest_frequency_hours, last_digest_sent_at, email_enabled")
                .eq("email_enabled", True)
                .execute()
            )
            users_due = []
            for setting in settings_response.data:
                user_id = setting["user_id"]
                frequency_hours = setting.get("digest_frequency_hours", 24)
                last_sent = setting.get("last_digest_sent_at")
                if last_sent:
                    last_sent_dt = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
                    time_since_last = now - last_sent_dt
                    if time_since_last.total_seconds() < (frequency_hours * 3600):
                        continue
                pending_response = (
                    sb.table("pending_alerts")
                    .select("id", count="exact")
                    .eq("user_id", user_id)
                    .eq("included_in_digest", False)
                    .execute()
                )
                pending_count = pending_response.count or 0
                if pending_count == 0:
                    continue
                user_response = (
                    sb.table("auth.users").select("email").eq("id", user_id).single().execute()
                )
                if user_response.data:
                    users_due.append(
                        {
                            "user_id": user_id,
                            "email": user_response.data.get("email"),
                            "digest_frequency_hours": frequency_hours,
                            "pending_count": pending_count,
                        }
                    )
            return users_due
        except Exception as exc:
            logger.error(f"Error getting users due for digest: {exc}")
            return []
