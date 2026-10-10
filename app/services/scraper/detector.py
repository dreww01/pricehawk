"""Store platform classifier detecting Shopify, WooCommerce, and custom web stores."""
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services.stores.base import BaseStoreHandler
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.generic import GenericHandler

HANDLER_CLASSES: list[type[BaseStoreHandler]] = [ShopifyHandler, WooCommerceHandler, GenericHandler]


@dataclass
class PlatformDetectionResult:
    """Detailed result of store platform detection."""
    platform: str            # 'shopify', 'woocommerce', 'custom'
    platform_label: str      # 'Shopify', 'Shopify (Headless)', 'WooCommerce', etc.
    confidence: float        # 0.0 to 1.0
    handler: BaseStoreHandler
    signatures: list[str] = field(default_factory=list)


def classify_platform_from_url(url: str) -> tuple[str, str, float, list[str]] | None:
    """Classify platform purely from URL patterns, subdomains, and paths."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        hostname, path, query = (parsed.hostname or "").lower(), (parsed.path or "").lower(), (parsed.query or "").lower()
        signatures: list[str] = []

        if hostname.endswith(".myshopify.com") or hostname == "myshopify.com":
            signatures.append("url:myshopify_domain")
            return "shopify", "Shopify", 0.95, signatures

        shopify_path = False
        if "/products/" in path:
            signatures.append("url:shopify_products_path")
            shopify_path = True
        if "/collections/" in path:
            signatures.append("url:shopify_collections_path")
            shopify_path = True

        parts = hostname.split(".")
        is_sub = len(parts) >= 3 and parts[0] in {"shop", "store", "buy", "products", "checkout"}
        if is_sub:
            signatures.append(f"url:subdomain_{parts[0]}")

        if shopify_path and is_sub:
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

        if wc_path and is_sub:
            return "woocommerce", "WooCommerce", 0.85, signatures
        if shopify_path:
            return "shopify", "Shopify", 0.70, signatures
        if wc_path:
            return "woocommerce", "WooCommerce", 0.70, signatures
    except Exception:
        pass
    return None


def classify_platform_from_html(html: str, url: str = "") -> tuple[str, str, float, list[str]]:
    """Classify platform from HTML content, recognizing modern headless setups and subdomains."""
    if not html:
        url_classification = classify_platform_from_url(url)
        return url_classification if url_classification else ("custom", "Custom / Web Heuristics", 0.50, ["fallback:empty_html"])

    html_lower = html.lower()
    shopify_sigs, wc_sigs = [], []
    shopify_headless, wc_headless = False, False

    # 1. Shopify markers
    if any(m in html_lower for m in ["@shopify/hydrogen", "hydrogen.shopify.com", "oxygen.shopify.com", "x-shopify-oxygen", "data-hydrogen-app", "__shopify_dev_host__"]):
        shopify_sigs.append("shopify:hydrogen_headless")
        shopify_headless = True
    if any(m in html_lower for m in ["shopifybuy", "shopify-buy", "storefrontaccesstoken", "api/unstable/graphql.json", "api/2024-01/graphql.json", "api/2023-10/graphql.json"]):
        shopify_sigs.append("shopify:storefront_api_or_sdk")
        shopify_headless = True
    if ("__next_data__" in html_lower or "__remixcontext" in html_lower) and ("myshopify.com" in html_lower or "shopify" in html_lower):
        if any(m in html_lower for m in ["cdn.shopify.com", "shopifybuy", "productbyhandle"]):
            shopify_sigs.append("shopify:headless_hydration_state")
            shopify_headless = True
    if "cdn.shopify.com" in html_lower:
        shopify_sigs.append("shopify:cdn_assets")
    if any(m in html_lower for m in ["shopify.theme", "shopify.currency", "shopify.shop", "shopify.routes", "shopifyanalytics", "window.shopify"]):
        shopify_sigs.append("shopify:runtime_globals")
    if 'name="generator" content="shopify' in html_lower or 'content="shopify"' in html_lower:
        shopify_sigs.append("shopify:meta_generator")
    if any(p in html_lower for p in ['rel="preconnect" href="//cdn.shopify.com', 'rel="preconnect" href="https://cdn.shopify.com', 'rel="dns-prefetch" href="//cdn.shopify.com']):
        shopify_sigs.append("shopify:cdn_preconnect")
    if "myshopify.com" in html_lower:
        shopify_sigs.append("shopify:myshopify_references")

    # 2. WooCommerce markers
    if any(m in html_lower for m in ["cocart", "wpgraphql", "wp-graphql", "/wp-json/cocart/", "productvariation"]) and any(w in html_lower for w in ["woocommerce", "wc-", "wp-"]):
        wc_sigs.append("woocommerce:headless_api")
        wc_headless = True
    if "wp-content/plugins/woocommerce" in html_lower:
        wc_sigs.append("woocommerce:plugin_assets")
    if 'name="generator" content="woocommerce' in html_lower:
        wc_sigs.append("woocommerce:meta_generator")
    if any(m in html_lower for m in ["woocommerce_params", "wc_add_to_cart_params", "wc_cart_fragments_params", "wc_single_product_params", "woocommerce-js"]):
        wc_sigs.append("woocommerce:runtime_params")
    if any(m in html_lower for m in ["woocommerce-price-amount", "woocommerce-page", "wc-block", "wc-block-grid", "woocommerce-product-gallery"]):
        wc_sigs.append("woocommerce:dom_classes")
    if 'rel="https://api.w.org/"' in html_lower and ("wc" in html_lower or "woocommerce" in html_lower):
        wc_sigs.append("woocommerce:wp_api_link")

    # 3. Scoring
    url_res = classify_platform_from_url(url)
    if url_res:
        u_plat, _, _, u_sigs = url_res
        (shopify_sigs if u_plat == "shopify" else wc_sigs if u_plat == "woocommerce" else []).extend(u_sigs)

    shopify_score = 0.0
    if shopify_sigs:
        if "shopify:meta_generator" in shopify_sigs or ("shopify:cdn_assets" in shopify_sigs and "shopify:runtime_globals" in shopify_sigs):
            shopify_score = 0.95
        elif shopify_headless:
            shopify_score = 0.90
        elif "shopify:cdn_assets" in shopify_sigs:
            shopify_score = 0.85
        else:
            shopify_score = 0.75
        shopify_score = min(shopify_score, 0.98)

    wc_score = 0.0
    if wc_sigs:
        if "woocommerce:meta_generator" in wc_sigs or ("woocommerce:plugin_assets" in wc_sigs and "woocommerce:runtime_params" in wc_sigs):
            wc_score = 0.95
        elif wc_headless:
            wc_score = 0.90
        elif "woocommerce:plugin_assets" in wc_sigs or "woocommerce:dom_classes" in wc_sigs:
            wc_score = 0.85
        else:
            wc_score = 0.75
        wc_score = min(wc_score, 0.98)

    if shopify_score >= 0.70 and shopify_score >= wc_score:
        return "shopify", ("Shopify (Headless)" if shopify_headless else "Shopify"), round(shopify_score, 2), shopify_sigs
    if wc_score >= 0.70 and wc_score > shopify_score:
        return "woocommerce", ("WooCommerce (Headless)" if wc_headless else "WooCommerce"), round(wc_score, 2), wc_sigs

    # 4. Fallback heuristics
    heuristic_sigs = []
    has_schema = False
    if 'application/ld+json' in html_lower and any(w in html_lower for w in ['"product"', '"price"', '"offers"']):
        heuristic_sigs.append("heuristics:schema_product_or_offer")
        has_schema = True
    if any(m in html_lower for m in ['property="product:price:amount"', 'property="og:price:amount"', 'itemprop="price"', 'itemprop="lowprice"']):
        heuristic_sigs.append("heuristics:meta_price_tags")
    if any(a in html_lower for a in ['data-price', 'data-product-price', 'data-regular-price', 'data-sale-price']):
        heuristic_sigs.append("heuristics:data_price_attributes")

    if heuristic_sigs:
        return "custom", "Custom / Web Heuristics", (0.65 if has_schema else 0.60), heuristic_sigs
    return "custom", "Custom / Web Heuristics", 0.50, ["fallback:generalized_heuristics"]


def detect_platform_from_html(html: str) -> str | None:
    """Detect e-commerce platform from HTML content (Shopify, WooCommerce, or None for custom)."""
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
    norm_platform = (platform or "").lower()
    handler = platform_map.get(norm_platform, GenericHandler)()
    if norm_platform in ("shopify", "woocommerce"):
        handler.platform_label = "Shopify" if norm_platform == "shopify" else "WooCommerce"
        handler.confidence = 0.95
    else:
        handler.platform_label = "Custom / Web Heuristics"
        handler.confidence = 0.50
    return handler


async def _create_detection_result(platform: str, label: str, conf: float, sigs: list[str], handler: BaseStoreHandler | None = None) -> PlatformDetectionResult:
    if handler is None:
        handler = await get_handler_for_platform(platform)
    handler.platform_label, handler.confidence, handler.signatures = label, conf, sigs
    return PlatformDetectionResult(platform=platform, platform_label=label, confidence=conf, handler=handler, signatures=sigs)


async def detect_platform_details(url: str, html: str | None = None) -> PlatformDetectionResult:
    """Detect store platform with detailed confidence, platform labels, and signatures."""
    if html:
        p, lbl, conf, sigs = classify_platform_from_html(html, url)
        return await _create_detection_result(p, lbl, conf, sigs)

    url_cls = classify_platform_from_url(url)
    if url_cls and url_cls[2] >= 0.95:
        return await _create_detection_result(url_cls[0], url_cls[1], url_cls[2], url_cls[3])

    for handler_cls in [ShopifyHandler, WooCommerceHandler]:
        handler = handler_cls()
        try:
            if await handler.detect(url):
                lbl = getattr(handler, "platform_label", handler.platform_name.title())
                conf = getattr(handler, "confidence", 0.95)
                sigs = getattr(handler, "signatures", [f"{handler.platform_name}:detected"])
                return await _create_detection_result(handler.platform_name, lbl, conf, sigs, handler=handler)
        except Exception:
            await handler.close()

    generic = GenericHandler()
    try:
        await generic.detect(url)
    except Exception:
        pass
    lbl = getattr(generic, "platform_label", "Custom / Web Heuristics")
    conf = getattr(generic, "confidence", 0.50)
    sigs = getattr(generic, "signatures", ["fallback:generalized_heuristics"])
    return await _create_detection_result(generic.platform_name, lbl, conf, sigs, handler=generic)


async def detect_platform(url: str, html: str | None = None) -> BaseStoreHandler:
    """Detect store platform and return appropriate handler instance."""
    return (await detect_platform_details(url, html=html)).handler
