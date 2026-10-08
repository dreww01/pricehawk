"""Scraper, discovery, and worker health schemas."""

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

# Maximum products for discovery and tracking
MAX_PRODUCTS_LIMIT = 5000


class HealthCheckResponse(BaseModel):
    """Service health check response contract."""

    status: str = Field(default="healthy", description="Service operational health status", examples=["healthy"])


class MessageResponse(BaseModel):
    """Standard generic message response contract."""

    message: str = Field(..., description="Human-readable operational status message", examples=["Operation completed successfully."])


class WorkerHealthResponse(BaseModel):
    """Response model for worker health check."""

    worker_status: str
    ping_response: str | None = None
    active_tasks: int | None = None
    error: str | None = None


class ScrapeTaskResponse(BaseModel):
    """Response when manual scrape task is queued."""

    task_id: str
    status: str = "queued"
    message: str = "Scrape task queued"
    correlation_id: str | None = None


class ScrapeProgressResponse(BaseModel):
    """Progress update from scrape task (SSE event data)."""

    status: str
    completed: int = 0
    total: int = 0
    current: str | None = None
    results: list[dict] = []
    error: str | None = None
    correlation_id: str | None = None


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


class InitialPriceResult(BaseModel):
    """Result of initial price scraping for a competitor URL."""

    url: str
    price: Decimal | None
    currency: str = "USD"
    status: str
    error_message: str | None = None


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


__all__ = [
    "MAX_PRODUCTS_LIMIT",
    "HealthCheckResponse",
    "MessageResponse",
    "WorkerHealthResponse",
    "ScrapeTaskResponse",
    "ScrapeProgressResponse",
    "ScrapeResultResponse",
    "InitialPriceResult",
    "DiscoveredProductResponse",
    "StoreDiscoveryRequest",
    "StoreDiscoveryResponse",
    "DashboardCacheMetricsResponse",
]
