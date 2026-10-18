"""HTTP presentation for alert preferences, delivery, and currency acceptance."""

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from supabase import Client

from app.api.routes.alert_http import alert_errors as _errors
from app.api.routes.alert_webhooks import router as webhook_router
from app.core.flash import flash
from app.core.logging import get_correlation_id
from app.core.security import CurrentUser, get_current_user
from app.db.database import get_supabase_client, get_user_supabase_client
from app.db.models import (
    AcceptAllCurrenciesResponse,
    AcceptCurrencyResponse,
    AlertHistoryListResponse,
    AlertSettingsResponse,
    AlertSettingsUpdate,
    CheckPriceDropRequest,
    CheckPriceDropResponse,
    DigestRunRequest,
    DigestRunResponse,
    ErrorEnvelope,
    PendingAlertsListResponse,
    TestEmailRequest,
    TestEmailResponse,
    TestWebhookRequest,
    TestWebhookResponse,
)
from app.middleware.rate_limit import API_RATE_LIMIT, limiter
from app.services import alert_delivery_service
from app.services.alert_management_service import AlertManagementService
from app.services.alert_query_service import AlertQueryService
from app.services.alert_settings_service import AlertSettingsService
from app.services.digest_service import DigestService
from app.services.email_service import EmailService

router = APIRouter(prefix="/alerts", tags=["alerts"])
router.include_router(webhook_router)


class AcceptCurrencyRequest(BaseModel):
    """Currency selected by the user after a detected currency change."""

    currency: str


def _settings_response(settings: dict) -> AlertSettingsResponse:
    return AlertSettingsResponse(
        **{key: value for key, value in settings.items() if key != "webhook_secret"},
        webhook_secret_configured=bool(settings.get("webhook_secret")),
    )


@router.get("/settings", response_model=AlertSettingsResponse)
async def get_alert_settings(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> AlertSettingsResponse:
    with _errors("Unable to load alert settings"):
        return _settings_response(AlertSettingsService(sb, current_user.id).get())


@router.put("/settings", response_model=AlertSettingsResponse)
async def update_alert_settings(
    updates: AlertSettingsUpdate,
    http_response: Response,
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> AlertSettingsResponse:
    with _errors("Unable to save alert settings"):
        result = _settings_response(AlertSettingsService(sb, current_user.id).update(updates))
        flash(http_response, "Alert settings saved successfully.", "success")
        return result


@router.get("/pending", response_model=PendingAlertsListResponse)
async def get_pending_alerts(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> PendingAlertsListResponse:
    with _errors("Unable to load pending alerts"):
        return PendingAlertsListResponse(**AlertQueryService(sb, current_user.id).pending())


@router.get("/history", response_model=AlertHistoryListResponse)
async def get_alert_history(
    limit: int = Query(default=20, ge=1, le=100),
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> AlertHistoryListResponse:
    with _errors("Unable to load alert history"):
        return AlertHistoryListResponse(**AlertQueryService(sb, current_user.id).history(limit))


@router.post("/digests/run", response_model=DigestRunResponse)
async def run_alert_digest(
    request: DigestRunRequest, current_user: CurrentUser = Depends(get_current_user)
) -> DigestRunResponse:
    with _errors("Unable to run alert digest"):
        return DigestRunResponse(
            **alert_delivery_service.run_digest(
                current_user.id,
                current_user.email,
                current_user.role,
                request.force,
                request.dry_run,
                get_correlation_id(),
                DigestService,
            )
        )


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
    request: TestEmailRequest | None = None, current_user: CurrentUser = Depends(get_current_user)
) -> TestEmailResponse:
    with _errors("Unable to send test email"):
        email = (request.email if request else None) or current_user.email
        return TestEmailResponse(**alert_delivery_service.send_test_email(email, EmailService))


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
    with _errors("Unable to send test webhook"):
        return TestWebhookResponse(
            **alert_delivery_service.send_test_webhook(
                sb, get_supabase_client, current_user.id, body, get_correlation_id()
            )
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
    with _errors("Unable to check price drop conditions"):
        result = await AlertManagementService(sb, current_user.id, get_supabase_client).check_price(
            competitor_id, body, get_correlation_id()
        )
        return CheckPriceDropResponse(**result)


@router.patch(
    "/competitors/{competitor_id}/accept-currency",
    response_model=AcceptCurrencyResponse,
    summary="Accept currency change",
    description="Accept a new currency for a competitor after currency change detection.",
    responses={
        200: {
            "model": AcceptCurrencyResponse,
            "description": "Currency accepted and alerts dismissed",
        },
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
    current_user: CurrentUser = Depends(get_current_user),
) -> AcceptCurrencyResponse:
    with _errors("Unable to update currency"):
        return AcceptCurrencyResponse(
            **AlertManagementService(sb, current_user.id, get_supabase_client).accept_currency(
                competitor_id, request.currency
            )
        )


@router.post(
    "/accept-all-currencies",
    response_model=AcceptAllCurrenciesResponse,
    summary="Accept all pending currency changes",
    description="Bulk operation to update all competitors with currency_changed alerts to their new detected currencies.",
    responses={
        200: {
            "model": AcceptAllCurrenciesResponse,
            "description": "Accepted all pending currency changes",
        },
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
        500: {"model": ErrorEnvelope, "description": "Unable to accept currency changes"},
    },
)
async def accept_all_currencies(
    sb: Client = Depends(get_user_supabase_client),
    current_user: CurrentUser = Depends(get_current_user),
) -> AcceptAllCurrenciesResponse:
    with _errors("Unable to accept currency changes"):
        return AcceptAllCurrenciesResponse(
            **AlertManagementService(
                sb, current_user.id, get_supabase_client
            ).accept_all_currencies()
        )
