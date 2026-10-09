"""Store platform detection and classification."""
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services.stores.base import BaseStoreHandler
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.generic import GenericHandler

HANDLER_CLASSES: list[type[BaseStoreHandler]] = [
    ShopifyHandler,
    WooCommerceHandler,
    GenericHandler,
]

COMMERCE_SUBDOMAINS = {"shop", "store", "buy", "products", "checkout"}
HYDROGEN_MARKERS = (
    "@shopify/hydrogen", "hydrogen.shopify.com", "oxygen.shopify.com",
    "x-shopify-oxygen", "data-hydrogen-app", "__shopify_dev_host__",
)
SHOPIFY_API_MARKERS = (
    "shopifybuy", "shopify-buy", "storefrontaccesstoken",
    "api/unstable/graphql.json", "api/2024-01/graphql.json", "api/2023-10/graphql.json",
)
SHOPIFY_GLOBALS = (
    "shopify.theme", "shopify.currency", "shopify.shop",
    "shopify.routes", "shopifyanalytics", "window.shopify",
)
WC_HEADLESS_MARKERS = ("cocart", "wpgraphql", "wp-graphql", "/wp-json/cocart/", "productvariation")
WC_PARAMS = (
    "woocommerce_params", "wc_add_to_cart_params", "wc_cart_fragments_params",
    "wc_single_product_params", "woocommerce-js",
)
WC_DOM = (
    "woocommerce-price-amount", "woocommerce-page", "wc-block",
    "wc-block-grid", "woocommerce-product-gallery",
)
HEURISTIC_META_PRICE = (
    'property="product:price:amount"', 'property="og:price:amount"',
    'itemprop="price"', 'itemprop="lowprice"',
)
HEURISTIC_DATA_PRICE = (
    "data-price", "data-product-price", "data-regular-price", "data-sale-price",
)


@dataclass
class PlatformDetectionResult:
    """Detailed result of store platform detection."""
    platform: str
    platform_label: str
    confidence: float
    handler: BaseStoreHandler
    signatures: list[str] = field(default_factory=list)


def classify_platform_from_url(url: str) -> tuple[str, str, float, list[str]] | None:
    """Classify platform purely from URL patterns, subdomains, and paths."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        signatures: list[str] = []

        if hostname.endswith(".myshopify.com") or hostname == "myshopify.com":
            signatures.append("url:myshopify_domain")
            return "shopify", "Shopify", 0.95, signatures

        shopify_path = "/products/" in path or "/collections/" in path
        if "/products/" in path:
            signatures.append("url:shopify_products_path")
        if "/collections/" in path:
            signatures.append("url:shopify_collections_path")

        parts = hostname.split(".")
        is_comm_sub = len(parts) >= 3 and parts[0] in COMMERCE_SUBDOMAINS
        if is_comm_sub:
            signatures.append(f"url:subdomain_{parts[0]}")
        if shopify_path and is_comm_sub:
            return "shopify", "Shopify", 0.85, signatures

        wc_path = False
        if "/product/" in path:
            signatures.append("url:woocommerce_product_path")
            wc_path = True
        elif "/product-category/" in path:
            signatures.append("url:woocommerce_category_path")
            wc_path = True
        elif "post_type=product" in query:
            signatures.append("url:woocommerce_query_param")
            wc_path = True
        elif "rest_route=/wc" in query:
            signatures.append("url:woocommerce_rest_route")
            return "woocommerce", "WooCommerce", 0.90, signatures

        if wc_path and is_comm_sub:
            return "woocommerce", "WooCommerce", 0.85, signatures
        if shopify_path:
            return "shopify", "Shopify", 0.70, signatures
        if wc_path:
            return "woocommerce", "WooCommerce", 0.70, signatures
    except Exception:
        pass
    return None


def _calc_score(sigs: list[str], headless: bool, meta: str, assets: str, globals_k: str, dom: str | None = None) -> float:
    if not sigs:
        return 0.0
    if meta in sigs or (assets in sigs and globals_k in sigs):
        score = 0.95
    elif headless:
        score = 0.90
    elif assets in sigs or (dom and dom in sigs):
        score = 0.85
    else:
        score = 0.75
    return min(score, 0.98)


def classify_platform_from_html(html: str, url: str = "") -> tuple[str, str, float, list[str]]:
    """Classify platform from HTML content, headless setups, and URL structure."""
    if not html:
        url_res = classify_platform_from_url(url)
        return url_res if url_res else ("custom", "Custom / Web Heuristics", 0.50, ["fallback:empty_html"])

    html_lower = html.lower()
    shopify_sigs, wc_sigs = [], []
    shopify_headless, wc_headless = False, False

    if any(m in html_lower for m in HYDROGEN_MARKERS):
        shopify_sigs.append("shopify:hydrogen_headless")
        shopify_headless = True
    if any(m in html_lower for m in SHOPIFY_API_MARKERS):
        shopify_sigs.append("shopify:storefront_api_or_sdk")
        shopify_headless = True
    if ("__next_data__" in html_lower or "__remixcontext" in html_lower) and ("myshopify.com" in html_lower or "shopify" in html_lower):
        if any(k in html_lower for k in ("cdn.shopify.com", "shopifybuy", "productbyhandle")):
            shopify_sigs.append("shopify:headless_hydration_state")
            shopify_headless = True
    if "cdn.shopify.com" in html_lower:
        shopify_sigs.append("shopify:cdn_assets")
    if any(m in html_lower for m in SHOPIFY_GLOBALS):
        shopify_sigs.append("shopify:runtime_globals")
    if 'name="generator" content="shopify' in html_lower or 'content="shopify"' in html_lower:
        shopify_sigs.append("shopify:meta_generator")
    if any(k in html_lower for k in ('rel="preconnect" href="//cdn.shopify.com', 'rel="preconnect" href="https://cdn.shopify.com', 'rel="dns-prefetch" href="//cdn.shopify.com')):
        shopify_sigs.append("shopify:cdn_preconnect")
    if "myshopify.com" in html_lower:
        shopify_sigs.append("shopify:myshopify_references")

    if any(m in html_lower for m in WC_HEADLESS_MARKERS) and any(k in html_lower for k in ("woocommerce", "wc-", "wp-")):
        wc_sigs.append("woocommerce:headless_api")
        wc_headless = True
    if "wp-content/plugins/woocommerce" in html_lower:
        wc_sigs.append("woocommerce:plugin_assets")
    if 'name="generator" content="woocommerce' in html_lower:
        wc_sigs.append("woocommerce:meta_generator")
    if any(m in html_lower for m in WC_PARAMS):
        wc_sigs.append("woocommerce:runtime_params")
    if any(m in html_lower for m in WC_DOM):
        wc_sigs.append("woocommerce:dom_classes")
    if 'rel="https://api.w.org/"' in html_lower and ("wc" in html_lower or "woocommerce" in html_lower):
        wc_sigs.append("woocommerce:wp_api_link")

    url_res = classify_platform_from_url(url)
    if url_res:
        u_plat, _, _, u_sigs = url_res
        (shopify_sigs if u_plat == "shopify" else wc_sigs).extend(u_sigs)

    shopify_score = _calc_score(shopify_sigs, shopify_headless, "shopify:meta_generator", "shopify:cdn_assets", "shopify:runtime_globals")
    wc_score = _calc_score(wc_sigs, wc_headless, "woocommerce:meta_generator", "woocommerce:plugin_assets", "woocommerce:runtime_params", "woocommerce:dom_classes")

    if shopify_score >= 0.70 and shopify_score >= wc_score:
        return "shopify", ("Shopify (Headless)" if shopify_headless else "Shopify"), round(shopify_score, 2), shopify_sigs
    if wc_score >= 0.70 and wc_score > shopify_score:
        return "woocommerce", ("WooCommerce (Headless)" if wc_headless else "WooCommerce"), round(wc_score, 2), wc_sigs

    heuristic_sigs: list[str] = []
    has_schema = False
    if 'application/ld+json' in html_lower and any(k in html_lower for k in ('"product"', '"price"', '"offers"')):
        heuristic_sigs.append("heuristics:schema_product_or_offer")
        has_schema = True
    if any(m in html_lower for m in HEURISTIC_META_PRICE):
        heuristic_sigs.append("heuristics:meta_price_tags")
    if any(a in html_lower for a in HEURISTIC_DATA_PRICE):
        heuristic_sigs.append("heuristics:data_price_attributes")

    if heuristic_sigs:
        return "custom", "Custom / Web Heuristics", (0.65 if has_schema else 0.60), heuristic_sigs
    return "custom", "Custom / Web Heuristics", 0.50, ["fallback:generalized_heuristics"]


def detect_platform_from_html(html: str) -> str | None:
    """Detect e-commerce platform from HTML content, supporting headless and subdomains."""
    if not html:
        return None
    try:
        platform, _, confidence, _ = classify_platform_from_html(html)
        if platform in ("shopify", "woocommerce") and confidence >= 0.70:
            return platform
    except Exception:
        pass
    html_lower = html.lower()
    if "shopify" in html_lower or "cdn.shopify" in html_lower:
        return "shopify"
    if "woocommerce" in html_lower or "wc-block" in html_lower:
        return "woocommerce"
    return None


async def get_handler_for_platform(platform: str) -> BaseStoreHandler:
    """Get handler instance for a specific platform name."""
    platform_map = {"shopify": ShopifyHandler, "woocommerce": WooCommerceHandler, "custom": GenericHandler}
    norm = (platform or "").lower()
    handler = platform_map.get(norm, GenericHandler)()
    if norm in ("shopify", "woocommerce"):
        handler.platform_label = "Shopify" if norm == "shopify" else "WooCommerce"
        handler.confidence = 0.95
    else:
        handler.platform_label = "Custom / Web Heuristics"
        handler.confidence = 0.50
    return handler


async def detect_platform_details(url: str, html: str | None = None) -> PlatformDetectionResult:
    """Detect store platform with detailed confidence, platform labels, and signatures."""
    if html:
        platform, label, conf, sigs = classify_platform_from_html(html, url)
        handler = await get_handler_for_platform(platform)
        handler.platform_label, handler.confidence, handler.signatures = label, conf, sigs
        return PlatformDetectionResult(platform, label, conf, handler, sigs)

    url_res = classify_platform_from_url(url)
    if url_res and url_res[2] >= 0.95:
        platform, label, conf, sigs = url_res
        handler = await get_handler_for_platform(platform)
        handler.platform_label, handler.confidence, handler.signatures = label, conf, sigs
        return PlatformDetectionResult(platform, label, conf, handler, sigs)

    for h_cls in [ShopifyHandler, WooCommerceHandler]:
        handler = h_cls()
        try:
            if await handler.detect(url):
                label = getattr(handler, "platform_label", handler.platform_name.title())
                conf = getattr(handler, "confidence", 0.95)
                sigs = getattr(handler, "signatures", [f"{handler.platform_name}:detected"])
                return PlatformDetectionResult(handler.platform_name, label, conf, handler, sigs)
        except Exception:
            await handler.close()

    generic = GenericHandler()
    try:
        await generic.detect(url)
    except Exception:
        pass
    label = getattr(generic, "platform_label", "Custom / Web Heuristics")
    conf = getattr(generic, "confidence", 0.50)
    sigs = getattr(generic, "signatures", ["fallback:generalized_heuristics"])
    return PlatformDetectionResult(generic.platform_name, label, conf, generic, sigs)


async def detect_platform(url: str, html: str | None = None) -> BaseStoreHandler:
    """Detect store platform and return appropriate handler instance."""
    details = await detect_platform_details(url, html=html)
    return details.handler


class PlatformDetector:
    """Class classifier facade for store platform detection."""
    classify_url = staticmethod(classify_platform_from_url)
    classify_html = staticmethod(classify_platform_from_html)
    detect_details = staticmethod(detect_platform_details)
    detect = staticmethod(detect_platform)
    get_handler = staticmethod(get_handler_for_platform)
