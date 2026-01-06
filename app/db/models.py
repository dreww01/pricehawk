from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


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
    url: str = Field(..., min_length=10, max_length=2048)
    keyword: str | None = Field(None, max_length=100)
    limit: int = Field(default=50, ge=1, le=250)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("https://"):
            raise ValueError("URL must use HTTPS")
        return v


class StoreDiscoveryResponse(BaseModel):
    """Response from store discovery."""
    platform: str
    store_url: str
    total_found: int
    products: list[DiscoveredProductResponse]
    error: str | None = None


class TrackProductsRequest(BaseModel):
    """Request to add discovered products to tracking."""
    group_name: str = Field(..., min_length=1, max_length=255)
    product_urls: list[str] = Field(..., min_length=1, max_length=50)
    alert_threshold_percent: Decimal = Field(default=Decimal("10.00"), ge=0, le=100)


class TrackProductsResponse(BaseModel):
    """Response from tracking products."""
    group_id: str
    group_name: str
    products_added: int


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
