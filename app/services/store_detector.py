from app.services.stores.base import BaseStoreHandler
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.amazon import AmazonHandler
from app.services.stores.ebay import EbayHandler
from app.services.stores.generic import GenericHandler


# Handler priority order (most specific first)
HANDLER_CLASSES: list[type[BaseStoreHandler]] = [
    ShopifyHandler,
    WooCommerceHandler,
    AmazonHandler,
    EbayHandler,
    GenericHandler,
]


async def detect_platform(url: str) -> BaseStoreHandler:
    """
    Detect store platform and return appropriate handler.

    Tries each handler in priority order until one matches.
    Falls back to GenericHandler if none match.

    Args:
        url: Store URL to analyze

    Returns:
        Appropriate handler instance for the detected platform
    """
    for handler_class in HANDLER_CLASSES:
        handler = handler_class()
        try:
            if await handler.detect(url):
                return handler
        except Exception:
            await handler.close()
            continue

    # Fallback to generic handler
    return GenericHandler()


async def get_handler_for_platform(platform: str) -> BaseStoreHandler:
    """
    Get handler instance for a specific platform name.

    Args:
        platform: Platform name (shopify, woocommerce, amazon, ebay, custom)

    Returns:
        Handler instance for the platform
    """
    platform_map = {
        "shopify": ShopifyHandler,
        "woocommerce": WooCommerceHandler,
        "amazon": AmazonHandler,
        "ebay": EbayHandler,
        "custom": GenericHandler,
    }

    handler_class = platform_map.get(platform.lower(), GenericHandler)
    return handler_class()
