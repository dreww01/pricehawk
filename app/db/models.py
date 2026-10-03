from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator
from app.core.errors import (
    ErrorCode,
    ErrorEnvelope,
    STANDARD_ERROR_RESPONSES,
    create_error_response,
    map_error_to_code,
)



# Maximum products for discovery and tracking
MAX_PRODUCTS_LIMIT = 5000  


# ---------------------------------------------------------------------------
# Store Discovery Models
# ---------------------------------------------------------------------------
class DiscoveredProductResponse(BaseModel):
    """Single product discovered from a store."""
    name: str
    price: Decimal | None
    currency: str
    image_url: str | None
    product_url: str
    platform: str
    platform_label: str | None = None
    confidence: float | None = None
    variant_id: str | None = None
    sku: str | None = None
    in_stock: bool = True


class StoreDiscoveryRequest(BaseModel):
    """Request to discover products from a store."""
    url: str = Field(..., min_length=3, max_length=2048)
    keyword: str | None = Field(None, max_length=100)
    limit: int = Field(default=50, ge=1, le=MAX_PRODUCTS_LIMIT)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        # Reject http:// explicitly (insecure)
        if v.lower().startswith("http://"):
            raise ValueError("HTTP is not secure. Please use HTTPS or enter the domain without a protocol")
        # Normalize: add https:// if no scheme
        if not v.startswith("https://"):
            if "." in v and " " not in v:
                v = f"https://{v}"
            else:
                raise ValueError("Invalid URL format")
        return v


class StoreDiscoveryResponse(BaseModel):
    """Response from store discovery."""
    platform: str
    store_url: str
    total_found: int
    products: list[DiscoveredProductResponse]
    error: str | None = None
    platform_label: str = "Custom / Web Heuristics"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TrackProductItem(BaseModel):
    """Single product to track with pre-fetched price data."""
    url: str
    price: Decimal | None = None
    currency: str = "USD"


class TrackProductsRequest(BaseModel):
    """Request to add discovered products to tracking."""
    group_name: str = Field(..., min_length=1, max_length=255)
    products: list[TrackProductItem] = Field(..., min_length=1, max_length=MAX_PRODUCTS_LIMIT)
    alert_threshold_percent: Decimal = Field(default=Decimal("10.00"), ge=0, le=100)


class TrackProductsResponse(BaseModel):
    """Response from tracking products."""
    group_id: str
    group_name: str
    products_added: int
    prices_stored: int


# ---------------------------------------------------------------------------
# Competitor Models
# ---------------------------------------------------------------------------
class CompetitorResponse(BaseModel):
    """Output model for competitor data."""
    id: str
    url: str
    retailer_name: str | None
    alert_threshold_percent: Decimal
    created_at: datetime


# ---------------------------------------------------------------------------
# Product Models
# ---------------------------------------------------------------------------
class ProductUpdate(BaseModel):
    """Input model for updating a product."""
    product_name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None

    @field_validator("product_name")
    @classmethod
    def sanitize_product_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        v = v.replace("<", "&lt;").replace(">", "&gt;")
        return v


class ProductResponse(BaseModel):
    """Output model for product data with competitors."""
    id: str
    product_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    competitors: list[CompetitorResponse] = []


class ProductListResponse(BaseModel):
    """Output model for list of products."""
    products: list[ProductResponse]
    total: int


# ---------------------------------------------------------------------------
# Price History Models
# ---------------------------------------------------------------------------
class PriceHistoryResponse(BaseModel):
    """Output model for price history entry."""
    id: str
    competitor_id: str
    price: Decimal | None
    currency: str
    scraped_at: datetime
    scrape_status: str
    error_message: str | None


class PriceHistoryListResponse(BaseModel):
    """Output model for list of price history entries."""
    prices: list[PriceHistoryResponse]
    total: int


class ScrapeResultResponse(BaseModel):
    """Output model for a single scrape result."""
    competitor_id: str
    competitor_url: str
    price: Decimal | None
    currency: str
    status: str
    error_message: str | None
    failure_reason: str | None = None
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Insights Models
# ---------------------------------------------------------------------------
class InsightResponse(BaseModel):
    """Output model for a single insight."""
    id: str
    product_id: str
    insight_text: str
    insight_type: str  # 'pattern', 'alert', 'recommendation'
    confidence_score: Decimal
    generated_at: datetime


class InsightListResponse(BaseModel):
    """Output model for list of insights."""
    insights: list[InsightResponse]
    total: int


class GenerateInsightRequest(BaseModel):
    """Request to generate insights (optional parameters for future use)."""
    force_regenerate: bool = False


# ---------------------------------------------------------------------------
# Chart Data Models
# ---------------------------------------------------------------------------
class ChartDataPoint(BaseModel):
    """Single data point for chart visualization."""
    timestamp: datetime
    price: Decimal | None
    currency: str
    status: str  # 'success' or 'failed'


class CompetitorChartData(BaseModel):
    """Chart data for a single competitor."""
    competitor_id: str
    competitor_name: str
    url: str
    data_points: list[ChartDataPoint]
    average_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    current_price: Decimal | None
    price_change_percent: Decimal | None  # vs first data point


class ChartDataResponse(BaseModel):
    """Output model for chart visualization data."""
    product_id: str
    product_name: str
    competitors: list[CompetitorChartData]
    date_range_start: datetime | None
    date_range_end: datetime | None
    total_data_points: int


# ---------------------------------------------------------------------------
# Initial Price Scraping Models
# ---------------------------------------------------------------------------
class InitialPriceResult(BaseModel):
    """Result of initial price scraping for a competitor URL."""
    url: str
    price: Decimal | None
    currency: str = "USD"
    status: str  # 'success' or 'failed'
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Alert Models
# ---------------------------------------------------------------------------
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
    def validate_webhook_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not value.startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")
        return value or None


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
    alert_type: str  # 'price_drop', 'price_increase', 'currency_changed'
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
    def validate_webhook_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")
        return value


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
    email: str | None = None  # If None, use user's email


# ---------------------------------------------------------------------------
# Manual Scrape Task Models (SSE)
# ---------------------------------------------------------------------------
class ScrapeTaskResponse(BaseModel):
    """Response when manual scrape task is queued."""
    task_id: str
    status: str = "queued"
    message: str = "Scrape task queued"
    correlation_id: str | None = None


class ScrapeProgressResponse(BaseModel):
    """Progress update from scrape task (SSE event data)."""
    status: str  # 'queued', 'scraping', 'completed', 'error'
    completed: int = 0
    total: int = 0
    current: str | None = None  # Current retailer being scraped
    results: list[dict] = []  # Completed results so far
    error: str | None = None
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# General & Health Models
# ---------------------------------------------------------------------------
class HealthCheckResponse(BaseModel):
    """Service health check response contract."""
    status: str = Field(default="healthy", description="Service operational health status", examples=["healthy"])


class MessageResponse(BaseModel):
    """Standard generic message response contract."""
    message: str = Field(..., description="Human-readable operational status message", examples=["Operation completed successfully."])


# ---------------------------------------------------------------------------
# Account Management Models
# ---------------------------------------------------------------------------
class AccountSettingsResponse(BaseModel):
    """Current authenticated user account profile and settings."""
    user_id: str = Field(..., description="Unique user identifier", examples=["usr_12345678-abcd-ef01-2345-6789abcdef01"])
    email: str = Field(..., description="Registered user email address", examples=["user@example.com"])


class ChangePasswordResponse(BaseModel):
    """Outcome of password update operation."""
    message: str = Field(default="Password updated successfully", description="Status message", examples=["Password updated successfully"])


class ChangeEmailResponse(BaseModel):
    """Outcome of email change request."""
    message: str = Field(
        default="Verification email sent to your new address. Please check your inbox.",
        description="Status message",
        examples=["Verification email sent to your new address. Please check your inbox."]
    )


class AccountDeletionDetails(BaseModel):
    """Detailed audit metrics of resources purged during account deletion."""
    model_config = {"extra": "allow"}

    user_id: str = Field(..., description="Target user identifier purged", examples=["usr_12345678-abcd-ef01-2345-6789abcdef01"])
    products_found: int = Field(default=0, description="Total products found and removed", examples=[3])
    competitors_found: int = Field(default=0, description="Total competitor entries found and removed", examples=[5])
    tasks_revoked: int = Field(default=0, description="Total background Celery tasks revoked", examples=[2])
    tables_cleaned: list[str] = Field(
        default_factory=list,
        description="Database tables purged during cascade",
        examples=[["price_history", "competitors", "insights", "tracking_jobs", "pending_alerts", "alert_history", "user_alert_settings", "products"]]
    )


class AccountDeleteResponse(BaseModel):
    """Outcome contract for permanent account and data deletion."""
    message: str = Field(
        default="Account data deleted successfully. Please log out.",
        description="Status message",
        examples=["Account data deleted successfully. Please log out."]
    )
    details: AccountDeletionDetails | dict[str, Any] = Field(
        ...,
        description="Resource cleanup summary metrics"
    )


# ---------------------------------------------------------------------------
# Authentication & Password Reset Models
# ---------------------------------------------------------------------------
class SignupResponse(BaseModel):
    """Outcome of user account registration."""
    message: str = Field(default="Account created successfully", description="Outcome message", examples=["Account created successfully"])
    user_id: str = Field(..., description="Unique ID assigned to created user", examples=["usr_12345678-abcd-ef01-2345-6789abcdef01"])
    email: str = Field(..., description="Email address of registered user", examples=["user@example.com"])
    email_confirmed: bool = Field(default=False, description="Whether email confirmation is already verified", examples=[False])


class ForgotPasswordResponse(BaseModel):
    """Confirmation for password reset initiation."""
    message: str = Field(
        default="If an account exists with this email, a reset code has been sent.",
        description="Status message",
        examples=["If an account exists with this email, a reset code has been sent."]
    )


class VerifyResetOTPResponse(BaseModel):
    """Outcome of password reset OTP verification."""
    message: str = Field(default="Code verified successfully", description="Status message", examples=["Code verified successfully"])
    reset_token: str = Field(..., description="Temporary single-use token to complete password reset", examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."])


class ResetPasswordResponse(BaseModel):
    """Outcome of password reset completion."""
    message: str = Field(
        default="Password has been reset successfully. You can now log in.",
        description="Status message",
        examples=["Password has been reset successfully. You can now log in."]
    )


# ---------------------------------------------------------------------------
# Alert Testing & Currency Acceptance Models
# ---------------------------------------------------------------------------
class TestEmailResponse(BaseModel):
    """Outcome of alert test email dispatch."""
    __test__ = False
    success: bool = Field(default=True, description="Whether test email delivery succeeded", examples=[True])
    message: str = Field(default="Test email sent successfully", description="Status message", examples=["Test email sent successfully"])
    email: str = Field(..., description="Target email recipient", examples=["user@example.com"])


class AcceptCurrencyResponse(BaseModel):
    """Outcome of accepting a new detected currency for a competitor."""
    success: bool = Field(default=True, description="Whether currency update succeeded", examples=[True])
    message: str = Field(..., description="Status message", examples=["Now tracking prices in USD"])
    competitor_id: str = Field(..., description="Competitor ID updated", examples=["comp_12345"])
    new_currency: str = Field(..., description="Newly accepted ISO currency code", examples=["USD"])


class AcceptAllCurrenciesResponse(BaseModel):
    """Outcome of bulk accepting all pending currency updates."""
    success: bool = Field(default=True, description="Whether bulk update succeeded", examples=[True])
    message: str = Field(..., description="Status message", examples=["Accepted 2 currency changes"])
    updated_count: int = Field(default=0, description="Total number of competitors updated", examples=[2])


# ---------------------------------------------------------------------------
# Dashboard Overview Models
# ---------------------------------------------------------------------------
class DashboardStatsResponse(BaseModel):
    """Aggregated dashboard statistics summary."""
    products: int = Field(..., description="Total tracked product count", examples=[12])
    competitors: int = Field(..., description="Total competitor URL count", examples=[35])
    alerts: int = Field(..., description="Pending alerts count over past 7 days", examples=[4])
    insights: int = Field(..., description="Total AI insights count", examples=[8])


class DashboardActivityItem(BaseModel):
    """Individual price change alert item for dashboard display."""
    id: str = Field(..., description="Alert record identifier", examples=["alt_12345"])
    type: str = Field(..., description="Alert classification type", examples=["price_drop"])
    product_id: str | None = Field(None, description="Parent product identifier", examples=["prod_67890"])
    product_name: str = Field(..., description="Product display name", examples=["Sony WH-1000XM5"])
    retailer: str = Field(..., description="Retailer name or store domain", examples=["amazon.com"])
    old_price: float | None = Field(None, description="Previous recorded price", examples=[399.99])
    new_price: float | None = Field(None, description="Newly detected price", examples=[348.00])
    change_percent: float | None = Field(None, description="Computed percentage price change", examples=[-13.0])
    detected_at: str | datetime = Field(..., description="Timestamp when price change was detected", examples=["2025-01-15T12:00:00Z"])


class DashboardActivityResponse(BaseModel):
    """Recent price change activity feed."""
    activity: list[DashboardActivityItem] = Field(default_factory=list, description="Recent activity items")


class DashboardProductItem(BaseModel):
    """Tracked product summary entry for dashboard cards."""
    id: str = Field(..., description="Product identifier", examples=["prod_12345"])
    product_name: str = Field(..., description="Product display name", examples=["Sony WH-1000XM5"])
    is_active: bool = Field(..., description="Whether active scraping is enabled", examples=[True])
    competitor_count: int = Field(..., description="Count of competitor URLs tracked", examples=[3])


class DashboardProductsResponse(BaseModel):
    """Recent tracked products listing for dashboard view."""
    products: list[DashboardProductItem] = Field(default_factory=list, description="Recent products")


class DashboardCacheMetricsResponse(BaseModel):
    """Cache performance diagnostics and hit ratios."""
    hits: int = Field(..., description="Total cache hits", examples=[9])
    misses: int = Field(..., description="Total cache misses", examples=[1])
    total_requests: int = Field(..., description="Total cache requests evaluated", examples=[10])
    hit_rate: float = Field(..., description="Fractional hit rate (0.0 - 1.0)", examples=[0.9])
    hit_percentage: float = Field(..., description="Percentage hit rate (0.0 - 100.0)", examples=[90.0])
    invalidations: int = Field(default=0, description="Total cache eviction/invalidation events", examples=[0])
    in_memory_keys: int = Field(default=0, description="Active in-memory cached entries", examples=[3])
    backend: str = Field(default="memory", description="Active cache backend engine", examples=["memory"])
    enabled: bool = Field(default=True, description="Whether caching layer is active", examples=[True])


class DashboardInsightItem(BaseModel):
    """AI insight item for dashboard overview."""
    id: str = Field(..., description="Insight identifier", examples=["ins_12345"])
    product_id: str = Field(..., description="Parent product identifier", examples=["prod_67890"])
    product_name: str = Field(..., description="Parent product display name", examples=["Sony WH-1000XM5"])
    insight_text: str = Field(..., description="Synthesized AI insight text", examples=["Competitor prices dropped 10% on weekends."])
    insight_type: str = Field(..., description="Insight classification", examples=["pattern"])
    generated_at: str | datetime = Field(..., description="Timestamp when insight was generated", examples=["2025-01-15T10:00:00Z"])


class DashboardInsightsResponse(BaseModel):
    """Cross-product AI insights listing."""
    insights: list[DashboardInsightItem] = Field(default_factory=list, description="Recent insight summaries")
    total: int = Field(..., description="Total insights count returned", examples=[10])

