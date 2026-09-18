from dataclasses import dataclass, field

from app.services.store_detector import detect_platform
from app.services.stores.base import DiscoveredProduct


@dataclass
class DiscoveryResult:
    """Result from store discovery."""
    platform: str
    store_url: str
    total_found: int
    products: list[DiscoveredProduct]
    error: str | None = None
    confidence: float = 1.0
    platform_label: str = ""
    is_headless: bool = False
    matched_signals: list[str] = field(default_factory=list)


async def discover_products(
    url: str,
    keyword: str | None = None,
    limit: int = 50,
) -> DiscoveryResult:
    """
    Discover products from any store URL.

    Process:
    1. Detect platform type
    2. Fetch products using appropriate handler
    3. Filter by keyword if provided
    4. Return unified result

    Args:
        url: Store URL to scrape
        keyword: Optional filter keyword
        limit: Maximum products to return

    Returns:
        DiscoveryResult with platform info and products
    """
    handler = None

    try:
        # Detect platform and get handler
        handler = await detect_platform(url)

        # Fetch products
        products = await handler.fetch_products(url, keyword, limit)

        # Robust fallback: if platform-specific fetching yielded no products, try generalized web heuristics
        if not products and handler.platform_name != "custom":
            try:
                from app.services.stores.generic import GenericHandler
                fallback_handler = GenericHandler()
                fallback_products = await fallback_handler.fetch_products(url, keyword, limit)
                if fallback_products:
                    for p in fallback_products:
                        p.platform = handler.platform_name
                    products = fallback_products
            except Exception:
                pass

        return DiscoveryResult(
            platform=handler.platform_name,
            store_url=url,
            total_found=len(products),
            products=products,
            confidence=getattr(handler, "confidence", 1.0),
            platform_label=getattr(handler, "platform_label", handler.platform_name.capitalize()),
            is_headless=getattr(handler, "is_headless", False),
            matched_signals=list(getattr(handler, "matched_signals", [])),
        )

    except Exception as e:
        return DiscoveryResult(
            platform="unknown",
            store_url=url,
            total_found=0,
            products=[],
            error=str(e)[:200],
            confidence=0.0,
            platform_label="Unknown",
            is_headless=False,
            matched_signals=[],
        )

    finally:
        if handler:
            await handler.close()


async def discover_single_product(url: str) -> DiscoveredProduct | None:
    """
    Attempt to extract a single product from a product page URL.

    Useful for adding individual products to tracking.

    Args:
        url: Product page URL

    Returns:
        DiscoveredProduct if found, None otherwise
    """
    result = await discover_products(url, limit=1)

    if result.products:
        return result.products[0]

    return None
