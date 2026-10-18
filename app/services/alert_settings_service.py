"""User-scoped alert preferences and webhook configuration persistence."""

from typing import Any, cast

from supabase import Client

from app.db.models import AlertSettingsUpdate, WebhookRegisterRequest
from app.services.alert_errors import AlertOperationError
from app.services.dashboard_cache import invalidate_dashboard_cache
from app.services.webhook_service import WebhookDeliveryError, WebhookService


def default_settings(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "email_enabled": True,
        "digest_frequency_hours": 24,
        "alert_price_drop": True,
        "alert_price_increase": True,
    }


class AlertSettingsService:
    """Persist settings using the caller's RLS-scoped database client."""

    def __init__(self, client: Client, user_id: str) -> None:
        self.client = client
        self.user_id = user_id

    def get(self) -> dict[str, Any]:
        response = (
            self.client.table("user_alert_settings")
            .select("*")
            .eq("user_id", self.user_id)
            .execute()
        )
        data = cast(list[dict[str, Any]], response.data or [])
        if data:
            return data[0]
        response = (
            self.client.table("user_alert_settings")
            .insert(default_settings(self.user_id))
            .execute()
        )
        inserted = cast(list[dict[str, Any]], response.data or [])
        if not inserted:
            raise AlertOperationError("Failed to create default settings", 500)
        return inserted[0]

    def update(self, updates: AlertSettingsUpdate) -> dict[str, Any]:
        if updates.webhook_enabled is True:
            existing: dict[str, Any] = {}
            if not updates.webhook_url or not updates.webhook_secret:
                response = (
                    self.client.table("user_alert_settings")
                    .select("webhook_url, webhook_secret")
                    .eq("user_id", self.user_id)
                    .limit(1)
                    .execute()
                )
                data = cast(list[dict[str, Any]], response.data or [])
                existing = data[0] if data else {}
            if not (updates.webhook_url or existing.get("webhook_url")):
                raise AlertOperationError("webhook_url is required when enabling webhooks")
            secret = updates.webhook_secret or existing.get("webhook_secret")
            if not secret or len(secret) < 16:
                raise AlertOperationError(
                    "webhook_secret (at least 16 characters) is required when enabling webhooks"
                )
        payload = updates.model_dump(exclude_none=True)
        if not payload:
            raise AlertOperationError("No fields to update")
        saved = self._save(payload)
        if not saved:
            raise AlertOperationError("Failed to update settings", 500)
        return saved

    def _save(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = (
            self.client.table("user_alert_settings")
            .update(payload)
            .eq("user_id", self.user_id)
            .execute()
        )
        data = cast(list[dict[str, Any]], response.data or [])
        if not data:
            response = (
                self.client.table("user_alert_settings")
                .insert({**default_settings(self.user_id), **payload})
                .execute()
            )
            data = cast(list[dict[str, Any]], response.data or [])
        return data[0] if data else {}

    def get_webhook(self) -> dict[str, Any]:
        response = (
            self.client.table("user_alert_settings")
            .select("webhook_url, webhook_enabled, webhook_secret")
            .eq("user_id", self.user_id)
            .limit(1)
            .execute()
        )
        data = cast(list[dict[str, Any]], response.data or [])
        record = data[0] if data else {}
        return self._webhook_response(
            record,
            "Webhook configuration loaded" if data else "No webhook configuration found",
        )

    def register_webhook(self, body: WebhookRegisterRequest) -> dict[str, Any]:
        try:
            WebhookService._validate_url(body.webhook_url)
        except WebhookDeliveryError as exc:
            raise AlertOperationError(str(exc)) from exc
        payload = {"webhook_url": body.webhook_url, "webhook_enabled": body.enabled}
        if body.webhook_secret is not None:
            payload["webhook_secret"] = body.webhook_secret.strip() or None
        saved = self._save(payload)
        invalidate_dashboard_cache(self.user_id)
        return self._webhook_response(saved or payload, "Webhook configuration saved successfully")

    def delete_webhook(self) -> dict[str, Any]:
        self.client.table("user_alert_settings").update(
            {
                "webhook_enabled": False,
                "webhook_url": None,
                "webhook_secret": None,
            }
        ).eq("user_id", self.user_id).execute()
        invalidate_dashboard_cache(self.user_id)
        return self._webhook_response({}, "Webhook endpoint deleted successfully")

    @staticmethod
    def _webhook_response(record: dict[str, Any], message: str) -> dict[str, Any]:
        return {
            "webhook_url": record.get("webhook_url"),
            "webhook_enabled": bool(record.get("webhook_enabled")),
            "webhook_secret_configured": bool(record.get("webhook_secret")),
            "message": message,
        }
