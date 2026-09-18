from dataclasses import dataclass, field

from app.services.stores.base import BaseStoreHandler
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.generic import GenericHandler


# Handler priority order (most specific first)
HANDLER_CLASSES: list[type[BaseStoreHandler]] = [
    ShopifyHandler,
    WooCommerceHandler,
    GenericHandler,
]


@dataclass
class DetectionResult:
    """Detailed result of competitor store platform detection."""
    platform: str                    # "shopify", "woocommerce", "custom"
    platform_label: str              # "Shopify", "Shopify (Headless)", "WooCommerce", etc.
    confidence: float                # 0.0 to 1.0
    is_headless: bool = False
    matched_signals: list[str] = field(default_factory=list)
    handler: BaseStoreHandler | None = None


async def detect_store(url: str, html: str | None = None) -> DetectionResult:
    """
    Detect competitor store platform with transparent confidence and platform labels.
    Accurately recognizes modern headless setups, custom subdomains, and alternative URL patterns.

    Args:
        url: Store or product URL to analyze
        html: Optional pre-fetched HTML snippet

    Returns:
        DetectionResult containing platform identifier, descriptive label,
        confidence score, headless indicator, matched signals, and handler.
    """
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    for handler_class in HANDLER_CLASSES:
        handler = handler_class()
        try:
            matched = await handler.detect(url, html=html)
            if matched:
                return DetectionResult(
                    platform=handler.platform_name,
                    platform_label=getattr(handler, "platform_label", handler.platform_name.capitalize()),
                    confidence=getattr(handler, "confidence", 0.50),
                    is_headless=getattr(handler, "is_headless", False),
                    matched_signals=list(getattr(handler, "matched_signals", [])),
                    handler=handler,
                )
        except Exception:
            await handler.close()
            continue

    # Fallback to generic handler with generalized heuristics
    fallback_handler = GenericHandler()
    if html:
        try:
            await fallback_handler.detect(url, html=html)
        except Exception:
            pass
    return DetectionResult(
        platform="custom",
        platform_label=getattr(fallback_handler, "platform_label", "Custom (Generalized Heuristics)"),
        confidence=getattr(fallback_handler, "confidence", 0.50),
        is_headless=False,
        matched_signals=list(getattr(fallback_handler, "matched_signals", ["generic_fallback"])),
        handler=fallback_handler,
    )


async def detect_platform(url: str, html: str | None = None) -> BaseStoreHandler:
    """
    Detect store platform and return appropriate handler.
    Tries each handler in priority order until one matches.
    Falls back to GenericHandler with generalized heuristics if none match.

    Populates transparent platform_label, confidence, is_headless, and
    matched_signals on the returned handler instance.

    Args:
        url: Store URL to analyze
        html: Optional pre-fetched HTML content

    Returns:
        Appropriate handler instance for the detected platform
    """
    result = await detect_store(url, html=html)
    handler = result.handler or GenericHandler()
    handler.platform_name = result.platform
    handler.platform_label = result.platform_label
    handler.confidence = result.confidence
    handler.is_headless = result.is_headless
    handler.matched_signals = result.matched_signals
    return handler


async def get_handler_for_platform(platform: str) -> BaseStoreHandler:
    """
    Get handler instance for a specific platform name or label.

    Args:
        platform: Platform name or label (e.g. shopify, woocommerce, custom,
                  shopify (headless), woocommerce (headless))

    Returns:
        Handler instance for the platform
    """
    clean_platform = platform.lower().strip()
    if "shopify" in clean_platform:
        handler = ShopifyHandler()
        if "headless" in clean_platform:
            handler.is_headless = True
            handler.platform_label = "Shopify (Headless)"
            handler.confidence = 0.90
        return handler
    elif "woocommerce" in clean_platform or "woo" in clean_platform:
        handler = WooCommerceHandler()
        if "headless" in clean_platform:
            handler.is_headless = True
            handler.platform_label = "WooCommerce (Headless)"
            handler.confidence = 0.90
        return handler
    else:
        return GenericHandler()
