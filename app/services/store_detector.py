from dataclasses import dataclass, field
import json
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

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
class PlatformDetectionResult:
    """Detailed result of store platform detection."""
    platform: str            # 'shopify', 'woocommerce', 'custom'
    platform_label: str      # 'Shopify', 'Shopify (Headless)', 'WooCommerce', etc.
    confidence: float        # 0.0 to 1.0
    handler: BaseStoreHandler
    signatures: list[str] = field(default_factory=list)


def classify_platform_from_url(url: str) -> tuple[str, str, float, list[str]] | None:
    """
    Classify platform purely from URL patterns, subdomains, and paths.

    Returns:
        (platform, platform_label, confidence, signatures) or None if no specific pattern matched.
    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        signatures: list[str] = []

        # 1. Direct myshopify.com subdomain
        if hostname.endswith(".myshopify.com") or hostname == "myshopify.com":
            signatures.append("url:myshopify_domain")
            return "shopify", "Shopify", 0.95, signatures

        # 2. Alternative Shopify URL patterns
        shopify_path_match = False
        if "/products/" in path:
            signatures.append("url:shopify_products_path")
            shopify_path_match = True
        if "/collections/" in path:
            signatures.append("url:shopify_collections_path")
            shopify_path_match = True

        # Custom subdomains commonly used for Shopify stores
        is_commerce_subdomain = False
        parts = hostname.split(".")
        if len(parts) >= 3:
            subdomain = parts[0]
            if subdomain in {"shop", "store", "buy", "products", "checkout"}:
                is_commerce_subdomain = True
                signatures.append(f"url:subdomain_{subdomain}")

        # If both commerce subdomain and Shopify path structure match
        if shopify_path_match and is_commerce_subdomain:
            return "shopify", "Shopify", 0.85, signatures

        # 3. Alternative WooCommerce URL patterns
        wc_path_match = False
        if "/product/" in path:
            signatures.append("url:woocommerce_product_path")
            wc_path_match = True
        elif "/product-category/" in path:
            signatures.append("url:woocommerce_category_path")
            wc_path_match = True
        elif "post_type=product" in query:
            signatures.append("url:woocommerce_query_param")
            wc_path_match = True
        elif "rest_route=/wc" in query:
            signatures.append("url:woocommerce_rest_route")
            return "woocommerce", "WooCommerce", 0.90, signatures

        if wc_path_match and is_commerce_subdomain:
            return "woocommerce", "WooCommerce", 0.85, signatures

        if shopify_path_match:
            return "shopify", "Shopify", 0.70, signatures

        if wc_path_match:
            return "woocommerce", "WooCommerce", 0.70, signatures

    except Exception:
        pass

    return None


def classify_platform_from_html(html: str, url: str = "") -> tuple[str, str, float, list[str]]:
    """
    Classify platform from HTML content, recognizing modern headless setups,
    subdomains, and alternative URL structures.

    Returns:
        (platform, platform_label, confidence, signatures)
    """
    if not html:
        url_classification = classify_platform_from_url(url)
        if url_classification:
            return url_classification
        return "custom", "Custom / Web Heuristics", 0.50, ["fallback:empty_html"]

    html_lower = html.lower()
    shopify_signatures: list[str] = []
    wc_signatures: list[str] = []
    shopify_is_headless = False
    wc_is_headless = False

    # -------------------------------------------------------------------------
    # 1. Check Shopify signatures (including modern headless Hydrogen / Remix)
    # -------------------------------------------------------------------------
    # Headless Hydrogen / Oxygen markers
    if any(marker in html_lower for marker in [
        "@shopify/hydrogen",
        "hydrogen.shopify.com",
        "oxygen.shopify.com",
        "x-shopify-oxygen",
        "data-hydrogen-app",
        "__shopify_dev_host__",
    ]):
        shopify_signatures.append("shopify:hydrogen_headless")
        shopify_is_headless = True

    # Headless Shopify Storefront / Buy SDK markers
    if any(marker in html_lower for marker in [
        "shopifybuy",
        "shopify-buy",
        "storefrontaccesstoken",
        "api/unstable/graphql.json",
        "api/2024-01/graphql.json",
        "api/2023-10/graphql.json",
    ]):
        shopify_signatures.append("shopify:storefront_api_or_sdk")
        shopify_is_headless = True

    # Next.js or Remix context containing Shopify handles / storefront info
    if "__next_data__" in html_lower or "__remixcontext" in html_lower:
        if "myshopify.com" in html_lower or "shopify" in html_lower:
            if "cdn.shopify.com" in html_lower or "shopifybuy" in html_lower or "productbyhandle" in html_lower:
                shopify_signatures.append("shopify:headless_hydration_state")
                shopify_is_headless = True

    # Standard Shopify CDN assets
    if "cdn.shopify.com" in html_lower:
        shopify_signatures.append("shopify:cdn_assets")

    # Standard Shopify Theme and runtime objects
    if any(marker in html_lower for marker in [
        "shopify.theme",
        "shopify.currency",
        "shopify.shop",
        "shopify.routes",
        "shopifyanalytics",
        "window.shopify",
    ]):
        shopify_signatures.append("shopify:runtime_globals")

    # Meta generator or meta tags
    if 'name="generator" content="shopify' in html_lower or 'content="shopify"' in html_lower:
        shopify_signatures.append("shopify:meta_generator")

    # Preconnect / dns-prefetch links to Shopify CDN
    if ('rel="preconnect" href="//cdn.shopify.com' in html_lower or
        'rel="preconnect" href="https://cdn.shopify.com' in html_lower or
        'rel="dns-prefetch" href="//cdn.shopify.com' in html_lower):
        shopify_signatures.append("shopify:cdn_preconnect")

    # Checkout / Cart actions pointing to myshopify.com
    if "myshopify.com" in html_lower:
        shopify_signatures.append("shopify:myshopify_references")

    # -------------------------------------------------------------------------
    # 2. Check WooCommerce signatures (including modern headless setups)
    # -------------------------------------------------------------------------
    # Headless WooCommerce: WPGraphQL, CoCart, Faust.js
    if any(marker in html_lower for marker in [
        "cocart",
        "wpgraphql",
        "wp-graphql",
        "/wp-json/cocart/",
        "productvariation",
    ]) and ("woocommerce" in html_lower or "wc-" in html_lower or "wp-" in html_lower):
        wc_signatures.append("woocommerce:headless_api")
        wc_is_headless = True

    # WooCommerce plugin asset paths
    if "wp-content/plugins/woocommerce" in html_lower:
        wc_signatures.append("woocommerce:plugin_assets")

    # Meta generator
    if 'name="generator" content="woocommerce' in html_lower:
        wc_signatures.append("woocommerce:meta_generator")

    # JavaScript runtime parameter blocks
    if any(marker in html_lower for marker in [
        "woocommerce_params",
        "wc_add_to_cart_params",
        "wc_cart_fragments_params",
        "wc_single_product_params",
        "woocommerce-js",
    ]):
        wc_signatures.append("woocommerce:runtime_params")

    # CSS classes and DOM markers
    if any(marker in html_lower for marker in [
        "woocommerce-price-amount",
        "woocommerce-page",
        "wc-block",
        "wc-block-grid",
        "woocommerce-product-gallery",
    ]):
        wc_signatures.append("woocommerce:dom_classes")

    # Link tag to WP REST API with WooCommerce namespace
    if 'rel="https://api.w.org/"' in html_lower:
        if "wc" in html_lower or "woocommerce" in html_lower:
            wc_signatures.append("woocommerce:wp_api_link")

    # -------------------------------------------------------------------------
    # 3. Calculate scores & determine classification
    # -------------------------------------------------------------------------
    # Incorporate URL signals if available
    url_result = classify_platform_from_url(url)
    if url_result:
        u_plat, _, u_conf, u_sigs = url_result
        if u_plat == "shopify":
            shopify_signatures.extend(u_sigs)
        elif u_plat == "woocommerce":
            wc_signatures.extend(u_sigs)

    shopify_score = 0.0
    if shopify_signatures:
        # Base score on strength of signatures
        if "shopify:meta_generator" in shopify_signatures:
            shopify_score += 0.95
        elif "shopify:cdn_assets" in shopify_signatures and "shopify:runtime_globals" in shopify_signatures:
            shopify_score += 0.95
        elif shopify_is_headless:
            shopify_score += 0.90
        elif "shopify:cdn_assets" in shopify_signatures:
            shopify_score += 0.85
        else:
            shopify_score += 0.75

        # Cap at 0.98
        shopify_score = min(shopify_score, 0.98)

    wc_score = 0.0
    if wc_signatures:
        if "woocommerce:meta_generator" in wc_signatures:
            wc_score += 0.95
        elif "woocommerce:plugin_assets" in wc_signatures and "woocommerce:runtime_params" in wc_signatures:
            wc_score += 0.95
        elif wc_is_headless:
            wc_score += 0.90
        elif "woocommerce:plugin_assets" in wc_signatures or "woocommerce:dom_classes" in wc_signatures:
            wc_score += 0.85
        else:
            wc_score += 0.75

        wc_score = min(wc_score, 0.98)

    # Determine winner
    if shopify_score >= 0.70 and shopify_score >= wc_score:
        label = "Shopify (Headless)" if shopify_is_headless else "Shopify"
        return "shopify", label, round(shopify_score, 2), shopify_signatures

    if wc_score >= 0.70 and wc_score > shopify_score:
        label = "WooCommerce (Headless)" if wc_is_headless else "WooCommerce"
        return "woocommerce", label, round(wc_score, 2), wc_signatures

    # -------------------------------------------------------------------------
    # 4. Generalized Web Heuristics Fallback
    # -------------------------------------------------------------------------
    # Check if page has generalized eCommerce heuristics (Schema.org Product, OpenGraph, price selectors)
    heuristic_signatures: list[str] = []
    has_schema_product = False

    if 'application/ld+json' in html_lower and ('"product"' in html_lower or '"price"' in html_lower or '"offers"' in html_lower):
        heuristic_signatures.append("heuristics:schema_product_or_offer")
        has_schema_product = True

    if any(meta in html_lower for meta in [
        'property="product:price:amount"',
        'property="og:price:amount"',
        'itemprop="price"',
        'itemprop="lowprice"',
    ]):
        heuristic_signatures.append("heuristics:meta_price_tags")

    if any(attr in html_lower for attr in [
        'data-price',
        'data-product-price',
        'data-regular-price',
        'data-sale-price',
    ]):
        heuristic_signatures.append("heuristics:data_price_attributes")

    if heuristic_signatures:
        confidence = 0.65 if has_schema_product else 0.60
        return "custom", "Custom / Web Heuristics", confidence, heuristic_signatures

    return "custom", "Custom / Web Heuristics", 0.50, ["fallback:generalized_heuristics"]


async def detect_platform_details(url: str, html: str | None = None) -> PlatformDetectionResult:
    """
    Detect store platform with detailed confidence, platform labels, and signatures.

    Args:
        url: Store URL to analyze
        html: Optional HTML string to classify directly without fetching

    Returns:
        PlatformDetectionResult containing platform, label, confidence, handler, and signatures
    """
    # Fast path: HTML already provided
    if html:
        platform, label, confidence, signatures = classify_platform_from_html(html, url)
        handler = await get_handler_for_platform(platform)
        handler.platform_label = label
        handler.confidence = confidence
        handler.signatures = signatures
        return PlatformDetectionResult(
            platform=platform,
            platform_label=label,
            confidence=confidence,
            handler=handler,
            signatures=signatures,
        )

    # 1. URL pattern check (e.g. *.myshopify.com)
    url_classification = classify_platform_from_url(url)
    if url_classification and url_classification[2] >= 0.95:
        platform, label, confidence, signatures = url_classification
        handler = await get_handler_for_platform(platform)
        handler.platform_label = label
        handler.confidence = confidence
        handler.signatures = signatures
        return PlatformDetectionResult(
            platform=platform,
            platform_label=label,
            confidence=confidence,
            handler=handler,
            signatures=signatures,
        )

    # 2. Try specialized handlers in order (Shopify, WooCommerce)
    for handler_class in [ShopifyHandler, WooCommerceHandler]:
        handler = handler_class()
        try:
            if await handler.detect(url):
                label = getattr(handler, "platform_label", handler.platform_name.title())
                confidence = getattr(handler, "confidence", 0.95)
                signatures = getattr(handler, "signatures", [f"{handler.platform_name}:detected"])
                return PlatformDetectionResult(
                    platform=handler.platform_name,
                    platform_label=label,
                    confidence=confidence,
                    handler=handler,
                    signatures=signatures,
                )
        except Exception:
            await handler.close()
            continue

    # 3. Fallback to GenericHandler with generalized web heuristics
    generic_handler = GenericHandler()
    try:
        await generic_handler.detect(url)
    except Exception:
        pass

    label = getattr(generic_handler, "platform_label", "Custom / Web Heuristics")
    confidence = getattr(generic_handler, "confidence", 0.50)
    signatures = getattr(generic_handler, "signatures", ["fallback:generalized_heuristics"])

    return PlatformDetectionResult(
        platform=generic_handler.platform_name,
        platform_label=label,
        confidence=confidence,
        handler=generic_handler,
        signatures=signatures,
    )


async def detect_platform(url: str, html: str | None = None) -> BaseStoreHandler:
    """
    Detect store platform and return appropriate handler instance with
    platform_label, confidence, and signatures attached.

    Args:
        url: Store URL to analyze
        html: Optional HTML string to analyze directly

    Returns:
        Appropriate handler instance for the detected platform
    """
    details = await detect_platform_details(url, html=html)
    return details.handler


async def get_handler_for_platform(platform: str) -> BaseStoreHandler:
    """
    Get handler instance for a specific platform name.

    Args:
        platform: Platform name (shopify, woocommerce, custom)

    Returns:
        Handler instance for the platform
    """
    platform_map = {
        "shopify": ShopifyHandler,
        "woocommerce": WooCommerceHandler,
        "custom": GenericHandler,
    }

    norm_platform = (platform or "").lower()
    handler_class = platform_map.get(norm_platform, GenericHandler)
    handler = handler_class()

    if norm_platform == "shopify":
        handler.platform_label = "Shopify"
        handler.confidence = 0.95
    elif norm_platform == "woocommerce":
        handler.platform_label = "WooCommerce"
        handler.confidence = 0.95
    else:
        handler.platform_label = "Custom / Web Heuristics"
        handler.confidence = 0.50

    return handler
