"""Tracked product, price history, competitor, and store discovery schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

MAX_PRODUCTS_LIMIT = 5000


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
        if v.lower().startswith("http://"):
            raise ValueError("HTTP is not secure. Please use HTTPS or enter the domain without a protocol")
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
        v = v.strip()
        return v.replace("<", "&lt;").replace(">", "&gt;")


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


class DashboardProductItem(BaseModel):
    """Tracked product summary entry for dashboard cards."""

    id: str = Field(..., description="Product identifier", examples=["prod_12345"])
    product_name: str = Field(..., description="Product display name", examples=["Sony WH-1000XM5"])
    is_active: bool = Field(..., description="Whether active scraping is enabled", examples=[True])
    competitor_count: int = Field(..., description="Count of competitor URLs tracked", examples=[3])


class DashboardProductsResponse(BaseModel):
    """Recent tracked products listing for dashboard view."""

    products: list[DashboardProductItem] = Field(default_factory=list, description="Recent products")
