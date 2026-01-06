from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
import random

import httpx


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
]


@dataclass
class DiscoveredProduct:
    """Unified product data from any platform."""
    name: str
    price: Decimal | None
    currency: str
    image_url: str | None
    product_url: str
    platform: str
    variant_id: str | None = None
    sku: str | None = None
    in_stock: bool = True
    product_type: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    raw_data: dict = field(default_factory=dict)


class BaseStoreHandler(ABC):
    """Abstract base class for store handlers."""

    platform_name: str = "unknown"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": random.choice(USER_AGENTS)},
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @abstractmethod
    async def detect(self, url: str) -> bool:
        """
        Check if URL belongs to this platform.
        Returns True if this handler can process the URL.
        """
        pass

    @abstractmethod
    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """
        Fetch products from the store.

        Args:
            url: Store URL to scrape
            keyword: Optional filter keyword
            limit: Maximum products to return

        Returns:
            List of discovered products
        """
        pass

    def filter_by_keyword(
        self,
        products: list[DiscoveredProduct],
        keyword: str | None,
    ) -> list[DiscoveredProduct]:
        """Filter products by keyword (case-insensitive, matches any word across multiple fields)."""
        if not keyword:
            return products

        # Split keyword into individual words
        words = [w.lower() for w in keyword.split() if w.strip()]

        if not words:
            return products

        # Match products where at least one word appears in any searchable field
        filtered = []
        for p in products:
            # Build searchable text from multiple fields
            searchable_parts = [
                p.name.lower(),
                p.product_type.lower() if p.product_type else "",
                " ".join(p.tags).lower() if p.tags else "",
                p.description.lower() if p.description else "",
            ]
            searchable_text = " ".join(searchable_parts)

            # Check if any word matches
            if any(word in searchable_text for word in words):
                filtered.append(p)

        return filtered
