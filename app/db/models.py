import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Competitor Models
# ---------------------------------------------------------------------------
class CompetitorCreate(BaseModel):
    """Input model for creating a competitor."""

    url: str = Field(..., min_length=10, max_length=2048, examples=["https://amazon.com/dp/B08N5WRWNW"])
    retailer_name: str | None = Field(None, max_length=100, examples=["Amazon"])
    alert_threshold_percent: Decimal = Field(default=Decimal("10.00"), ge=0, le=100, examples=[10.00])

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()

        if not v.startswith("https://"):
            raise ValueError("URL must use HTTPS")

        blocked_patterns = [
            r"^https?://localhost",
            r"^https?://127\.",
            r"^https?://0\.",
            r"^https?://10\.",
            r"^https?://172\.(1[6-9]|2[0-9]|3[01])\.",
            r"^https?://192\.168\.",
        ]
        for pattern in blocked_patterns:
            if re.match(pattern, v, re.IGNORECASE):
                raise ValueError("Private/local URLs are not allowed")

        return v


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
class ProductCreate(BaseModel):
    """Input model for creating a product with competitors."""

    product_name: str = Field(..., min_length=1, max_length=255, examples=["iPhone 15 Pro Max"])
    competitors: list[CompetitorCreate] = Field(..., min_length=1, max_length=20)

    @field_validator("product_name")
    @classmethod
    def sanitize_product_name(cls, v: str) -> str:
        v = v.strip()
        v = v.replace("<", "&lt;").replace(">", "&gt;")
        return v


class ProductUpdate(BaseModel):
    """Input model for updating a product."""

    product_name: str | None = Field(None, min_length=1, max_length=255, examples=["iPhone 15 Pro Max 256GB"])
    is_active: bool | None = Field(None, examples=[True])

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
