"""
Unit and regression tests for store platform detection resilience and fallbacks:
1. Classic Shopify store detection (HTML signatures, custom subdomains, URL patterns)
2. Modern headless Shopify detection (Hydrogen, Remix Oxygen, Storefront API, Next.js Commerce, Shopify Buy SDK)
3. Classic WooCommerce store detection (HTML signatures, custom subdomains, URL patterns)
4. Modern headless WooCommerce detection (WPGraphQL / WooGraphQL, CoCart, Next.js WooCommerce)
5. Custom platform detection fallback with generalized web heuristics (Schema.org, OpenGraph, Microdata, Hydration, Heuristic selectors)
6. Robust price and currency extraction heuristics (international currencies, European formats)
7. Transparent detection confidence and platform labels
8. Store discovery route transparency
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest

from app.services.store_detector import (
    DetectionResult,
    detect_store,
    detect_platform,
    get_handler_for_platform,
)
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.generic import GenericHandler
from app.services.scraper_service import (
    detect_platform_from_html,
    extract_price_from_html,
    parse_price,
)


# ============================================================================
# Representative HTML Snippets
# ============================================================================

CLASSIC_SHOPIFY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Classic Apparel Co. - Denim Jacket</title>
    <link rel="dns-prefetch" href="//cdn.shopify.com">
    <link rel="preconnect" href="https://cdn.shopify.com">
    <meta name="shopify-checkout-api-token" content="abc123token">
    <meta name="shopify-digital-wallet" content="/12345/digital_wallets/dialog">
    <script>
        window.Shopify = window.Shopify || { theme: { name: "Dawn" }, routes: { root: "/" } };
        var ShopifyAnalytics = { lib: "trekkie" };
    </script>
</head>
<body class="template-product">
    <div id="shopify-section-header" class="shopify-section"></div>
    <div class="product__info-container">
        <h1 class="product__title">Denim Jacket</h1>
        <div class="price-item price-item--regular" data-product-price>
            <span class="money">$89.50</span>
        </div>
        <button type="submit" name="add" class="shopify-payment-button">Add to cart</button>
    </div>
</body>
</html>
"""

HEADLESS_HYDROGEN_SHOPIFY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Hydrogen Storefront - Cool Kicks</title>
    <!-- Built with @shopify/hydrogen and Remix Oxygen -->
    <meta name="generator" content="Shopify Hydrogen / Oxygen">
    <script>
        window.__HYDROGEN_STATE__ = {
            storefrontAccessToken: "shpat_headless_token_123",
            storeDomain: "cool-kicks.myshopify.com"
        };
        window.__SHOPIFY_DEV_HOST__ = "oxygen-v1";
    </script>
</head>
<body>
    <div id="root">
        <main class="product-page">
            <h1>Cool Kicks</h1>
            <div class="price font-bold text-lg" data-test="price">$145.00</div>
            <button data-storefront-action="add-to-cart">Add to Bag</button>
        </main>
    </div>
</body>
</html>
"""

HEADLESS_NEXTJS_SHOPIFY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Modern Goods - Next.js Commerce</title>
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "product": {
                    "handle": "premium-leather-bag",
                    "title": "Premium Leather Bag",
                    "price": "220.00",
                    "currencyCode": "USD",
                    "shopifyStorefront": { "shopId": "gid://shopify/Shop/123" }
                }
            }
        },
        "page": "/products/[handle]",
        "query": { "handle": "premium-leather-bag" }
    }
    </script>
</head>
<body>
    <div id="__next">
        <div class="commerce-container">
            <h1>Premium Leather Bag</h1>
            <span class="price-tag">$220.00</span>
        </div>
    </div>
</body>
</html>
"""

HEADLESS_SHOPIFY_BUY_SDK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Custom Boutique - Silk Scarf</title>
    <script src="https://sdks.shopifycdn.com/js-buy-sdk/v2/latest/shopify-buy.umd.polyfilled.min.js"></script>
    <script>
        var client = ShopifyBuy.buildClient({
            domain: "boutique-store.myshopify.com",
            storefrontAccessToken: "token9988"
        });
    </script>
</head>
<body>
    <h1>Silk Scarf</h1>
    <div class="product-price">$45.00</div>
</body>
</html>
"""

CLASSIC_WOOCOMMERCE_HTML = """
<!DOCTYPE html>
<html lang="en-US">
<head>
    <meta charset="UTF-8">
    <title>WordPress Gear - Organic Cotton T-Shirt &#8211; WooCommerce Store</title>
    <meta name="generator" content="WooCommerce 8.5.2" />
    <link rel="https://api.w.org/" href="https://example-woo.com/wp-json/" />
    <link rel="stylesheet" id="woocommerce-general-css" href="/wp-content/plugins/woocommerce/assets/css/woocommerce.css" type="text/css" media="all" />
    <script type="text/javascript">
        /* <![CDATA[ */
        var wc_add_to_cart_params = {"ajax_url":"/wp-admin/admin-ajax.php","wc_ajax_url":"/?wc-ajax=%%endpoint%%"};
        var woocommerce_params = {"currency_symbol":"£"};
        /* ]]> */
    </script>
</head>
<body class="product-template-default single single-product postid-100 woocommerce woocommerce-page">
    <div class="product type-product status-publish first instock has-post-thumbnail">
        <h1 class="product_title entry-title">Organic Cotton T-Shirt</h1>
        <p class="price">
            <span class="woocommerce-Price-amount amount">
                <bdi><span class="woocommerce-Price-currencySymbol">&pound;</span>28.00</bdi>
            </span>
        </p>
        <form class="cart" method="post" enctype="multipart/form-data">
            <button type="submit" name="add-to-cart" value="100" class="single_add_to_cart_button button alt">Add to basket</button>
        </form>
    </div>
</body>
</html>
"""

HEADLESS_WOO_GRAPHQL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Decoupled WP - Minimalist Desk</title>
    <!-- Headless WooCommerce with WPGraphQL and WooGraphQL backend -->
    <meta name="generator" content="WPGraphQL / WooGraphQL Headless">
    <script>
        window.FRONTEND_CONFIG = {
            graphqlEndpoint: "https://backend.brand.com/graphql",
            plugin: "woographql"
        };
    </script>
</head>
<body>
    <div id="app">
        <h1>Minimalist Desk</h1>
        <div class="price text-xl">€349.00</div>
    </div>
</body>
</html>
"""

HEADLESS_WOO_COCART_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>CoCart Headless Commerce - Running Shoes</title>
    <script>
        const COCART_API = "https://wp.brand.com/wp-json/cocart/v2/";
    </script>
</head>
<body>
    <h1>Running Shoes</h1>
    <span class="product-price">120.00 EUR</span>
</body>
</html>
"""

GENERIC_SCHEMA_ORG_GRAPH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Custom Artisan Shop - Handcrafted Pottery</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "name": "Artisan Craft Co.",
                "url": "https://custom-artisan.com"
            },
            {
                "@type": "Product",
                "name": "Handcrafted Ceramic Mug",
                "image": "https://custom-artisan.com/images/mug.jpg",
                "description": "Wheel-thrown ceramic stoneware mug.",
                "sku": "MUG-STONE-01",
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "USD",
                    "price": "34.50",
                    "availability": "https://schema.org/InStock"
                }
            }
        ]
    }
    </script>
</head>
<body>
    <div class="product-wrapper">
        <h1>Handcrafted Ceramic Mug</h1>
        <div class="custom-pricing-tag">Special Price: $34.50</div>
    </div>
</body>
</html>
"""

GENERIC_SCHEMA_PRICE_SPEC_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Nordic Furniture - Ergonomic Office Chair</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Ergonomic Office Chair",
        "offers": {
            "@type": "Offer",
            "priceSpecification": {
                "@type": "UnitPriceSpecification",
                "price": "499.00",
                "priceCurrency": "EUR"
            }
        }
    }
    </script>
</head>
<body>
    <h1>Ergonomic Office Chair</h1>
    <div>499,00 €</div>
</body>
</html>
"""

GENERIC_OPENGRAPH_MICRODATA_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Custom Boutique - Cashmere Sweater</title>
    <meta property="og:title" content="Cashmere Sweater">
    <meta property="og:image" content="https://boutique.example.com/sweater.jpg">
    <meta property="product:price:amount" content="189.99">
    <meta property="product:price:currency" content="CAD">
    <meta name="twitter:card" content="product">
</head>
<body>
    <div class="item-detail" itemscope itemtype="https://schema.org/Product">
        <h1 itemprop="name">Cashmere Sweater</h1>
        <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <meta itemprop="priceCurrency" content="CAD">
            <span itemprop="price" content="189.99">C$189.99</span>
        </div>
    </div>
</body>
</html>
"""

GENERIC_NEXTJS_HYDRATION_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Tech Gadgets Store - Wireless ANC Headphones</title>
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "initialState": {
                    "product": {
                        "id": "prod_anc_99",
                        "title": "Wireless ANC Headphones",
                        "regularPrice": 179.99,
                        "currency": "USD"
                    }
                }
            }
        }
    }
    </script>
</head>
<body>
    <div id="__next">
        <h1>Wireless ANC Headphones</h1>
        <div class="price-value">$179.99</div>
    </div>
</body>
</html>
"""

GENERIC_EUROPEAN_FORMAT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Euro Electrics - Soundbar Pro</title>
    <meta property="og:price:amount" content="1.299,00">
    <meta property="og:price:currency" content="EUR">
</head>
<body>
    <div class="product-view">
        <h1 class="title">Soundbar Pro</h1>
        <div class="current-price">1.299,00 €</div>
    </div>
</body>
</html>
"""

GENERIC_HEURISTIC_SELECTORS_ONLY_HTML = """
<!DOCTYPE html>
<html>
<head><title>Simple Goods - Heavy Wool Blanket</title></head>
<body>
    <div class="product-card">
        <h1 class="product-title">Heavy Wool Blanket</h1>
        <div class="product-price">
            <span class="sale-price">₦45,000</span>
        </div>
    </div>
</body>
</html>
"""

HEADLESS_GATSBY_SHOPIFY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Modern Static Store - Gatsby Shopify</title>
    <meta name="generator" content="Gatsby 5.12.0">
    <script>
        window.___GATSBY = {
            plugins: ["gatsby-source-shopify", "gatsby-plugin-image"],
            shopifyDomain: "modern-static.myshopify.com"
        };
    </script>
</head>
<body>
    <div id="___gatsby">
        <h1>Modern Static Store</h1>
        <div class="product-price">$89.00</div>
    </div>
</body>
</html>
"""

HEADLESS_NEXTJS_WOOCOMMERCE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Next.js Woo Store - Classic Watch</title>
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "product": {
                    "id": "prod_1001",
                    "title": "Classic Chrono Watch",
                    "backend": "woographql",
                    "price": "299.00",
                    "currency": "EUR"
                }
            }
        }
    }
    </script>
</head>
<body>
    <div id="__next">
        <h1>Classic Chrono Watch</h1>
        <span class="price">299.00 EUR</span>
    </div>
</body>
</html>
"""

GENERIC_CATALOG_CARDS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Boutique Catalog</title></head>
<body>
    <div class="product-grid">
        <div class="product-card">
            <h2 class="product-title">Handmade Leather Wallet</h2>
            <img src="/images/wallet.jpg" alt="Wallet" class="product-image" />
            <div class="price">$49.99</div>
            <a href="/products/leather-wallet">View Product</a>
        </div>
        <div class="product-card">
            <h2 class="product-title">Canvas Tote Bag</h2>
            <img src="/images/tote.jpg" alt="Tote" class="product-image" />
            <div class="price">$29.50</div>
            <a href="/products/canvas-tote">View Product</a>
        </div>
    </div>
</body>
</html>
"""

GENERIC_SCHEMA_AGGREGATE_OFFER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Pro Gaming Headset</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": "Pro Gaming Headset RGB",
        "offers": {
            "@type": "AggregateOffer",
            "lowPrice": "79.99",
            "highPrice": "119.99",
            "priceCurrency": "GBP"
        }
    }
    </script>
</head>
<body>
    <h1>Pro Gaming Headset RGB</h1>
    <div>From £79.99</div>
</body>
</html>
"""

GENERIC_PLAIN_NON_COMMERCE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Personal Blog</title></head>
<body>
    <h1>Welcome to My Blog</h1>
    <p>Thoughts on technology and design.</p>
</body>
</html>
"""


# ============================================================================
# Platform Detection Tests
# ============================================================================

@pytest.mark.asyncio
async def test_detect_classic_shopify_store():
    """Verify classic Shopify store detection with high confidence and Shopify label."""
    url = "https://classic-apparel.com/products/denim-jacket"
    result = await detect_store(url, html=CLASSIC_SHOPIFY_HTML)

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify"
    assert result.is_headless is False
    assert result.confidence >= 0.85
    assert "cdn.shopify.com" in result.matched_signals or "shopify_js_globals" in result.matched_signals
    assert isinstance(result.handler, ShopifyHandler)


@pytest.mark.asyncio
async def test_detect_myshopify_subdomain():
    """Verify myshopify.com subdomain is recognized as Shopify even without HTML."""
    url = "https://acme-brand.myshopify.com/collections/frontpage/products/t-shirt"
    result = await detect_store(url, html="")

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify"
    assert result.is_headless is False
    assert result.confidence >= 0.95
    assert "myshopify_domain" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_shopify_custom_subdomain_and_url_pattern():
    """Verify custom subdomains and alternative Shopify URL paths are tracked."""
    url = "https://shop.brand.com/collections/jackets/products/leather-coat"
    result = await detect_store(url, html=CLASSIC_SHOPIFY_HTML)

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify"
    assert "custom_subdomain" in result.matched_signals
    assert "shopify_url_pattern" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_headless_shopify_hydrogen():
    """Verify modern Shopify Hydrogen / Remix Oxygen headless frontends are accurately recognized."""
    url = "https://cool-kicks.com/products/cool-kicks"
    result = await detect_store(url, html=HEADLESS_HYDROGEN_SHOPIFY_HTML)

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify (Headless)"
    assert result.is_headless is True
    assert result.confidence >= 0.80
    assert any(s in result.matched_signals for s in ("hydrogen", "hydrogen_state", "shopify_dev_host"))


@pytest.mark.asyncio
async def test_detect_headless_shopify_nextjs_commerce():
    """Verify Next.js Commerce with Shopify backend is classified as headless Shopify."""
    url = "https://modern-goods.com/products/premium-leather-bag"
    result = await detect_store(url, html=HEADLESS_NEXTJS_SHOPIFY_HTML)

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify (Headless)"
    assert result.is_headless is True
    assert result.confidence >= 0.80
    assert "nextjs_shopify" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_headless_shopify_buy_sdk():
    """Verify storefronts powered by Shopify Buy SDK are recognized as headless Shopify."""
    url = "https://boutique.example.com/item/silk-scarf"
    result = await detect_store(url, html=HEADLESS_SHOPIFY_BUY_SDK_HTML)

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify (Headless)"
    assert result.is_headless is True
    assert result.confidence >= 0.80
    assert "shopify_buy_sdk" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_classic_woocommerce_store():
    """Verify classic WooCommerce store detection with high confidence and WooCommerce label."""
    url = "https://example-woo.com/product/organic-cotton-t-shirt/"
    result = await detect_store(url, html=CLASSIC_WOOCOMMERCE_HTML)

    assert result.platform == "woocommerce"
    assert result.platform_label == "WooCommerce"
    assert result.is_headless is False
    assert result.confidence >= 0.85
    assert any(s in result.matched_signals for s in ("wp_woocommerce_assets", "woocommerce_generator_meta", "woocommerce_css_classes"))
    assert isinstance(result.handler, WooCommerceHandler)


@pytest.mark.asyncio
async def test_detect_woocommerce_custom_subdomain_and_url_patterns():
    """Verify custom subdomains and alternative WooCommerce URL patterns (shop, product-category)."""
    url = "https://store.brand.com/product-category/clothing/product/hoodie/?post_type=product"
    result = await detect_store(url, html=CLASSIC_WOOCOMMERCE_HTML)

    assert result.platform == "woocommerce"
    assert "custom_subdomain" in result.matched_signals
    assert "woocommerce_url_pattern" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_headless_woocommerce_woographql():
    """Verify decoupled WooCommerce using WPGraphQL / WooGraphQL backend."""
    url = "https://headless-desk.com/desk-item"
    result = await detect_store(url, html=HEADLESS_WOO_GRAPHQL_HTML)

    assert result.platform == "woocommerce"
    assert result.platform_label == "WooCommerce (Headless)"
    assert result.is_headless is True
    assert result.confidence >= 0.80
    assert "woographql" in result.matched_signals or "wpgraphql" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_headless_woocommerce_cocart():
    """Verify headless WooCommerce using CoCart API is recognized."""
    url = "https://shoes.example.com/p/running-shoes"
    result = await detect_store(url, html=HEADLESS_WOO_COCART_HTML)

    assert result.platform == "woocommerce"
    assert result.platform_label == "WooCommerce (Headless)"
    assert result.is_headless is True
    assert result.confidence >= 0.80
    assert "cocart" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_custom_platform_schema_org_fallback():
    """Verify unrecognized custom store falls back to Generalized Heuristics with transparent confidence."""
    url = "https://custom-artisan.com/handcrafted-ceramic-mug"
    result = await detect_store(url, html=GENERIC_SCHEMA_ORG_GRAPH_HTML)

    assert result.platform == "custom"
    assert result.platform_label == "Custom (Generalized Heuristics)"
    assert result.is_headless is False
    assert result.confidence >= 0.65
    assert "schema_org_json_ld" in result.matched_signals
    assert isinstance(result.handler, GenericHandler)


@pytest.mark.asyncio
async def test_detect_custom_platform_opengraph_microdata_fallback():
    """Verify custom store with OpenGraph and Microdata metadata falls back gracefully."""
    url = "https://boutique.example.com/p/cashmere-sweater"
    result = await detect_store(url, html=GENERIC_OPENGRAPH_MICRODATA_HTML)

    assert result.platform == "custom"
    assert result.platform_label == "Custom (Generalized Heuristics)"
    assert "opengraph_price_metadata" in result.matched_signals
    assert "microdata_price" in result.matched_signals
    assert result.confidence >= 0.65


@pytest.mark.asyncio
async def test_detect_custom_platform_hydration_fallback():
    """Verify custom store with Next.js hydration data is classified as custom with confidence."""
    url = "https://tech-gadgets.com/product/wireless-anc-headphones"
    result = await detect_store(url, html=GENERIC_NEXTJS_HYDRATION_HTML)

    assert result.platform == "custom"
    assert result.platform_label == "Custom (Generalized Heuristics)"
    assert "hydration_data" in result.matched_signals
    assert result.confidence >= 0.60


@pytest.mark.asyncio
async def test_detect_platform_handler_attributes_populated():
    """Verify detect_platform populates platform_name, platform_label, confidence, is_headless on handler."""
    url = "https://shop.brand.com/products/denim-jacket"
    handler = await detect_platform(url, html=CLASSIC_SHOPIFY_HTML)

    assert handler.platform_name == "shopify"
    assert handler.platform_label == "Shopify"
    assert handler.confidence >= 0.85
    assert handler.is_headless is False
    assert len(handler.matched_signals) > 0


@pytest.mark.asyncio
async def test_get_handler_for_platform_headless_variants():
    """Verify get_handler_for_platform respects headless and custom platform labels."""
    shopify_headless = await get_handler_for_platform("Shopify (Headless)")
    assert isinstance(shopify_headless, ShopifyHandler)
    assert shopify_headless.is_headless is True
    assert shopify_headless.platform_label == "Shopify (Headless)"

    woo_headless = await get_handler_for_platform("woocommerce (headless)")
    assert isinstance(woo_headless, WooCommerceHandler)
    assert woo_headless.is_headless is True
    assert woo_headless.platform_label == "WooCommerce (Headless)"

    custom = await get_handler_for_platform("Custom (Generalized Heuristics)")
    assert isinstance(custom, GenericHandler)


# ============================================================================
# Robust Generalized Web Heuristics Fallback Price Extraction Tests
# ============================================================================

def test_extract_price_from_schema_org_graph():
    """Test price and currency extraction from Schema.org @graph structure."""
    price, currency = extract_price_from_html(GENERIC_SCHEMA_ORG_GRAPH_HTML, retailer="unknown")
    assert price == Decimal("34.50")
    assert currency == "USD"


def test_extract_price_from_schema_org_price_specification():
    """Test price extraction when price is wrapped in UnitPriceSpecification."""
    price, currency = extract_price_from_html(GENERIC_SCHEMA_PRICE_SPEC_HTML, retailer="unknown")
    assert price == Decimal("499.00")
    assert currency == "EUR"


def test_extract_price_from_opengraph_and_microdata():
    """Test price and Canadian Dollars currency extraction from OpenGraph/Microdata."""
    price, currency = extract_price_from_html(GENERIC_OPENGRAPH_MICRODATA_HTML, retailer="unknown")
    assert price == Decimal("189.99")
    assert currency == "CAD"


def test_extract_price_from_nextjs_hydration():
    """Test price extraction from embedded Next.js __NEXT_DATA__."""
    price, currency = extract_price_from_html(GENERIC_NEXTJS_HYDRATION_HTML, retailer="unknown")
    assert price == Decimal("179.99")
    assert currency == "USD"


def test_extract_price_from_european_formatting():
    """Test European decimal and thousand separator handling (1.299,00 -> 1299.00 EUR)."""
    price, currency = extract_price_from_html(GENERIC_EUROPEAN_FORMAT_HTML, retailer="unknown")
    assert price == Decimal("1299.00")
    assert currency == "EUR"


def test_extract_price_from_heuristic_selectors_with_ngn():
    """Test heuristic selector extraction with Nigerian Naira symbol."""
    price, currency = extract_price_from_html(GENERIC_HEURISTIC_SELECTORS_ONLY_HTML, retailer="unknown")
    assert price == Decimal("45000")
    assert currency == "NGN"


def test_parse_price_international_currencies():
    """Verify price and currency parsing across multiple international currencies."""
    assert parse_price("$99.99") == (Decimal("99.99"), "USD")
    assert parse_price("£45.50") == (Decimal("45.50"), "GBP")
    assert parse_price("€120.00") == (Decimal("120.00"), "EUR")
    assert parse_price("C$75.00") == (Decimal("75.00"), "CAD")
    assert parse_price("A$89.00") == (Decimal("89.00"), "AUD")
    assert parse_price("¥9800") == (Decimal("9800"), "JPY")
    assert parse_price("₹2,499") == (Decimal("2499"), "INR")
    assert parse_price("₦35,000") == (Decimal("35000"), "NGN")
    assert parse_price("CHF 150.00") == (Decimal("150.00"), "CHF")


def test_detect_platform_from_html_headless_support():
    """Verify detect_platform_from_html detects modern headless setups."""
    assert detect_platform_from_html(HEADLESS_HYDROGEN_SHOPIFY_HTML) == "shopify"
    assert detect_platform_from_html(HEADLESS_NEXTJS_SHOPIFY_HTML) == "shopify"
    assert detect_platform_from_html(HEADLESS_SHOPIFY_BUY_SDK_HTML) == "shopify"
    assert detect_platform_from_html(HEADLESS_WOO_GRAPHQL_HTML) == "woocommerce"
    assert detect_platform_from_html(HEADLESS_WOO_COCART_HTML) == "woocommerce"
    assert detect_platform_from_html(GENERIC_SCHEMA_ORG_GRAPH_HTML) is None


# ============================================================================
# API Discovery Route Transparency Tests
# ============================================================================

def test_discovery_route_returns_transparent_confidence_and_labels(client, monkeypatch):
    """
    REV-COMPETITOR-DETECTION: Verify /api/stores/discover returns transparent
    detection confidence, platform labels, is_headless, and matched signals.
    """
    from app.services.store_discovery import DiscoveryResult
    from app.services.stores.base import DiscoveredProduct

    mock_discovery = DiscoveryResult(
        platform="shopify",
        store_url="https://cool-kicks.com",
        total_found=1,
        products=[
            DiscoveredProduct(
                name="Cool Kicks",
                price=Decimal("145.00"),
                currency="USD",
                image_url=None,
                product_url="https://cool-kicks.com/products/cool-kicks",
                platform="shopify",
            )
        ],
        confidence=0.92,
        platform_label="Shopify (Headless)",
        is_headless=True,
        matched_signals=["hydrogen", "storefront_api_unstable"],
    )

    with patch("app.api.routes.discovery.discover_products", new_callable=AsyncMock) as mock_dp:
        mock_dp.return_value = mock_discovery

        # Use an authenticated test user token
        from app.core.security import create_access_token
        token = create_access_token({"sub": "test-user-id", "email": "test@example.com"})

        response = client.post(
            "/api/stores/discover",
            json={"url": "https://cool-kicks.com", "limit": 25},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["platform"] == "shopify"
        assert data["platform_label"] == "Shopify (Headless)"
        assert data["confidence"] == 0.92
        assert data["is_headless"] is True
        assert "hydrogen" in data["matched_signals"]
        assert len(data["products"]) == 1
        assert data["products"][0]["name"] == "Cool Kicks"


@pytest.mark.asyncio
async def test_detect_headless_gatsby_shopify():
    """Verify Gatsby-powered Shopify storefronts are classified as headless Shopify."""
    url = "https://modern-static.com"
    result = await detect_store(url, html=HEADLESS_GATSBY_SHOPIFY_HTML)

    assert result.platform == "shopify"
    assert result.platform_label == "Shopify (Headless)"
    assert result.is_headless is True
    assert "gatsby_shopify" in result.matched_signals


@pytest.mark.asyncio
async def test_detect_headless_nextjs_woocommerce():
    """Verify Next.js Woo storefronts with hydration data are classified as headless WooCommerce."""
    url = "https://nextjs-woo.com/product/classic-chrono-watch"
    result = await detect_store(url, html=HEADLESS_NEXTJS_WOOCOMMERCE_HTML)

    assert result.platform == "woocommerce"
    assert result.platform_label == "WooCommerce (Headless)"
    assert result.is_headless is True
    assert any(s in result.matched_signals for s in ("woographql", "nextjs_woocommerce"))


@pytest.mark.asyncio
async def test_detect_custom_platform_plain_html_confidence():
    """Verify non-commerce or minimal HTML yields base confidence 0.50 and custom label."""
    url = "https://myblog.example.com"
    result = await detect_store(url, html=GENERIC_PLAIN_NON_COMMERCE_HTML)

    assert result.platform == "custom"
    assert result.platform_label == "Custom (Generalized Heuristics)"
    assert result.confidence == 0.50
    assert "generic_fallback" in result.matched_signals


def test_generic_handler_catalog_cards_extraction():
    """Verify GenericHandler correctly parses multiple product cards from a catalog page."""
    handler = GenericHandler()
    products = handler._parse_products(GENERIC_CATALOG_CARDS_HTML, "https://example.com/catalog")

    assert len(products) == 2
    assert products[0].name == "Handmade Leather Wallet"
    assert products[0].price == Decimal("49.99")
    assert products[0].currency == "USD"
    assert "wallet.jpg" in products[0].image_url
    assert products[0].product_url == "https://example.com/products/leather-wallet"

    assert products[1].name == "Canvas Tote Bag"
    assert products[1].price == Decimal("29.50")
    assert products[1].currency == "USD"
    assert "tote.jpg" in products[1].image_url
    assert products[1].product_url == "https://example.com/products/canvas-tote"


def test_extract_price_from_schema_aggregate_offer():
    """Verify price and British Pounds currency extraction from Schema.org AggregateOffer."""
    price, currency = extract_price_from_html(GENERIC_SCHEMA_AGGREGATE_OFFER_HTML, retailer="unknown")
    assert price == Decimal("79.99")
    assert currency == "GBP"


@pytest.mark.asyncio
async def test_shopify_handler_fallback_to_generic_when_apis_fail():
    """Verify ShopifyHandler falls back to generalized web heuristics when API endpoints fail."""
    handler = ShopifyHandler()
    handler._fetch_via_products_json = AsyncMock(return_value=[])
    handler._fetch_via_storefront_api = AsyncMock(return_value=[])

    with patch("app.services.stores.generic.GenericHandler.fetch_products", new_callable=AsyncMock) as mock_gen:
        from app.services.stores.base import DiscoveredProduct
        mock_gen.return_value = [
            DiscoveredProduct(
                name="Fallback Scraped Hat",
                price=Decimal("24.00"),
                currency="USD",
                image_url=None,
                product_url="https://headless-store.com/products/hat",
                platform="custom",
            )
        ]

        products = await handler.fetch_products("https://headless-store.com/products/hat")
        assert len(products) == 1
        assert products[0].name == "Fallback Scraped Hat"
        assert products[0].platform == "shopify"


@pytest.mark.asyncio
async def test_woocommerce_handler_fallback_to_generic_when_apis_fail():
    """Verify WooCommerceHandler falls back to generalized web heuristics when API endpoints fail."""
    handler = WooCommerceHandler()
    handler._find_working_endpoint = AsyncMock(return_value=None)

    with patch("app.services.stores.generic.GenericHandler.fetch_products", new_callable=AsyncMock) as mock_gen:
        from app.services.stores.base import DiscoveredProduct
        mock_gen.return_value = [
            DiscoveredProduct(
                name="Fallback Scraped Boots",
                price=Decimal("110.00"),
                currency="EUR",
                image_url=None,
                product_url="https://headless-woo.com/product/boots",
                platform="custom",
            )
        ]

        products = await handler.fetch_products("https://headless-woo.com/product/boots")
        assert len(products) == 1
        assert products[0].name == "Fallback Scraped Boots"
        assert products[0].platform == "woocommerce"


@pytest.mark.asyncio
async def test_discover_products_custom_store_end_to_end():
    """Verify discover_products discovers custom store products using generalized web heuristics."""
    from app.services.store_discovery import discover_products

    url = "https://custom-artisan.com/handcrafted-ceramic-mug"
    with patch("httpx.AsyncClient.get") as mock_get:
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = GENERIC_SCHEMA_ORG_GRAPH_HTML
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response

        res = await discover_products(url)
        assert res.platform == "custom"
        assert res.platform_label == "Custom (Generalized Heuristics)"
        assert res.confidence >= 0.65
        assert res.total_found >= 1
        assert res.products[0].name == "Handcrafted Ceramic Mug"
        assert res.products[0].price == Decimal("34.50")

