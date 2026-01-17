from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator



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
    """User's alert settings."""
    user_id: str
    email_enabled: bool = True
    digest_frequency: str = "daily"  # 'immediate', 'daily', 'weekly'
    alert_on_price_drop: bool = True
    alert_on_price_increase: bool = True
    alert_threshold_percent: Decimal = Decimal("5.00")


class AlertSettingsUpdate(BaseModel):
    """Request to update alert settings."""
    email_enabled: bool | None = None
    digest_frequency: str | None = None
    alert_on_price_drop: bool | None = None
    alert_on_price_increase: bool | None = None
    alert_threshold_percent: Decimal | None = None


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
    """A sent alert in history."""
    id: str
    product_id: str
    product_name: str
    alert_type: str
    message: str
    sent_at: datetime
    email_status: str  # 'sent', 'failed', 'pending'


class AlertHistoryListResponse(BaseModel):
    """List of alert history."""
    alerts: list[AlertHistoryResponse]
    total: int


class TestEmailRequest(BaseModel):
    """Request to send a test email."""
    email: str | None = None  # If None, use user's email


# ---------------------------------------------------------------------------
# Manual Scrape Task Models (SSE)
# ---------------------------------------------------------------------------
class ScrapeTaskResponse(BaseModel):
    """Response when manual scrape task is queued."""
    task_id: str
    status: str = "queued"
    message: str = "Scrape task queued"


class ScrapeProgressResponse(BaseModel):
    """Progress update from scrape task (SSE event data)."""
    status: str  # 'queued', 'scraping', 'completed', 'error'
    completed: int = 0
    total: int = 0
    current: str | None = None  # Current retailer being scraped
    results: list[dict] = []  # Completed results so far
    error: str | None = None
