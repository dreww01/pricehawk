"""Manual digest, email, and webhook delivery operations."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from supabase import Client

from app.db.models import TestWebhookRequest
from app.services.alert_errors import AlertOperationError
from app.services.digest_service import DigestService
from app.services.email_service import EmailService
from app.services.webhook_service import WebhookDeliveryError, WebhookService

logger = logging.getLogger(__name__)


def run_digest(
    user_id: str,
    email: str | None,
    role: str,
    force: bool,
    dry_run: bool,
    correlation_id: str | None,
    factory: Callable[[], DigestService],
) -> dict[str, Any]:
    if force and role not in {"admin", "service_role"}:
        raise AlertOperationError("Only administrators may force a digest run", 403)
    return factory().run_for_user(
        user_id, email, force=force, dry_run=dry_run, correlation_id=correlation_id
    )


def send_test_email(email: str | None, factory: Callable[[], EmailService]) -> dict[str, Any]:
    if not email:
        raise AlertOperationError("No email address available")
    result = factory().send_test_email(email)
    if not result["success"]:
        logger.error("Failed to send test email to %s: %s", email, result.get("error"))
        raise AlertOperationError(
            "Failed to send test email. Please check your email configuration.", 500
        )
    return dict(success=True, message="Test email sent successfully", email=email)


def send_test_webhook(
    client: Client,
    service_client: Callable[[], Client],
    user_id: str,
    body: TestWebhookRequest | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    url = body.webhook_url if body else None
    secret = body.webhook_secret if body else None
    if not url:
        response = (
            client.table("user_alert_settings")
            .select("webhook_url, webhook_secret, webhook_enabled")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if response.data:
            url = response.data[0].get("webhook_url")
            if secret is None:
                secret = response.data[0].get("webhook_secret")
    if not url:
        raise AlertOperationError("No webhook URL configured or provided")
    try:
        result = WebhookService().send_test_ping(
            webhook_url=url,
            webhook_secret=secret,
            correlation_id=correlation_id,
            extra_data={"user_id": user_id},
        )
    except WebhookDeliveryError as exc:
        raise AlertOperationError(str(exc)) from exc
    audit_client = service_client()
    record = dict(
        user_id=user_id,
        digest_sent_at=datetime.now(timezone.utc).isoformat(),
        alerts_count=1,
        price_drops=0,
        price_increases=0,
        currency_changes=0,
        email_status="disabled",
        webhook_status="sent" if result.get("success") else "failed",
        response_code=result.get("status_code"),
        error_message=result.get("error"),
        alert_ids=[],
    )
    try:
        audit_client.table("alert_history").insert(record).execute()
    except Exception as exc:
        logger.warning("Failed to insert alert_history audit row: %s", exc)
    success = bool(result.get("success"))
    return dict(
        success=success,
        message=(
            "Test webhook ping sent successfully"
            if success
            else f"Test webhook delivery failed: {result.get('error')}"
        ),
        status_code=result.get("status_code"),
        response_code=result.get("status_code"),
        error=None if success else result.get("error"),
    )
