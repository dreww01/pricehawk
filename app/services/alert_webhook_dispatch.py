"""Nonblocking alert webhook dispatch with a local worker fallback."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class AlertWebhookDispatcher:
    def __init__(self, client_provider: Callable[[], Any]) -> None:
        self.client_provider = client_provider

    def dispatch_alert_webhook(
        self, user_id: str, alert_event: dict[str, Any], correlation_id: str | None = None
    ) -> None:
        """Queue delivery without blocking the scrape on the remote webhook."""
        try:
            from app.tasks.scraper_tasks import dispatch_webhook_alert

            dispatch_webhook_alert.delay(
                user_id=user_id, alert_data=alert_event, correlation_id=correlation_id
            )
        except Exception as exc:
            logger.debug(f"Celery task queue unavailable, dispatching via background worker: {exc}")
            threading.Thread(
                target=self._deliver_in_background,
                args=(user_id, alert_event, correlation_id),
                daemon=True,
            ).start()

    def _deliver_in_background(
        self, user_id: str, alert_event: dict[str, Any], correlation_id: str | None
    ) -> None:
        from app.services.webhook_service import WebhookService

        try:
            sb = self.client_provider()
            settings_res = (
                sb.table("user_alert_settings").select("*").eq("user_id", user_id).execute()
            )
            if not settings_res.data:
                return
            settings = settings_res.data[0]
            if not settings.get("webhook_enabled") or not settings.get("webhook_url"):
                return
            res = WebhookService().send_alert(
                webhook_url=settings["webhook_url"],
                payload=alert_event,
                webhook_secret=settings.get("webhook_secret"),
                correlation_id=correlation_id,
            )
            alert_id = alert_event.get("alert_id")
            alert_type = alert_event.get("alert_type") or alert_event.get("event")
            history_data = {
                "user_id": user_id,
                "digest_sent_at": datetime.now(UTC).isoformat(),
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
            except Exception as exc:
                logger.warning(f"Failed to record alert history: {exc}")
        except Exception as exc:
            logger.warning(f"Background webhook delivery failed: {exc}")
