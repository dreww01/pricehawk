"""Webhook configuration HTTP endpoints mounted by the alert router."""

from fastapi import APIRouter, Depends, Request
from supabase import Client

from app.api.routes.alert_http import alert_errors
from app.core.security import CurrentUser, get_current_user
from app.db.database import get_user_supabase_client
from app.db.models import WebhookConfigResponse, WebhookRegisterRequest
from app.middleware.rate_limit import API_RATE_LIMIT, limiter
from app.services.alert_settings_service import AlertSettingsService

router = APIRouter()


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
    with alert_errors("Unable to load webhook configuration"):
        return WebhookConfigResponse(**AlertSettingsService(sb, current_user.id).get_webhook())


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
    with alert_errors("Unable to save webhook configuration"):
        return WebhookConfigResponse(
            **AlertSettingsService(sb, current_user.id).register_webhook(body)
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
    with alert_errors("Unable to delete webhook configuration"):
        return WebhookConfigResponse(**AlertSettingsService(sb, current_user.id).delete_webhook())
