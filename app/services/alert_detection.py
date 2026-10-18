"""Detect and persist price/currency changes after successful scrapes."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.core.logging import get_correlation_id
from app.services.alert_detection_policy import (
    disabled_notification_message,
    duplicate_alert_message,
)

if TYPE_CHECKING:
    from app.services.alert_service import AlertConfig

logger = logging.getLogger(__name__)


def _result(
    message: str, alert_type: str | None = None, change: Decimal | None = None
) -> dict[str, Any]:
    return {
        "alert_created": False,
        "alert_type": alert_type,
        "change_percent": round(change, 2) if change is not None else None,
        "message": message,
    }


def _invalidate_cache(user_id: str) -> None:
    try:
        from app.services.dashboard_cache import invalidate_dashboard_cache

        invalidate_dashboard_cache(user_id)
    except Exception as exc:
        logger.debug(f"Failed to invalidate cache after alert creation: {exc}")


class AlertDetector:
    """Own detection orchestration, with database and delivery supplied by the service."""

    def __init__(
        self,
        client_provider: Callable[[], Any],
        config: "AlertConfig",
        dispatch: Callable[..., None],
    ) -> None:
        self.client_provider = client_provider
        self.config = config
        self.dispatch = dispatch

    async def check_price_change_and_alert(
        self,
        competitor_id: str,
        new_price: Decimal | float,
        currency: str = "USD",
        custom_threshold: Decimal | float | None = None,
    ) -> dict[str, Any]:
        try:
            new_price = Decimal(str(new_price))
            sb = self.client_provider()
            comp_response = (
                sb.table("competitors")
                .select(
                    "id, url, retailer_name, alert_threshold_percent, product_id, products(id, product_name, user_id)"
                )
                .eq("id", competitor_id)
                .single()
                .execute()
            )
            if not comp_response.data:
                return _result("Competitor not found")
            competitor = (
                comp_response.data[0]
                if isinstance(comp_response.data, list)
                else comp_response.data
            )
            product = competitor["products"]
            user_id = product["user_id"]
            threshold_percent = abs(
                Decimal(
                    str(
                        custom_threshold
                        if custom_threshold is not None
                        else competitor["alert_threshold_percent"]
                    )
                )
            )
            prev_response = (
                sb.table("price_history")
                .select("price, currency")
                .eq("competitor_id", competitor_id)
                .eq("scrape_status", "success")
                .not_.is_("price", "null")
                .order("scraped_at", desc=True)
                .limit(2)
                .execute()
            )
            if not prev_response.data:
                return _result("No previous price to compare (first scrape)")
            # Scrapers may already have inserted the new price before invoking detection.
            if Decimal(str(prev_response.data[0]["price"])) == new_price:
                if len(prev_response.data) < 2:
                    return _result("No previous price to compare (first scrape)")
                old_record = prev_response.data[1]
            else:
                old_record = prev_response.data[0]
            old_price = Decimal(str(old_record["price"]))
            old_currency = old_record.get("currency", "USD")
            if old_currency != currency:
                alert_data = {
                    "user_id": user_id,
                    "product_id": product["id"],
                    "competitor_id": competitor_id,
                    "alert_type": "currency_changed",
                    "old_price": float(old_price),
                    "new_price": float(new_price),
                    "old_currency": old_currency,
                    "new_currency": currency,
                    "detected_at": datetime.now(UTC).isoformat(),
                }
                sb.table("pending_alerts").insert(alert_data).execute()
                _invalidate_cache(user_id)
                logger.warning(
                    f"Currency mismatch for competitor {competitor_id}: {old_currency} → {currency}"
                )
                return {
                    "alert_created": True,
                    "alert_type": "currency_changed",
                    "change_percent": None,
                    "message": f"Currency changed: {old_currency} → {currency}",
                }
            if old_price == 0:
                return _result("Previous price was zero")
            change_amount = new_price - old_price
            change_percent = (change_amount / old_price) * 100
            if change_percent <= -threshold_percent:
                alert_type = "price_drop"
            elif change_percent >= threshold_percent:
                alert_type = "price_increase"
            elif (
                custom_threshold is None
                and abs(change_amount) >= self.config.MIN_SIGNIFICANT_CHANGE_AMOUNT
            ):
                alert_type = "price_drop" if change_amount < 0 else "price_increase"
            else:
                return _result(
                    f"Price change ({change_percent:.2f}%) below threshold ({threshold_percent}%)",
                    change=change_percent,
                )
            settings_response = (
                sb.table("user_alert_settings")
                .select(
                    "email_enabled, alert_price_drop, alert_price_increase, webhook_enabled, webhook_url, webhook_secret"
                )
                .eq("user_id", user_id)
                .execute()
            )
            settings = settings_response.data[0] if settings_response.data else {}
            disabled_message = disabled_notification_message(settings, alert_type)
            if disabled_message:
                return _result(disabled_message, alert_type, change_percent)
            duplicate_message = duplicate_alert_message(
                sb, competitor_id, alert_type, new_price, threshold_percent, self.config
            )
            if duplicate_message:
                return {
                    **_result(duplicate_message, alert_type, change_percent),
                    "suppressed": True,
                }
            count_response = (
                sb.table("pending_alerts")
                .select("id", count="exact")
                .eq("user_id", user_id)
                .eq("included_in_digest", False)
                .execute()
            )
            pending_count = count_response.count or 0
            if pending_count >= self.config.MAX_PENDING_ALERTS_PER_USER:
                return _result(
                    f"User has too many pending alerts ({pending_count})",
                    alert_type,
                    change_percent,
                )
            alert_id = str(uuid4())
            alert_data = {
                "id": alert_id,
                "user_id": user_id,
                "product_id": product["id"],
                "competitor_id": competitor_id,
                "alert_type": alert_type,
                "old_price": float(old_price),
                "new_price": float(new_price),
                "price_change_percent": float(change_percent),
                "threshold_percent": float(threshold_percent),
                "detected_at": datetime.now(UTC).isoformat(),
            }
            sb.table("pending_alerts").insert(alert_data).execute()
            _invalidate_cache(user_id)
            webhook_dispatched = False
            cid = get_correlation_id()
            if settings and settings.get("webhook_enabled") and settings.get("webhook_url"):
                alert_event = {
                    "event": "price_drop" if alert_type == "price_drop" else "price_alert",
                    "event_type": alert_type,
                    "alert_id": alert_id,
                    "user_id": user_id,
                    "product_id": product["id"],
                    "product_name": product.get("product_name"),
                    "competitor_id": competitor_id,
                    "retailer_name": competitor.get("retailer_name"),
                    "competitor_url": competitor.get("url"),
                    "old_price": float(old_price),
                    "new_price": float(new_price),
                    "price_change_percent": float(change_percent),
                    "threshold_percent": float(threshold_percent),
                    "currency": currency,
                    "detected_at": alert_data["detected_at"],
                    "timestamp": alert_data["detected_at"],
                }
                self.dispatch(user_id=user_id, alert_event=alert_event, correlation_id=cid)
                webhook_dispatched = True
            return {
                "alert_created": True,
                "alert_id": alert_id,
                "alert_type": alert_type,
                "change_percent": round(change_percent, 2),
                "webhook_dispatched": webhook_dispatched,
                "message": f"Alert created: {alert_type} of {abs(change_percent):.2f}%",
            }
        except Exception as exc:
            logger.exception("Error checking price change for competitor %s", competitor_id)
            return _result(f"Error checking price change: {str(exc)}")
