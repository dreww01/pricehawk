"""
API endpoints for alert management.

Handles user alert settings, pending alerts, alert history, and test emails.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from supabase import Client

from app.core.flash import flash
from app.core.logging import get_correlation_id
from app.core.security import get_current_user, CurrentUser
from app.db.database import get_user_supabase_client, get_supabase_client
from app.middleware.rate_limit import limiter, API_RATE_LIMIT
from app.db.models import (
    AcceptAllCurrenciesResponse,
    AcceptCurrencyResponse,
    AlertHistoryListResponse,
    AlertHistoryResponse,
    AlertSettingsResponse,
    AlertSettingsUpdate,
    CheckPriceDropRequest,
    CheckPriceDropResponse,
    DigestRunRequest,
    DigestRunResponse,
    ErrorEnvelope,
    PendingAlertResponse,
    PendingAlertsListResponse,
    TestEmailRequest,
    TestEmailResponse,
    TestWebhookRequest,
    TestWebhookResponse,
    WebhookConfigResponse,
    WebhookRegisterRequest,
)
from app.services.alert_service import AlertService
from app.services.email_service import EmailService
from app.services.digest_service import DigestService
from app.services.webhook_service import WebhookService, WebhookDeliveryError
from app.services.dashboard_cache import invalidate_dashboard_cache


class AcceptCurrencyRequest(BaseModel):
    """Request to accept a new currency for a competitor."""
    currency: str

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


def _settings_response(settings: dict) -> AlertSettingsResponse:
    return AlertSettingsResponse(
        user_id=settings["user_id"],
        email_enabled=settings.get("email_enabled", True),
        digest_frequency_hours=settings.get("digest_frequency_hours", 24),
        alert_price_drop=settings.get("alert_price_drop", True),
        alert_price_increase=settings.get("alert_price_increase", True),
        webhook_enabled=settings.get("webhook_enabled", False),
        webhook_url=settings.get("webhook_url"),
        webhook_secret_configured=bool(settings.get("webhook_secret")),
        last_digest_sent_at=settings.get("last_digest_sent_at"),
        created_at=settings["created_at"],
        updated_at=settings["updated_at"],
    )


@router.get("/settings", response_model=AlertSettingsResponse)
async def get_alert_settings(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get user's alert notification settings.

    If settings don't exist, creates default settings.
    """
    try:
        # Try to fetch existing settings
        response = (
            sb.table("user_alert_settings")
            .select("*")
            .eq("user_id", current_user.id)
            .execute()
        )

        if response.data:
            settings = response.data[0]
            return _settings_response(settings)

        # Create default settings if none exist
        default_settings = {
            "user_id": current_user.id,
            "email_enabled": True,
            "digest_frequency_hours": 24,
            "alert_price_drop": True,
            "alert_price_increase": True
        }

        create_response = (
            sb.table("user_alert_settings")
            .insert(default_settings)
            .execute()
        )

        if create_response.data:
            settings = create_response.data[0]
            return _settings_response(settings)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create default settings"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get alert settings for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load alert settings"
        )


@router.put("/settings", response_model=AlertSettingsResponse)
async def update_alert_settings(
    updates: AlertSettingsUpdate,
    http_response: Response,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Update user's alert notification settings.

    Only provided fields will be updated.
    """
    try:
        if updates.webhook_enabled is True:
            needs_existing_url = not updates.webhook_url
            needs_existing_secret = not updates.webhook_secret

            existing_record = None
            if needs_existing_url or needs_existing_secret:
                existing = (
                    sb.table("user_alert_settings")
                    .select("webhook_url, webhook_secret")
                    .eq("user_id", current_user.id)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    existing_record = existing.data[0]

            effective_url = updates.webhook_url or (
                existing_record.get("webhook_url") if existing_record else None
            )
            effective_secret = updates.webhook_secret or (
                existing_record.get("webhook_secret") if existing_record else None
            )

            if not effective_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="webhook_url is required when enabling webhooks",
                )
            if not effective_secret or len(effective_secret) < 16:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="webhook_secret (at least 16 characters) is required when enabling webhooks",
                )

        # Build update dict (only include non-None fields)
        update_data = {}
        if updates.email_enabled is not None:
            update_data["email_enabled"] = updates.email_enabled
        if updates.digest_frequency_hours is not None:
            update_data["digest_frequency_hours"] = updates.digest_frequency_hours
        if updates.alert_price_drop is not None:
            update_data["alert_price_drop"] = updates.alert_price_drop
        if updates.alert_price_increase is not None:
            update_data["alert_price_increase"] = updates.alert_price_increase
        if updates.webhook_enabled is not None:
            update_data["webhook_enabled"] = updates.webhook_enabled
        if updates.webhook_url is not None:
            update_data["webhook_url"] = updates.webhook_url
        if updates.webhook_secret is not None:
            update_data["webhook_secret"] = updates.webhook_secret

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        # Update settings
        response = (
            sb.table("user_alert_settings")
            .update(update_data)
            .eq("user_id", current_user.id)
            .execute()
        )

        # If no rows updated, settings don't exist - create them
        if not response.data:
            default_settings = {
                "user_id": current_user.id,
                "email_enabled": True,
                "digest_frequency_hours": 24,
                "alert_price_drop": True,
                "alert_price_increase": True,
                **update_data
            }

            response = (
                sb.table("user_alert_settings")
                .insert(default_settings)
                .execute()
            )

        if response.data:
            settings = response.data[0]
            flash(http_response, "Alert settings saved successfully.", "success")
            return _settings_response(settings)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update settings"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update alert settings for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save alert settings"
        )


@router.get("/pending", response_model=PendingAlertsListResponse)
async def get_pending_alerts(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get user's pending alerts that haven't been sent in a digest yet.
    """
    try:
        response = (
            sb.table("pending_alerts")
            .select(
                "id, alert_type, old_price, new_price, price_change_percent, detected_at, "
                "old_currency, new_currency, product_id, competitor_id, "
                "products(product_name), competitors(retailer_name, url)"
            )
            .eq("user_id", current_user.id)
            .eq("included_in_digest", False)
            .order("detected_at", desc=True)
            .execute()
        )

        alerts = [
            PendingAlertResponse(
                id=row["id"],
                product_id=row["product_id"],
                product_name=row["products"]["product_name"],
                competitor_id=row["competitor_id"],
                competitor_url=row["competitors"]["url"],
                alert_type=row["alert_type"],
                old_price=row["old_price"],
                new_price=row["new_price"],
                price_change_percent=row["price_change_percent"],
                old_currency=row.get("old_currency"),
                new_currency=row.get("new_currency"),
                created_at=row["detected_at"]
            )
            for row in response.data
        ]

        return PendingAlertsListResponse(alerts=alerts, total=len(alerts))

    except Exception as e:
        logger.exception(f"Failed to get pending alerts for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load pending alerts"
        )


@router.get("/history", response_model=AlertHistoryListResponse)
async def get_alert_history(
    limit: int = Query(default=20, ge=1, le=100),
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get user's alert history (sent digest emails).
    """
    try:
        response = (
            sb.table("alert_history")
            .select(
                "*"
            )
            .eq("user_id", current_user.id)
            .order("digest_sent_at", desc=True)
            .limit(limit)
            .execute()
        )

        history = []
        for row in response.data or []:
            sent_at = row["digest_sent_at"]
            resp_code = row.get("response_code")
            wh_status = row.get("webhook_status", "disabled")
            em_status = row.get("email_status", "disabled")
            is_delivered = wh_status == "sent" or em_status == "sent"
            del_status = (
                "delivered"
                if is_delivered
                else ("failed" if (wh_status == "failed" or em_status == "failed") else "disabled")
            )
            history.append(
                AlertHistoryResponse(
                    id=row["id"],
                    digest_sent_at=sent_at,
                    timestamp=sent_at,
                    alerts_count=row["alerts_count"],
                    price_drops=row.get("price_drops", 0),
                    price_increases=row.get("price_increases", 0),
                    currency_changes=row.get("currency_changes", 0),
                    email_status=em_status,
                    webhook_status=wh_status,
                    delivered=is_delivered,
                    delivered_status=del_status,
                    response_code=resp_code,
                    status_code=resp_code,
                    error_message=row.get("error_message"),
                )
            )

        return AlertHistoryListResponse(alerts=history, total=len(history))

    except Exception as e:
        logger.exception(f"Failed to get alert history for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load alert history"
        )


@router.post("/digests/run", response_model=DigestRunResponse)
async def run_alert_digest(
    request: DigestRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Run the current user's digest, optionally forcing or previewing it."""
    try:
        if request.force and current_user.role not in {"admin", "service_role"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators may force a digest run",
            )
        cid = get_correlation_id()
        return DigestRunResponse(
            **DigestService().run_for_user(
                current_user.id,
                current_user.email,
                force=request.force,
                dry_run=request.dry_run,
                correlation_id=cid,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to run digest for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to run alert digest",
        ) from exc


@router.post(
    "/test",
    response_model=TestEmailResponse,
    summary="Send test email",
    description="Send a test email to verify email configuration. If email is not provided, sends to user's registered email.",
    responses={
        200: {"model": TestEmailResponse, "description": "Test email sent successfully"},
        400: {"model": ErrorEnvelope, "description": "No email address available"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
        500: {"model": ErrorEnvelope, "description": "Failed to send test email"},
    },
)
async def send_test_email(
    request: TestEmailRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user)
) -> TestEmailResponse:
    """
    Send a test email to verify email configuration.

    If email is not provided, sends to user's registered email (from JWT).
    """
    try:
        target_email = request.email if request else None

        if not target_email:
            target_email = current_user.email

        if not target_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No email address available"
            )

        email_service = EmailService()
        logger.info(f"Sending test email to: {target_email}")
        result = email_service.send_test_email(target_email)
        logger.info(f"Email result: {result}")

        if result["success"]:
            return TestEmailResponse(
                success=True,
                message="Test email sent successfully",
                email=target_email
            )
        else:
            logger.error(f"Failed to send test email to {target_email}: {result.get('error')}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send test email. Please check your email configuration."
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error sending test email for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to send test email"
        )


@router.get(
    "/webhook",
    response_model=WebhookConfigResponse,
    summary="Get webhook configuration",
    description="Retrieve the current user's registered webhook endpoint settings.",
)
async def get_webhook_config(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> WebhookConfigResponse:
    """Get the current user's registered webhook endpoint."""
    try:
        response = (
            sb.table("user_alert_settings")
            .select("webhook_url, webhook_enabled, webhook_secret")
            .eq("user_id", current_user.id)
            .limit(1)
            .execute()
        )
        if response.data:
            rec = response.data[0]
            return WebhookConfigResponse(
                webhook_url=rec.get("webhook_url"),
                webhook_enabled=bool(rec.get("webhook_enabled")),
                webhook_secret_configured=bool(rec.get("webhook_secret")),
                message="Webhook configuration loaded",
            )
        return WebhookConfigResponse(
            webhook_url=None,
            webhook_enabled=False,
            webhook_secret_configured=False,
            message="No webhook configuration found",
        )
    except Exception as exc:
        logger.exception("Failed to get webhook configuration: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load webhook configuration",
        )


@router.post(
    "/webhook",
    response_model=WebhookConfigResponse,
    summary="Register webhook endpoint",
    description="Register an outgoing webhook endpoint URL with an optional secret signature to receive real-time JSON alert payloads.",
)
@router.put(
    "/webhook",
    response_model=WebhookConfigResponse,
    summary="Update webhook endpoint",
    description="Update outgoing webhook endpoint configuration with an optional secret signature.",
)
@limiter.limit(API_RATE_LIMIT)
async def register_webhook(
    request: Request,
    body: WebhookRegisterRequest,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> WebhookConfigResponse:
    """Register or update an outgoing webhook endpoint URL with optional secret signature."""
    try:
        try:
            WebhookService._validate_url(body.webhook_url)
        except WebhookDeliveryError as v_err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(v_err),
            )

        update_payload = {
            "webhook_url": body.webhook_url,
            "webhook_enabled": body.enabled,
        }
        if body.webhook_secret is not None:
            clean_secret = body.webhook_secret.strip()
            update_payload["webhook_secret"] = clean_secret if clean_secret else None

        res = (
            sb.table("user_alert_settings")
            .update(update_payload)
            .eq("user_id", current_user.id)
            .execute()
        )
        if not res.data:
            insert_data = {
                "user_id": current_user.id,
                "email_enabled": True,
                "digest_frequency_hours": 24,
                "alert_price_drop": True,
                "alert_price_increase": True,
                **update_payload,
            }
            res = sb.table("user_alert_settings").insert(insert_data).execute()

        invalidate_dashboard_cache(current_user.id)
        saved = res.data[0] if res.data else update_payload
        return WebhookConfigResponse(
            webhook_url=saved.get("webhook_url"),
            webhook_enabled=bool(saved.get("webhook_enabled")),
            webhook_secret_configured=bool(saved.get("webhook_secret")),
            message="Webhook configuration saved successfully",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to register webhook: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save webhook configuration",
        )


@router.delete(
    "/webhook",
    response_model=WebhookConfigResponse,
    summary="Delete webhook endpoint",
    description="Disable and clear the registered webhook endpoint URL.",
)
async def delete_webhook(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> WebhookConfigResponse:
    """Disable and delete registered webhook endpoint configuration."""
    try:
        sb.table("user_alert_settings").update({
            "webhook_enabled": False,
            "webhook_url": None,
            "webhook_secret": None,
        }).eq("user_id", current_user.id).execute()
        invalidate_dashboard_cache(current_user.id)
        return WebhookConfigResponse(
            webhook_url=None,
            webhook_enabled=False,
            webhook_secret_configured=False,
            message="Webhook endpoint deleted successfully",
        )
    except Exception as exc:
        logger.exception("Failed to delete webhook: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete webhook configuration",
        )


@router.post(
    "/test-webhook",
    response_model=TestWebhookResponse,
    summary="Send test webhook ping",
    description="Send a sample ping to verify the registered webhook endpoint works.",
)
@router.post(
    "/webhook/test",
    response_model=TestWebhookResponse,
    summary="Send test webhook ping",
    description="Send a sample ping to verify the registered webhook endpoint works.",
)
@limiter.limit(API_RATE_LIMIT)
async def send_test_webhook(
    request: Request,
    body: TestWebhookRequest | None = None,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> TestWebhookResponse:
    """Send a sample ping to verify that an endpoint works."""
    try:
        target_url = body.webhook_url if body else None
        target_secret = body.webhook_secret if body else None

        if not target_url:
            settings_res = (
                sb.table("user_alert_settings")
                .select("webhook_url, webhook_secret, webhook_enabled")
                .eq("user_id", current_user.id)
                .limit(1)
                .execute()
            )
            if settings_res.data:
                target_url = settings_res.data[0].get("webhook_url")
                if target_secret is None:
                    target_secret = settings_res.data[0].get("webhook_secret")

        if not target_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No webhook URL configured or provided",
            )

        cid = get_correlation_id()
        wh_service = WebhookService()
        try:
            result = wh_service.send_test_ping(
                webhook_url=target_url,
                webhook_secret=target_secret,
                correlation_id=cid,
                extra_data={"user_id": current_user.id},
            )
        except WebhookDeliveryError as v_err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(v_err),
            )

        # Log delivery in alert_history for audit
        service_sb = get_supabase_client()
        history_record = {
            "user_id": current_user.id,
            "digest_sent_at": datetime.now(timezone.utc).isoformat(),
            "alerts_count": 1,
            "price_drops": 0,
            "price_increases": 0,
            "currency_changes": 0,
            "email_status": "disabled",
            "webhook_status": "sent" if result.get("success") else "failed",
            "response_code": result.get("status_code"),
            "error_message": result.get("error"),
            "alert_ids": [],
        }
        try:
            service_sb.table("alert_history").insert(history_record).execute()
        except Exception as h_err:
            logger.warning("Failed to insert alert_history audit row: %s", h_err)

        if result.get("success"):
            return TestWebhookResponse(
                success=True,
                message="Test webhook ping sent successfully",
                status_code=result.get("status_code"),
                response_code=result.get("status_code"),
                error=None,
            )
        else:
            return TestWebhookResponse(
                success=False,
                message=f"Test webhook delivery failed: {result.get('error')}",
                status_code=result.get("status_code"),
                response_code=result.get("status_code"),
                error=result.get("error"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to send test webhook for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to send test webhook",
        )


@router.post(
    "/check/{competitor_id}",
    response_model=CheckPriceDropResponse,
    summary="Check price drop conditions",
    description="Check price drop conditions for a competitor given a price.",
)
@limiter.limit(API_RATE_LIMIT)
async def check_price_drop(
    request: Request,
    competitor_id: str,
    body: CheckPriceDropRequest,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> CheckPriceDropResponse:
    """Check price drop conditions whenever a competitor price is updated."""
    try:
        comp_res = (
            sb.table("competitors")
            .select("id, product_id, products(user_id)")
            .eq("id", competitor_id)
            .execute()
        )
        if not comp_res.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found")
        comp = comp_res.data[0]
        if comp.get("products", {}).get("user_id") != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this competitor")

        alert_svc = AlertService()
        cid = get_correlation_id()
        custom_threshold = body.threshold_percent if body.threshold_percent is not None else body.target_percentage
        res = await alert_svc.check_price_change_and_alert(
            competitor_id=competitor_id,
            new_price=body.price,
            currency=body.currency,
            correlation_id=cid,
            custom_threshold=custom_threshold,
        )
        return CheckPriceDropResponse(
            alert_created=res.get("alert_created", False),
            alert_type=res.get("alert_type"),
            change_percent=res.get("change_percent"),
            message=res.get("message", ""),
            suppressed=res.get("suppressed", False),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to check price drop conditions: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to check price drop conditions",
        )


@router.patch(
    "/competitors/{competitor_id}/accept-currency",
    response_model=AcceptCurrencyResponse,
    summary="Accept currency change",
    description="Accept a new currency for a competitor after currency change detection.",
    responses={
        200: {"model": AcceptCurrencyResponse, "description": "Currency accepted and alerts dismissed"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
        403: {"model": ErrorEnvelope, "description": "Not authorized to modify this competitor"},
        404: {"model": ErrorEnvelope, "description": "Competitor not found"},
        500: {"model": ErrorEnvelope, "description": "Unable to update currency"},
    },
)
async def accept_currency(
    competitor_id: str,
    request: AcceptCurrencyRequest,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
) -> AcceptCurrencyResponse:
    """
    Accept a new currency for a competitor after currency change detection.

    Updates the competitor's expected_currency and dismisses the currency_changed alert.
    """
    try:
        # Verify user owns this competitor (via product ownership)
        comp_response = (
            sb.table("competitors")
            .select("id, product_id, products(user_id)")
            .eq("id", competitor_id)
            .execute()
        )

        if not comp_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Competitor not found"
            )

        competitor = comp_response.data[0]
        if competitor["products"]["user_id"] != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to modify this competitor"
            )

        # Update expected_currency using service client (bypasses RLS for update)
        service_sb = get_supabase_client()
        service_sb.table("competitors").update({
            "expected_currency": request.currency
        }).eq("id", competitor_id).execute()

        # Invalidate dashboard cache immediately once currency update commits
        invalidate_dashboard_cache(current_user.id)

        try:
            # Dismiss any pending currency_changed alerts for this competitor
            service_sb.table("pending_alerts").update({
                "included_in_digest": True
            }).eq("competitor_id", competitor_id).eq("alert_type", "currency_changed").execute()
        finally:
            invalidate_dashboard_cache(current_user.id)

        return AcceptCurrencyResponse(
            success=True,
            message=f"Now tracking prices in {request.currency}",
            competitor_id=competitor_id,
            new_currency=request.currency
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to accept currency for competitor {competitor_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update currency"
        )


@router.post(
    "/accept-all-currencies",
    response_model=AcceptAllCurrenciesResponse,
    summary="Accept all pending currency changes",
    description="Bulk operation to update all competitors with currency_changed alerts to their new detected currencies.",
    responses={
        200: {"model": AcceptAllCurrenciesResponse, "description": "Accepted all pending currency changes"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
        500: {"model": ErrorEnvelope, "description": "Unable to accept currency changes"},
    },
)
async def accept_all_currencies(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
) -> AcceptAllCurrenciesResponse:
    """
    Accept all pending currency changes for the current user.

    Bulk operation to update all competitors with currency_changed alerts
    to their new detected currencies.
    """
    try:
        # Get all pending currency_changed alerts for this user
        alerts_response = (
            sb.table("pending_alerts")
            .select("id, competitor_id, new_currency")
            .eq("user_id", current_user.id)
            .eq("alert_type", "currency_changed")
            .eq("included_in_digest", False)
            .execute()
        )

        if not alerts_response.data:
            return AcceptAllCurrenciesResponse(
                success=True,
                message="No pending currency changes",
                updated_count=0
            )

        service_sb = get_supabase_client()
        updated_count = 0

        try:
            for alert in alerts_response.data:
                competitor_id = alert["competitor_id"]
                new_currency = alert["new_currency"]

                if new_currency:
                    # Update competitor's expected currency
                    service_sb.table("competitors").update({
                        "expected_currency": new_currency
                    }).eq("id", competitor_id).execute()

                    # Mark alert as processed
                    service_sb.table("pending_alerts").update({
                        "included_in_digest": True
                    }).eq("id", alert["id"]).execute()

                    updated_count += 1
        finally:
            if updated_count > 0:
                invalidate_dashboard_cache(current_user.id)

        return AcceptAllCurrenciesResponse(
            success=True,
            message=f"Accepted {updated_count} currency changes",
            updated_count=updated_count
        )

    except Exception as e:
        logger.exception(f"Failed to accept all currencies for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to accept currency changes"
        )
