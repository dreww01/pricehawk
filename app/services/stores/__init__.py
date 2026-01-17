# Store handlers package
from app.services.stores.base import BaseStoreHandler, DiscoveredProduct
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.generic import GenericHandler

__all__ = [
    "BaseStoreHandler",
    "DiscoveredProduct",
    "ShopifyHandler",
    "WooCommerceHandler",
    "GenericHandler",
]
