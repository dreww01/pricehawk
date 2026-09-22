"""
Alert detection service for price changes.

Detects price changes after scraping and stores them as pending alerts
for later inclusion in digest emails.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.db.database import get_supabase_client
from app.core.logging import correlation_context, get_correlation_id

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
        currency: str = "USD",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Check if price changed beyond threshold and create pending alert.

        This is called after each successful scrape.

        Args:
            competitor_id: UUID of competitor
            new_price: Newly scraped price
            currency: Currency code
            correlation_id: Optional correlation ID for tracing

        Returns:
            dict with keys:
                - alert_created (bool): Whether alert was created
                - alert_type (str | None): 'price_drop' or 'price_increase'
                - change_percent (Decimal | None): Percentage change
                - message (str): Status message
        """
        if correlation_id:
            with correlation_context(correlation_id):
                return await self._check_price_change_and_alert_impl(competitor_id, new_price, currency)
        return await self._check_price_change_and_alert_impl(competitor_id, new_price, currency)

    async def _check_price_change_and_alert_impl(
        self,
        competitor_id: str,
        new_price: Decimal,
        currency: str = "USD",
    ) -> dict[str, Any]:
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

                try:
                    from app.services.dashboard_cache import invalidate_dashboard_cache
                    invalidate_dashboard_cache(user_id)
                except Exception as exc:
                    logger.debug(f"Failed to invalidate cache after alert creation: {exc}")

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
                .select("email_enabled, alert_price_drop, alert_price_increase, webhook_enabled, webhook_url, webhook_secret")
                .eq("user_id", user_id)
                .execute()
            )

            settings = settings_response.data[0] if settings_response.data else {}

            if settings:
                email_enabled = settings.get("email_enabled", True)
                webhook_enabled = settings.get("webhook_enabled", False)
                if not email_enabled and not webhook_enabled:
                    return {
                        "alert_created": False,
                        "alert_type": alert_type,
                        "change_percent": round(change_percent, 2),
                        "message": "User has disabled alert notifications"
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

            # Prevent alert fatigue: suppress duplicate notifications if price has not meaningfully changed since last alert
            try:
                last_alert_response = (
                    sb.table("pending_alerts")
                    .select("id, new_price, detected_at")
                    .eq("competitor_id", competitor_id)
                    .eq("alert_type", alert_type)
                    .order("detected_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if last_alert_response.data and len(last_alert_response.data) > 0:
                    last_alert = last_alert_response.data[0]
                    last_price = Decimal(str(last_alert["new_price"]))
                    if alert_type == "price_drop":
                        if new_price >= last_price:
                            return {
                                "alert_created": False,
                                "alert_type": alert_type,
                                "change_percent": round(change_percent, 2),
                                "message": f"Duplicate alert suppressed: price ${new_price} has not dropped below last alerted price (${last_price})",
                                "suppressed": True,
                            }
                        drop_from_last = ((last_price - new_price) / last_price) * 100
                        drop_amount = last_price - new_price
                        if drop_from_last < threshold_percent and drop_amount < self.config.MIN_SIGNIFICANT_CHANGE_AMOUNT:
                            return {
                                "alert_created": False,
                                "alert_type": alert_type,
                                "change_percent": round(change_percent, 2),
                                "message": f"Duplicate alert suppressed: price drop of {drop_from_last:.2f}% (${drop_amount:.2f}) since last alert (${last_price}) is below threshold",
                                "suppressed": True,
                            }
                    elif alert_type == "price_increase":
                        if new_price <= last_price:
                            return {
                                "alert_created": False,
                                "alert_type": alert_type,
                                "change_percent": round(change_percent, 2),
                                "message": f"Duplicate alert suppressed: price ${new_price} has not increased above last alerted price (${last_price})",
                                "suppressed": True,
                            }
                        inc_from_last = ((new_price - last_price) / last_price) * 100
                        inc_amount = new_price - last_price
                        if inc_from_last < threshold_percent and inc_amount < self.config.MIN_SIGNIFICANT_CHANGE_AMOUNT:
                            return {
                                "alert_created": False,
                                "alert_type": alert_type,
                                "change_percent": round(change_percent, 2),
                                "message": f"Duplicate alert suppressed: price change of {inc_from_last:.2f}% (${inc_amount:.2f}) since last alert (${last_price}) is below threshold",
                                "suppressed": True,
                            }
            except Exception as dup_exc:
                logger.debug(f"Duplicate alert check failed for competitor {competitor_id}: {dup_exc}")

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
                "detected_at": datetime.now(timezone.utc).isoformat()
            }

            sb.table("pending_alerts").insert(alert_data).execute()

            try:
                from app.services.dashboard_cache import invalidate_dashboard_cache
                invalidate_dashboard_cache(user_id)
            except Exception as exc:
                logger.debug(f"Failed to invalidate cache after alert creation: {exc}")

            # Dispatch outgoing webhook notification in background
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
                self.dispatch_alert_webhook(user_id=user_id, alert_event=alert_event, correlation_id=cid)
                webhook_dispatched = True

            return {
                "alert_created": True,
                "alert_id": alert_id,
                "alert_type": alert_type,
                "change_percent": round(change_percent, 2),
                "webhook_dispatched": webhook_dispatched,
                "message": f"Alert created: {alert_type} of {abs(change_percent):.2f}%"
            }

        except Exception as e:
            return {
                "alert_created": False,
                "alert_type": None,
                "change_percent": None,
                "message": f"Error checking price change: {str(e)}"
            }

    def dispatch_alert_webhook(
        self,
        user_id: str,
        alert_event: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        """
        Dispatch outgoing webhook notification in the background.
        Ensures scraping is never blocked or slowed down.
        """
        try:
            from app.tasks.scraper_tasks import dispatch_webhook_alert
            dispatch_webhook_alert.delay(user_id=user_id, alert_data=alert_event, correlation_id=correlation_id)
        except Exception as exc:
            logger.debug(f"Celery task queue unavailable, dispatching via background worker: {exc}")
            import threading
            from app.services.webhook_service import WebhookService

            def _background_worker():
                try:
                    sb = get_supabase_client()
                    settings_res = sb.table("user_alert_settings").select("*").eq("user_id", user_id).execute()
                    if not settings_res.data:
                        return
                    settings = settings_res.data[0]
                    if not settings.get("webhook_enabled") or not settings.get("webhook_url"):
                        return

                    wh_service = WebhookService()
                    res = wh_service.send_alert(
                        webhook_url=settings["webhook_url"],
                        payload=alert_event,
                        webhook_secret=settings.get("webhook_secret"),
                        correlation_id=correlation_id,
                    )

                    alert_id = alert_event.get("alert_id")
                    alert_type = alert_event.get("alert_type") or alert_event.get("event")
                    history_data = {
                        "user_id": user_id,
                        "digest_sent_at": datetime.now(timezone.utc).isoformat(),
                        "alerts_count": 1,
                        "price_drops": 1 if alert_type == "price_drop" else 0,
                        "price_increases": 1 if alert_type == "price_increase" else 0,
                        "currency_changes": 1 if alert_type == "currency_changed" else 0,
                        "email_status": "disabled",
                        "webhook_status": "sent" if res.get("success") else "failed",
                        "response_code": res.get("status_code"),
                        "error_message": res.get("error"),
                        "alert_ids": [alert_id] if alert_id else [],
                    }
                    try:
                        sb.table("alert_history").insert(history_data).execute()
                    except Exception as h_err:
                        logger.warning(f"Failed to record alert history: {h_err}")
                except Exception as bg_err:
                    logger.warning(f"Background webhook delivery failed: {bg_err}")

            threading.Thread(target=_background_worker, daemon=True).start()

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
