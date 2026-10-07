"""Scraper task request/response, worker health, charts, and operational schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


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


class WorkerHealthResponse(BaseModel):
    """Response model for worker health check."""

    worker_status: str
    ping_response: str | None = None
    active_tasks: int | None = None
    error: str | None = None


class InitialPriceResult(BaseModel):
    """Result of initial price scraping for a competitor URL."""

    url: str
    price: Decimal | None
    currency: str = "USD"
    status: str
    error_message: str | None = None


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


class HealthCheckResponse(BaseModel):
    """Service health check response contract."""

    status: str = Field(default="healthy", description="Service operational health status", examples=["healthy"])


class MessageResponse(BaseModel):
    """Standard generic message response contract."""

    message: str = Field(..., description="Human-readable operational status message", examples=["Operation completed successfully."])


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


class TestEmailRequest(BaseModel):
    """Request to send a test email."""

    __test__ = False
    email: str | None = None


class TestEmailResponse(BaseModel):
    """Outcome of alert test email dispatch."""

    __test__ = False
    success: bool = Field(default=True, description="Whether test email delivery succeeded")
    message: str = Field(default="Test email sent successfully", description="Status message")
    email: str = Field(..., description="Target email recipient")


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
