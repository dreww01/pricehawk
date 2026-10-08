"""Tracked product, price history, competitor, and dashboard schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

class TrackProductItem(BaseModel):
    """Single product to track with pre-fetched price data."""
    url: str
    price: Decimal | None = None
    currency: str = "USD"

class TrackProductsRequest(BaseModel):
    """Request to add discovered products to tracking."""
    group_name: str = Field(..., min_length=1, max_length=255)
    products: list[TrackProductItem] = Field(..., min_length=1, max_length=5000)
    alert_threshold_percent: Decimal = Field(default=Decimal("10.00"), ge=0, le=100)

class TrackProductsResponse(BaseModel):
    """Response from tracking products."""
    group_id: str
    group_name: str
    products_added: int
    prices_stored: int

class CompetitorResponse(BaseModel):
    """Output model for competitor data."""
    id: str
    url: str
    retailer_name: str | None
    alert_threshold_percent: Decimal
    created_at: datetime

class ProductUpdate(BaseModel):
    """Input model for updating a product."""
    product_name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None

    @field_validator("product_name")
    @classmethod
    def sanitize_product_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip().replace("<", "&lt;").replace(">", "&gt;")

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

class InsightResponse(BaseModel):
    """Output model for a single insight."""
    id: str
    product_id: str
    insight_text: str
    insight_type: str
    confidence_score: Decimal
    generated_at: datetime

class InsightListResponse(BaseModel):
    """Output model for list of insights."""
    insights: list[InsightResponse]
    total: int

class GenerateInsightRequest(BaseModel):
    """Request to generate insights."""
    force_regenerate: bool = False

class ChartDataPoint(BaseModel):
    """Single data point for chart visualization."""
    timestamp: datetime
    price: Decimal | None
    currency: str
    status: str

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
    price_change_percent: Decimal | None

class ChartDataResponse(BaseModel):
    """Output model for chart visualization data."""
    product_id: str
    product_name: str
    competitors: list[CompetitorChartData]
    date_range_start: datetime | None
    date_range_end: datetime | None
    total_data_points: int

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

__all__ = [
    "AcceptAllCurrenciesResponse", "AcceptCurrencyResponse",
    "ChartDataPoint", "ChartDataResponse", "CompetitorChartData",
    "CompetitorResponse", "DashboardActivityItem", "DashboardActivityResponse",
    "DashboardInsightItem", "DashboardInsightsResponse", "DashboardProductItem",
    "DashboardProductsResponse", "DashboardStatsResponse", "GenerateInsightRequest",
    "InsightListResponse", "InsightResponse", "PriceHistoryListResponse",
    "PriceHistoryResponse", "ProductListResponse", "ProductResponse",
    "ProductUpdate", "TrackProductItem", "TrackProductsRequest",
    "TrackProductsResponse",
]
