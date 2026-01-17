"""
Alert detection service for price changes.

Detects price changes after scraping and stores them as pending alerts
for later inclusion in digest emails.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db.database import get_supabase_client

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION BLOCK - Easy to tweak
# =============================================================================
class AlertConfig:
    """
    Centralized alert configuration for easy adjustments.
    Modify these values to change alert behavior.
    """
    # Minimum price change to trigger alert (even if below threshold)
    # This catches significant absolute changes on low-threshold products
    MIN_SIGNIFICANT_CHANGE_AMOUNT = Decimal("5.00")  # $5 minimum change

    # Maximum alerts per user to store (prevents database bloat)
    MAX_PENDING_ALERTS_PER_USER = 100

    # Auto-cleanup old pending alerts after this many days
    CLEANUP_PENDING_AFTER_DAYS = 7


# =============================================================================
# Alert Detection Service
# =============================================================================
class AlertService:
    """Service for detecting price changes and creating pending alerts."""

    def __init__(self):
        self.config = AlertConfig()

    async def check_price_change_and_alert(
        self,
        competitor_id: str,
        new_price: Decimal,
        currency: str = "USD"
    ) -> dict[str, Any]:
        """
        Check if price changed beyond threshold and create pending alert.

        This is called after each successful scrape.

        Args:
            competitor_id: UUID of competitor
            new_price: Newly scraped price
            currency: Currency code

        Returns:
            dict with keys:
                - alert_created (bool): Whether alert was created
                - alert_type (str | None): 'price_drop' or 'price_increase'
                - change_percent (Decimal | None): Percentage change
                - message (str): Status message
        """
        try:
            sb = get_supabase_client()  # Use service key

            # Fetch competitor info including product and user
            comp_response = (
                sb.table("competitors")
                .select("id, url, retailer_name, alert_threshold_percent, product_id, products(id, product_name, user_id)")
                .eq("id", competitor_id)
                .single()
                .execute()
            )

            if not comp_response.data:
                return {
                    "alert_created": False,
                    "alert_type": None,
                    "change_percent": None,
                    "message": "Competitor not found"
                }

            competitor = comp_response.data
            product = competitor["products"]
            user_id = product["user_id"]
            threshold_percent = Decimal(str(competitor["alert_threshold_percent"]))

            # Fetch previous price (most recent successful scrape)
            prev_response = (
                sb.table("price_history")
                .select("price, currency")
                .eq("competitor_id", competitor_id)
                .eq("scrape_status", "success")
                .not_.is_("price", "null")
                .order("scraped_at", desc=True)
                .limit(2)  # Get last 2 to skip the just-inserted one
                .execute()
            )

            # If less than 2 records, this is first scrape - no alert
            if not prev_response.data or len(prev_response.data) < 2:
                return {
                    "alert_created": False,
                    "alert_type": None,
                    "change_percent": None,
                    "message": "No previous price to compare (first scrape)"
                }

            # Get the second-to-last price (previous price before this scrape)
            old_price = Decimal(str(prev_response.data[1]["price"]))
            old_currency = prev_response.data[1].get("currency", "USD")

            # Currency mismatch check - create currency_changed alert instead of price alert
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
                    "detected_at": datetime.now().isoformat()
                }
                sb.table("pending_alerts").insert(alert_data).execute()

                logger.warning(
                    f"Currency mismatch for competitor {competitor_id}: "
                    f"{old_currency} → {currency}"
                )

                return {
                    "alert_created": True,
                    "alert_type": "currency_changed",
                    "change_percent": None,
                    "message": f"Currency changed: {old_currency} → {currency}"
                }

            # Calculate change
            if old_price == 0:
                return {
                    "alert_created": False,
                    "alert_type": None,
                    "change_percent": None,
                    "message": "Previous price was zero"
                }

            change_amount = new_price - old_price
            change_percent = (change_amount / old_price) * 100

            # Determine alert type
            if change_percent <= -threshold_percent:
                alert_type = "price_drop"
            elif change_percent >= threshold_percent:
                alert_type = "price_increase"
            else:
                # Check for significant absolute change
                if abs(change_amount) >= self.config.MIN_SIGNIFICANT_CHANGE_AMOUNT:
                    alert_type = "price_drop" if change_amount < 0 else "price_increase"
                else:
                    return {
                        "alert_created": False,
                        "alert_type": None,
                        "change_percent": round(change_percent, 2),
                        "message": f"Price change ({change_percent:.2f}%) below threshold ({threshold_percent}%)"
                    }

            # Check user alert settings
            settings_response = (
                sb.table("user_alert_settings")
                .select("email_enabled, alert_price_drop, alert_price_increase")
                .eq("user_id", user_id)
                .execute()
            )

            if settings_response.data:
                settings = settings_response.data[0]
                if not settings.get("email_enabled", True):
                    return {
                        "alert_created": False,
                        "alert_type": alert_type,
                        "change_percent": round(change_percent, 2),
                        "message": "User has disabled email alerts"
                    }

                if alert_type == "price_drop" and not settings.get("alert_price_drop", True):
                    return {
                        "alert_created": False,
                        "alert_type": alert_type,
                        "change_percent": round(change_percent, 2),
                        "message": "User has disabled price drop alerts"
                    }

                if alert_type == "price_increase" and not settings.get("alert_price_increase", True):
                    return {
                        "alert_created": False,
                        "alert_type": alert_type,
                        "change_percent": round(change_percent, 2),
                        "message": "User has disabled price increase alerts"
                    }

            # Check if user has too many pending alerts (rate limiting)
            count_response = (
                sb.table("pending_alerts")
                .select("id", count="exact")
                .eq("user_id", user_id)
                .eq("included_in_digest", False)
                .execute()
            )

            pending_count = count_response.count or 0
            if pending_count >= self.config.MAX_PENDING_ALERTS_PER_USER:
                return {
                    "alert_created": False,
                    "alert_type": alert_type,
                    "change_percent": round(change_percent, 2),
                    "message": f"User has too many pending alerts ({pending_count})"
                }

            # Create pending alert
            alert_data = {
                "user_id": user_id,
                "product_id": product["id"],
                "competitor_id": competitor_id,
                "alert_type": alert_type,
                "old_price": float(old_price),
                "new_price": float(new_price),
                "price_change_percent": float(change_percent),
                "threshold_percent": float(threshold_percent),
                "detected_at": datetime.now().isoformat()
            }

            sb.table("pending_alerts").insert(alert_data).execute()

            return {
                "alert_created": True,
                "alert_type": alert_type,
                "change_percent": round(change_percent, 2),
                "message": f"Alert created: {alert_type} of {abs(change_percent):.2f}%"
            }

        except Exception as e:
            return {
                "alert_created": False,
                "alert_type": None,
                "change_percent": None,
                "message": f"Error checking price change: {str(e)}"
            }

    async def get_pending_alerts_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """
        Get all pending alerts for a user that haven't been included in a digest.

        Returns:
            List of alert dicts with product and competitor details
        """
        try:
            sb = get_supabase_client()

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
                alerts.append({
                    "id": row["id"],
                    "product_name": row["products"]["product_name"],
                    "competitor_name": row["competitors"]["retailer_name"] or "Unknown Store",
                    "alert_type": row["alert_type"],
                    "old_price": Decimal(str(row["old_price"])),
                    "new_price": Decimal(str(row["new_price"])),
                    "price_change_percent": Decimal(str(row["price_change_percent"])),
                    "currency": "USD",
                    "detected_at": row["detected_at"]
                })

            return alerts

        except Exception as e:
            logger.error(f"Error fetching pending alerts: {e}")
            return []

    async def mark_alerts_as_included(self, alert_ids: list[str]) -> bool:
        """
        Mark pending alerts as included in a digest.

        Args:
            alert_ids: List of alert UUIDs

        Returns:
            bool: Success status
        """
        try:
            sb = get_supabase_client()

            sb.table("pending_alerts").update({
                "included_in_digest": True
            }).in_("id", alert_ids).execute()

            return True

        except Exception as e:
            logger.error(f"Error marking alerts as included: {e}")
            return False

    async def cleanup_old_pending_alerts(self) -> int:
        """
        Clean up old pending alerts that are already included in digests.

        Returns:
            int: Number of alerts deleted
        """
        try:
            from datetime import timedelta

            sb = get_supabase_client()
            cutoff_date = datetime.now() - timedelta(days=self.config.CLEANUP_PENDING_AFTER_DAYS)

            # Delete included alerts older than cutoff
            response = (
                sb.table("pending_alerts")
                .delete()
                .eq("included_in_digest", True)
                .lt("detected_at", cutoff_date.isoformat())
                .execute()
            )

            return len(response.data) if response.data else 0

        except Exception as e:
            logger.error(f"Error cleaning up old alerts: {e}")
            return 0

    async def get_users_due_for_digest(self) -> list[dict[str, Any]]:
        """
        Get list of users who have pending alerts and are due for a digest.

        Returns:
            List of dicts with keys: user_id, email, digest_frequency_hours, pending_count
        """
        try:
            from datetime import timedelta

            sb = get_supabase_client()
            now = datetime.now()

            # Get all users with alert settings
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

                # Check if due for digest
                if last_sent:
                    last_sent_dt = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
                    time_since_last = now - last_sent_dt
                    if time_since_last.total_seconds() < (frequency_hours * 3600):
                        continue  # Not due yet

                # Check if user has pending alerts
                pending_response = (
                    sb.table("pending_alerts")
                    .select("id", count="exact")
                    .eq("user_id", user_id)
                    .eq("included_in_digest", False)
                    .execute()
                )

                pending_count = pending_response.count or 0
                if pending_count == 0:
                    continue  # No alerts to send

                # Get user email from auth.users
                user_response = (
                    sb.table("auth.users")
                    .select("email")
                    .eq("id", user_id)
                    .single()
                    .execute()
                )

                if user_response.data:
                    users_due.append({
                        "user_id": user_id,
                        "email": user_response.data.get("email"),
                        "digest_frequency_hours": frequency_hours,
                        "pending_count": pending_count
                    })

            return users_due

        except Exception as e:
            logger.error(f"Error getting users due for digest: {e}")
            return []
