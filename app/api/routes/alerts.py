"""
API endpoints for alert management.

Handles user alert settings, pending alerts, alert history, and test emails.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_user_supabase_client
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
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
                "products(product_name), competitors(retailer_name)"
            )
            .eq("user_id", current_user.id)
            .eq("included_in_digest", False)
            .order("detected_at", desc=True)
            .execute()
        )

        alerts = [
            PendingAlertResponse(
                id=row["id"],
                product_name=row["products"]["product_name"],
                competitor_name=row["competitors"]["retailer_name"] or "Unknown Store",
                alert_type=row["alert_type"],
                old_price=row["old_price"],
                new_price=row["new_price"],
                price_change_percent=row["price_change_percent"],
                currency="USD",  # TODO: Get from competitor
                detected_at=row["detected_at"]
            )
            for row in response.data
        ]

        return PendingAlertsListResponse(alerts=alerts, total=len(alerts))

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
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

        return AlertHistoryListResponse(history=history, total=len(history))

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )


@router.post("/test")
async def send_test_email(
    request: TestEmailRequest | None = None,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Send a test email to verify email configuration.

    If email is not provided, sends to user's registered email.
    """
    try:
        # Get target email
        target_email = request.email if request else None

        if not target_email:
            # Fetch user's email from auth
            user_response = (
                sb.table("auth.users")
                .select("email")
                .eq("id", current_user.id)
                .single()
                .execute()
            )

            if not user_response.data or not user_response.data.get("email"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No email found for user"
                )

            target_email = user_response.data["email"]

        # Send test email
        email_service = EmailService()
        result = email_service.send_test_email(target_email)

        if result["success"]:
            return {
                "success": True,
                "message": f"Test email sent to {target_email}",
                "email": target_email
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to send test email: {result.get('error', 'Unknown error')}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error sending test email: {str(e)}"
        )
