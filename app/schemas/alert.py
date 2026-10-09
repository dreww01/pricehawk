"""Alert settings, pending alert lists, and delivery history schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

class AlertSettingsResponse(BaseModel):
    """User notification and digest settings."""
    user_id: str
    email_enabled: bool = True
    digest_frequency_hours: int = 24
    alert_price_drop: bool = True
    alert_price_increase: bool = True
    webhook_enabled: bool = False
    webhook_url: str | None = None
    webhook_secret_configured: bool = False
    last_digest_sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

class AlertSettingsUpdate(BaseModel):
    """Request to update alert settings."""
    email_enabled: bool | None = None
    digest_frequency_hours: int | None = Field(None, ge=1, le=168)
    alert_price_drop: bool | None = None
    alert_price_increase: bool | None = None
    webhook_enabled: bool | None = None
    webhook_url: str | None = Field(None, max_length=2048)
    webhook_secret: str | None = Field(None, min_length=16, max_length=255)
    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        val = v.strip()
        if val and not val.startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")
        return val or None

class DigestRunRequest(BaseModel):
    """Options for an on-demand digest run."""
    force: bool = False
    dry_run: bool = False

class DigestRunResponse(BaseModel):
    """Outcome of a digest run for one user."""
    user_id: str
    status: str
    alerts_count: int
    price_drops: int
    price_increases: int
    currency_changes: int
    email_sent: bool = False
    webhook_sent: bool = False
    dry_run: bool = False
    skipped_reason: str | None = None
    correlation_id: str | None = None

class PendingAlertResponse(BaseModel):
    """A pending alert that hasn't been sent yet."""
    id: str
    product_id: str
    product_name: str
    competitor_id: str
    competitor_url: str
    old_price: Decimal | None
    new_price: Decimal | None
    price_change_percent: Decimal | None
    alert_type: str
    old_currency: str | None = None
    new_currency: str | None = None
    created_at: datetime

class PendingAlertsListResponse(BaseModel):
    """List of pending alerts."""
    alerts: list[PendingAlertResponse]
    total: int

class AlertHistoryResponse(BaseModel):
    """A persisted digest or real-time alert delivery attempt."""
    id: str
    digest_sent_at: datetime
    timestamp: datetime | None = None
    alerts_count: int
    price_drops: int = 0
    price_increases: int = 0
    currency_changes: int = 0
    email_status: str
    webhook_status: str = "disabled"
    delivered: bool = False
    delivered_status: str = "disabled"
    response_code: int | None = None
    status_code: int | None = None
    error_message: str | None = None

class AlertHistoryListResponse(BaseModel):
    """List of alert history."""
    alerts: list[AlertHistoryResponse]
    total: int

class WebhookRegisterRequest(BaseModel):
    """Request to register or update an outgoing webhook endpoint."""
    webhook_url: str = Field(..., max_length=2048)
    webhook_secret: str | None = Field(default=None, max_length=255)
    enabled: bool = True
    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str) -> str:
        if not v.strip().startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")
        return v.strip()

class WebhookConfigResponse(BaseModel):
    """Current webhook configuration for a user."""
    webhook_url: str | None = None
    webhook_enabled: bool = False
    webhook_secret_configured: bool = False
    message: str = "Webhook configuration loaded"

class TestWebhookRequest(BaseModel):
    """Request to send a test webhook ping."""
    __test__ = False
    webhook_url: str | None = None
    webhook_secret: str | None = None

class TestWebhookResponse(BaseModel):
    """Response from test webhook action."""
    __test__ = False
    success: bool
    message: str
    status_code: int | None = None
    response_code: int | None = None
    error: str | None = None

class PriceAlertEvent(BaseModel):
    """Real-time JSON alert event payload dispatched to store owners via webhook."""
    event: str = "price_drop"
    event_type: str = "price_drop"
    alert_id: str
    user_id: str
    product_id: str
    product_name: str | None = None
    competitor_id: str
    retailer_name: str | None = None
    competitor_url: str | None = None
    old_price: Decimal | float | None = None
    new_price: Decimal | float | None = None
    price_change_percent: Decimal | float | None = None
    threshold_percent: Decimal | float | None = None
    currency: str = "USD"
    detected_at: str
    timestamp: str

class CheckPriceDropRequest(BaseModel):
    """Request to evaluate price drop conditions for a competitor."""
    price: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", max_length=3)
    threshold_percent: Decimal | None = Field(default=None, ge=0, le=100)
    target_percentage: Decimal | None = Field(default=None, ge=0, le=100)

class CheckPriceDropResponse(BaseModel):
    """Response from price drop evaluation."""
    alert_created: bool
    alert_type: str | None = None
    change_percent: Decimal | float | None = None
    message: str
    suppressed: bool = False

class TestEmailRequest(BaseModel):
    """Request to send a test email."""
    __test__ = False
    email: str | None = None

class TestEmailResponse(BaseModel):
    """Outcome of alert test email dispatch."""
    __test__ = False
    success: bool = Field(default=True, description="Whether test email delivery succeeded", examples=[True])
    message: str = Field(default="Test email sent successfully", description="Status message", examples=["Test email sent successfully"])
    email: str = Field(..., description="Target email recipient", examples=["user@example.com"])

__all__ = [
    "AlertHistoryListResponse", "AlertHistoryResponse",
    "AlertSettingsResponse", "AlertSettingsUpdate",
    "CheckPriceDropRequest", "CheckPriceDropResponse",
    "DigestRunRequest", "DigestRunResponse",
    "PendingAlertResponse", "PendingAlertsListResponse",
    "PriceAlertEvent", "TestEmailRequest", "TestEmailResponse",
    "TestWebhookRequest", "TestWebhookResponse",
    "WebhookConfigResponse", "WebhookRegisterRequest",
]
