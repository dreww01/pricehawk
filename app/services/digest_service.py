"""Price alert digest orchestration shared by API and Celery."""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.db.database import get_supabase_client
from app.services.email_service import EmailService
from app.services.webhook_service import WebhookDeliveryError, WebhookService

logger = logging.getLogger(__name__)

MAX_ALERTS_PER_DIGEST = 50


class DigestService:
    """Claim, summarize, deliver, and persist one user's pending alerts."""

    def __init__(
        self,
        email_service: EmailService | None = None,
        webhook_service: WebhookService | None = None,
    ) -> None:
        self.client = get_supabase_client()
        self.email_service = email_service or EmailService()
        self.webhook_service = webhook_service or WebhookService()

    def run_for_user(
        self,
        user_id: str,
        email: str | None,
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        settings = self._get_settings(user_id)
        if not settings:
            return self._empty_result(user_id, dry_run, "alert_settings_not_found")

        existing_claim = self._get_in_flight_claim(user_id) if not dry_run else None
        if not force and not existing_claim and not self._is_due(settings):
            return self._empty_result(user_id, dry_run, "frequency_window_not_elapsed")

        if existing_claim:
            digest_id = str(existing_claim["digest_id"])
            alerts = existing_claim["alerts"]
        else:
            digest_id = str(uuid4())
            alerts = self._load_alerts(user_id, digest_id, dry_run)

        if not alerts:
            return self._empty_result(user_id, dry_run, "no_pending_alerts")

        summary = self.build_summary(alerts)
        result = {
            "user_id": user_id,
            "status": "dry_run" if dry_run else "sent",
            "alerts_count": len(alerts),
            **summary["counts"],
            "email_sent": False,
            "webhook_sent": False,
            "dry_run": dry_run,
            "skipped_reason": None,
        }
        if dry_run:
            return result

        email_already_sent = False
        webhook_already_sent = False
        if existing_claim:
            history = self._get_digest_history(digest_id)
            if history:
                email_already_sent = history.get("email_status") == "sent"
                webhook_already_sent = history.get("webhook_status") == "sent"

        email_enabled = bool(settings.get("email_enabled"))
        webhook_enabled = bool(settings.get("webhook_enabled"))
        errors: list[str] = []

        if email_enabled:
            if email_already_sent:
                result["email_sent"] = True
            elif not email:
                errors.append("No recipient email is available")
            else:
                email_result = self.email_service.send_price_alert_digest(
                    to_email=email,
                    user_name=email.split("@", 1)[0],
                    alerts=alerts,
                    digest_period_hours=int(settings.get("digest_frequency_hours") or 24),
                )
                result["email_sent"] = bool(email_result.get("success"))
                if not result["email_sent"]:
                    errors.append(f"Email: {email_result.get('error', 'delivery failed')}")

        if webhook_enabled:
            if webhook_already_sent:
                result["webhook_sent"] = True
            else:
                webhook_url = settings.get("webhook_url")
                webhook_secret = settings.get("webhook_secret")
                if not webhook_url or not webhook_secret:
                    errors.append("Webhook: URL and secret must be configured")
                else:
                    payload = self.build_webhook_payload(digest_id, user_id, alerts, summary)
                    try:
                        webhook_result = self.webhook_service.send_digest(
                            webhook_url, webhook_secret, payload
                        )
                    except WebhookDeliveryError as exc:
                        webhook_result = {"success": False, "error": str(exc)}
                    result["webhook_sent"] = bool(webhook_result.get("success"))
                    if not result["webhook_sent"]:
                        errors.append(
                            f"Webhook: {webhook_result.get('error', 'delivery failed')}"
                        )

        if not email_enabled and not webhook_enabled:
            errors.append("No notification channel is enabled")

        success = not errors
        result["status"] = "sent" if success else "failed"
        self._finalize(
            digest_id,
            user_id,
            alerts,
            summary,
            result,
            errors,
            success,
            email_enabled=email_enabled,
            webhook_enabled=webhook_enabled,
        )
        return result

    @staticmethod
    def build_summary(alerts: list[dict[str, Any]]) -> dict[str, Any]:
        drops = [a for a in alerts if a.get("alert_type") == "price_drop"]
        increases = [a for a in alerts if a.get("alert_type") == "price_increase"]
        currency_changes = [
            a for a in alerts if a.get("alert_type") == "currency_changed"
        ]
        biggest_drops = sorted(
            drops,
            key=lambda alert: abs(Decimal(str(alert.get("price_change_percent") or 0))),
            reverse=True,
        )[:5]
        return {
            "counts": {
                "price_drops": len(drops),
                "price_increases": len(increases),
                "currency_changes": len(currency_changes),
            },
            "biggest_price_drops": [
                {
                    "alert_id": alert["id"],
                    "product_name": alert["product_name"],
                    "competitor_name": alert["competitor_name"],
                    "price_change_percent": float(
                        Decimal(str(alert.get("price_change_percent") or 0))
                    ),
                    "old_price": alert.get("old_price"),
                    "new_price": alert.get("new_price"),
                }
                for alert in biggest_drops
            ],
        }

    @staticmethod
    def build_webhook_payload(
        digest_id: str,
        user_id: str,
        alerts: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "event": "price_alert.digest",
            "version": "1.0",
            "digest_id": digest_id,
            "user_id": user_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "alerts": [
                {
                    "id": alert["id"],
                    "product_name": alert["product_name"],
                    "competitor_name": alert["competitor_name"],
                    "competitor_url": alert.get("competitor_url"),
                    "alert_type": alert["alert_type"],
                    "old_price": alert.get("old_price"),
                    "new_price": alert.get("new_price"),
                    "price_change_percent": alert.get("price_change_percent"),
                    "old_currency": alert.get("old_currency"),
                    "new_currency": alert.get("new_currency"),
                    "detected_at": alert["detected_at"],
                }
                for alert in alerts
            ],
        }

    def _get_settings(self, user_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("user_alert_settings")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    @staticmethod
    def _is_due(settings: dict[str, Any]) -> bool:
        last_sent = settings.get("last_digest_sent_at")
        if not last_sent:
            return True
        sent_at = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00"))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        frequency = timedelta(hours=int(settings.get("digest_frequency_hours") or 24))
        return datetime.now(timezone.utc) - sent_at >= frequency

    def _load_alerts(
        self, user_id: str, digest_id: str, dry_run: bool
    ) -> list[dict[str, Any]]:
        if dry_run:
            response = (
                self.client.table("pending_alerts")
                .select(
                    "id, alert_type, old_price, new_price, price_change_percent, "
                    "old_currency, new_currency, detected_at, products(product_name), "
                    "competitors(retailer_name, url)"
                )
                .eq("user_id", user_id)
                .eq("included_in_digest", False)
                .is_("processing_digest_id", "null")
                .order("detected_at")
                .limit(MAX_ALERTS_PER_DIGEST)
                .execute()
            )
        else:
            response = self.client.rpc(
                "claim_pending_alerts",
                {
                    "p_user_id": user_id,
                    "p_digest_id": digest_id,
                    "p_limit": MAX_ALERTS_PER_DIGEST,
                },
            ).execute()
        return [self._normalize_alert(row) for row in (response.data or [])]

    @staticmethod
    def _normalize_alert(row: dict[str, Any]) -> dict[str, Any]:
        product = row.get("products") or {}
        competitor = row.get("competitors") or {}
        return {
            "id": row["id"],
            "product_name": row.get("product_name") or product.get("product_name") or "Unknown Product",
            "competitor_name": row.get("competitor_name") or competitor.get("retailer_name") or "Unknown Store",
            "competitor_url": row.get("competitor_url") or competitor.get("url"),
            "alert_type": row["alert_type"],
            "old_price": row.get("old_price"),
            "new_price": row.get("new_price"),
            "price_change_percent": row.get("price_change_percent"),
            "old_currency": row.get("old_currency"),
            "new_currency": row.get("new_currency"),
            "currency": row.get("new_currency") or row.get("old_currency") or "USD",
            "detected_at": row["detected_at"],
        }

    def _get_in_flight_claim(self, user_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("pending_alerts")
            .select(
                "id, alert_type, old_price, new_price, price_change_percent, "
                "old_currency, new_currency, detected_at, processing_digest_id, "
                "products(product_name), competitors(retailer_name, url)"
            )
            .eq("user_id", user_id)
            .eq("included_in_digest", False)
            .not_.is_("processing_digest_id", "null")
            .order("detected_at")
            .limit(MAX_ALERTS_PER_DIGEST)
            .execute()
        )
        if not response.data:
            return None
        digest_id = response.data[0].get("processing_digest_id")
        if not digest_id:
            return None
        return {
            "digest_id": digest_id,
            "alerts": [self._normalize_alert(row) for row in response.data],
        }

    def _get_digest_history(self, digest_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("alert_history")
            .select("email_status, webhook_status")
            .eq("id", digest_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    def _finalize(
        self,
        digest_id: str,
        user_id: str,
        alerts: list[dict[str, Any]],
        summary: dict[str, Any],
        result: dict[str, Any],
        errors: list[str],
        success: bool,
        email_enabled: bool = True,
        webhook_enabled: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        counts = summary["counts"]
        email_status = (
            "sent" if result["email_sent"]
            else ("failed" if email_enabled else "disabled")
        )
        webhook_status = (
            "sent" if result["webhook_sent"]
            else ("failed" if webhook_enabled else "disabled")
        )
        self.client.table("alert_history").upsert(
            {
                "id": digest_id,
                "user_id": user_id,
                "digest_sent_at": now,
                "alerts_count": len(alerts),
                **counts,
                "email_status": email_status,
                "webhook_status": webhook_status,
                "error_message": "; ".join(errors)[:1000] or None,
                "alert_ids": [alert["id"] for alert in alerts],
            }
        ).execute()

        if success:
            self.client.table("pending_alerts").update(
                {"included_in_digest": True, "processing_digest_id": None}
            ).eq("processing_digest_id", digest_id).execute()
            self.client.table("user_alert_settings").update(
                {"last_digest_sent_at": now}
            ).eq("user_id", user_id).execute()
        else:
            any_channel_succeeded = result["email_sent"] or result["webhook_sent"]
            if not any_channel_succeeded:
                self.client.table("pending_alerts").update(
                    {"processing_digest_id": None}
                ).eq("processing_digest_id", digest_id).execute()

    @staticmethod
    def _empty_result(
        user_id: str, dry_run: bool, reason: str
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "status": "skipped",
            "alerts_count": 0,
            "price_drops": 0,
            "price_increases": 0,
            "currency_changes": 0,
            "email_sent": False,
            "webhook_sent": False,
            "dry_run": dry_run,
            "skipped_reason": reason,
        }
