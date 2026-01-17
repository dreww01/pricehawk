"""
API endpoints for alert management.

Handles user alert settings, pending alerts, alert history, and test emails.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_user_supabase_client, get_supabase_client
from app.db.models import (
    AlertSettingsResponse,
    AlertSettingsUpdate,
    PendingAlertsListResponse,
    PendingAlertResponse,
    AlertHistoryListResponse,
    AlertHistoryResponse,
    TestEmailRequest
)
from app.services.email_service import EmailService
from app.services.alert_service import AlertService


class AcceptCurrencyRequest(BaseModel):
    """Request to accept a new currency for a competitor."""
    currency: str

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


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
            return AlertSettingsResponse(
                user_id=settings["user_id"],
                email_enabled=settings["email_enabled"],
                digest_frequency_hours=settings["digest_frequency_hours"],
                alert_price_drop=settings["alert_price_drop"],
                alert_price_increase=settings["alert_price_increase"],
                last_digest_sent_at=settings.get("last_digest_sent_at"),
                created_at=settings["created_at"],
                updated_at=settings["updated_at"]
            )

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
            return AlertSettingsResponse(
                user_id=settings["user_id"],
                email_enabled=settings["email_enabled"],
                digest_frequency_hours=settings["digest_frequency_hours"],
                alert_price_drop=settings["alert_price_drop"],
                alert_price_increase=settings["alert_price_increase"],
                last_digest_sent_at=settings.get("last_digest_sent_at"),
                created_at=settings["created_at"],
                updated_at=settings["updated_at"]
            )

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
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Update user's alert notification settings.

    Only provided fields will be updated.
    """
    try:
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
            return AlertSettingsResponse(
                user_id=settings["user_id"],
                email_enabled=settings["email_enabled"],
                digest_frequency_hours=settings["digest_frequency_hours"],
                alert_price_drop=settings["alert_price_drop"],
                alert_price_increase=settings["alert_price_increase"],
                last_digest_sent_at=settings.get("last_digest_sent_at"),
                created_at=settings["created_at"],
                updated_at=settings["updated_at"]
            )

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
    limit: int = 20,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get user's alert history (sent digest emails).
    """
    try:
        response = (
            sb.table("alert_history")
            .select("id, digest_sent_at, alerts_count, email_status, error_message")
            .eq("user_id", current_user.id)
            .order("digest_sent_at", desc=True)
            .limit(limit)
            .execute()
        )

        history = [
            AlertHistoryResponse(
                id=row["id"],
                digest_sent_at=row["digest_sent_at"],
                alerts_count=row["alerts_count"],
                email_status=row["email_status"],
                error_message=row.get("error_message")
            )
            for row in response.data
        ]

        return AlertHistoryListResponse(alerts=history, total=len(history))

    except Exception as e:
        logger.exception(f"Failed to get alert history for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load alert history"
        )


@router.post("/test")
async def send_test_email(
    request: TestEmailRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user)
):
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
            return {
                "success": True,
                "message": "Test email sent successfully",
                "email": target_email
            }
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


@router.patch("/competitors/{competitor_id}/accept-currency")
async def accept_currency(
    competitor_id: str,
    request: AcceptCurrencyRequest,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
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

        # Dismiss any pending currency_changed alerts for this competitor
        service_sb.table("pending_alerts").update({
            "included_in_digest": True
        }).eq("competitor_id", competitor_id).eq("alert_type", "currency_changed").execute()

        return {
            "success": True,
            "message": f"Now tracking prices in {request.currency}",
            "competitor_id": competitor_id,
            "new_currency": request.currency
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to accept currency for competitor {competitor_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update currency"
        )


@router.post("/accept-all-currencies")
async def accept_all_currencies(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
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
            return {
                "success": True,
                "message": "No pending currency changes",
                "updated_count": 0
            }

        service_sb = get_supabase_client()
        updated_count = 0

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

        return {
            "success": True,
            "message": f"Accepted {updated_count} currency changes",
            "updated_count": updated_count
        }

    except Exception as e:
        logger.exception(f"Failed to accept all currencies for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to accept currency changes"
        )
