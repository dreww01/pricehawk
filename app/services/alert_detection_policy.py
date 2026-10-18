"""Notification preferences and duplicate suppression for price alerts."""

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.alert_service import AlertConfig

logger = logging.getLogger(__name__)


def disabled_notification_message(settings: dict[str, Any], alert_type: str) -> str | None:
    if not settings:
        return None
    if not settings.get("email_enabled", True) and not settings.get("webhook_enabled", False):
        return "User has disabled alert notifications"
    if alert_type == "price_drop" and not settings.get("alert_price_drop", True):
        return "User has disabled price drop alerts"
    if alert_type == "price_increase" and not settings.get("alert_price_increase", True):
        return "User has disabled price increase alerts"
    return None


def duplicate_alert_message(
    sb: Any,
    competitor_id: str,
    alert_type: str,
    new_price: Decimal,
    threshold_percent: Decimal,
    config: "AlertConfig",
) -> str | None:
    """Fail open when the advisory duplicate lookup cannot be completed."""
    try:
        response = (
            sb.table("pending_alerts")
            .select("id, new_price, detected_at")
            .eq("competitor_id", competitor_id)
            .eq("alert_type", alert_type)
            .order("detected_at", desc=True)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        last_price = Decimal(str(response.data[0]["new_price"]))
        if alert_type == "price_drop":
            if new_price >= last_price:
                return f"Duplicate alert suppressed: price ${new_price} has not dropped below last alerted price (${last_price})"
            drop_from_last = ((last_price - new_price) / last_price) * 100
            drop_amount = last_price - new_price
            if (
                drop_from_last < threshold_percent
                and drop_amount < config.MIN_SIGNIFICANT_CHANGE_AMOUNT
            ):
                return f"Duplicate alert suppressed: price drop of {drop_from_last:.2f}% (${drop_amount:.2f}) since last alert (${last_price}) is below threshold"
        elif alert_type == "price_increase":
            if new_price <= last_price:
                return f"Duplicate alert suppressed: price ${new_price} has not increased above last alerted price (${last_price})"
            inc_from_last = ((new_price - last_price) / last_price) * 100
            inc_amount = new_price - last_price
            if (
                inc_from_last < threshold_percent
                and inc_amount < config.MIN_SIGNIFICANT_CHANGE_AMOUNT
            ):
                return f"Duplicate alert suppressed: price change of {inc_from_last:.2f}% (${inc_amount:.2f}) since last alert (${last_price}) is below threshold"
    except Exception as exc:
        logger.debug(f"Duplicate alert check failed for competitor {competitor_id}: {exc}")
    return None
