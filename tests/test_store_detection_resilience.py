"""
Unit and integration tests for competitor store platform detection resilience and fallbacks:
1. Accurate recognition of modern headless setups (Shopify Hydrogen, Next.js, Headless WooCommerce, WPGraphQL).
2. Recognition of custom subdomains and alternative URL patterns.
3. Transparent detection confidence and platform labels.
4. Robust fallback mode extracting prices using generalized web heuristics (Schema.org JSON-LD with @graph, OpenGraph, microdata, data attributes).
5. Accurate multi-currency handling and European decimal formats.
"""

from decimal import Decimal
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.store_detector import (
    PlatformDetectionResult,
    classify_platform_from_html,
    classify_platform_from_url,
    detect_platform,
    detect_platform_details,
    get_handler_for_platform,
)
from app.services.scraper_service import (
    detect_platform_from_html,
    extract_price_from_html,
    parse_price,
)
from app.services.stores.shopify import ShopifyHandler
from app.services.stores.woocommerce import WooCommerceHandler
from app.services.stores.generic import GenericHandler
from app.services.store_discovery import discover_products


# ---------------------------------------------------------------------------
# Representative HTML Snippets from various eCommerce setups
# ---------------------------------------------------------------------------

SHOPIFY_CLASSIC_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>Classic Apparel Store - Wool Sweater</title>
    <meta name="generator" content="Shopify">
    <link rel="preconnect" href="https://cdn.shopify.com">
    <script src="https://cdn.shopify.com/s/files/1/0000/0001/t/1/assets/theme.js"></script>
    <script>
        window.Shopify = window.Shopify || {};
        Shopify.theme = { name: "Dawn", id: 12345 };
        Shopify.currency = { active: "USD", rate: "1.0" };
    </script>
</head>
<body>
    <div class="product-single">
        <h1 class="product-single__title">Wool Sweater</h1>
        <div class="price price--large">
            <span class="price-item price-item--sale">$89.00</span>
        </div>
    </div>
</body>
</html>
"""

SHOPIFY_HYDROGEN_HEADLESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>NextGen Shoes - Headless Store</title>
    <meta name="x-shopify-oxygen-deployment-id" content="dep-998877">
    <script type="module" src="https://hydrogen.shopify.com/client-runtime.js"></script>
    <script>
        window.__SHOPIFY_DEV_HOST__ = "headless.brand.com";
        window.ShopifyAnalytics = { lib: "trekkie" };
    </script>
</head>
<body data-hydrogen-app="true">
    <div id="root">
        <main>
            <h1 class="pdp-title">Aero Pro Running Shoes</h1>
            <div class="pdp-price" data-price="149.99">
                <span class="current-price">$149.99</span>
            </div>
        </main>
    </div>
</body>
</html>
"""

SHOPIFY_NEXTJS_HEADLESS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Modern Goods - Wireless Headphones</title>
    <link rel="preconnect" href="https://cdn.shopify.com">
    <link rel="dns-prefetch" href="https://cdn.shopify.com">
</head>
<body>
    <div id="__next">
        <div class="product-detail">
            <h1>Active Noise Cancelling Headphones</h1>
            <span class="sale-price">$199.50</span>
        </div>
    </div>
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "product": {
                    "handle": "active-noise-cancelling-headphones",
                    "shopifyId": "gid://shopify/Product/123456",
                    "storefrontAccessToken": "shpat_abc123xyz"
                }
            }
        }
    }
    </script>
</body>
</html>
"""

WOOCOMMERCE_CLASSIC_HTML = """
<!DOCTYPE html>
<html lang="en-US">
<head>
    <title>Artisan Coffee - Whole Bean Dark Roast</title>
    <meta name="generator" content="WooCommerce 8.5.2">
    <link rel="stylesheet" href="https://artisancoffee.com/wp-content/plugins/woocommerce/assets/css/woocommerce.css">
    <script>
        var woocommerce_params = { "ajax_url": "/wp-admin/admin-ajax.php" };
        var wc_add_to_cart_params = { "wc_ajax_url": "/?wc-ajax=%%endpoint%%" };
    </script>
</head>
<body class="product-template-default single single-product postid-100 woocommerce woocommerce-page">
    <div class="product type-product status-publish has-post-thumbnail">
        <h1 class="product_title entry-title">Whole Bean Dark Roast</h1>
        <p class="price">
            <span class="woocommerce-Price-amount amount">
                <bdi><span class="woocommerce-Price-currencySymbol">&#36;</span>18.50</bdi>
            </span>
        </p>
    </div>
</body>
</html>
"""

WOOCOMMERCE_HEADLESS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Urban Gear - Canvas Backpack</title>
    <link rel="https://api.w.org/" href="https://api.urbangear.com/wp-json/">
    <script>
        window.__WP_GRAPHQL_ENDPOINT__ = "https://api.urbangear.com/graphql";
        window.cocart_params = { "cart_url": "/wp-json/cocart/v2/cart" };
    </script>
</head>
<body>
    <div id="app">
        <div class="wc-block-components-product">
            <h2>Canvas Travel Backpack</h2>
            <div class="wc-block-components-product-price">
                <span class="price-value">$65.00</span>
            </div>
        </div>
    </div>
</body>
</html>
"""

CUSTOM_STORE_SCHEMA_JSONLD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Boutique Furniture - Scandinavian Lounge Chair</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Scandinavian Lounge Chair",
        "image": "https://customboutique.com/images/chair.jpg",
        "description": "Minimalist handcrafted oak armchair.",
        "sku": "CHAIR-OAK-01",
        "offers": {
            "@type": "Offer",
            "price": "349.00",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock"
        }
    }
    </script>
</head>
<body>
    <div class="item-container">
        <h1>Scandinavian Lounge Chair</h1>
        <div class="buy-box">
            <span class="price-text">$349.00</span>
            <button class="cta-button">Buy Now</button>
        </div>
    </div>
</body>
</html>
"""

CUSTOM_STORE_GRAPH_JSONLD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Gourmet Olive Oil - Extra Virgin Cold Pressed</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": "https://oliveoil.example/#organization",
                "name": "Estate Olives"
            },
            {
                "@type": "Product",
                "@id": "https://oliveoil.example/product/extra-virgin/#product",
                "name": "Extra Virgin Cold Pressed 500ml",
                "offers": {
                    "@type": "AggregateOffer",
                    "lowPrice": "24.50",
                    "highPrice": "29.50",
                    "priceCurrency": "EUR"
                }
            }
        ]
    }
    </script>
</head>
<body>
    <div class="catalog-entry">
        <h1>Extra Virgin Cold Pressed 500ml</h1>
        <div class="cost-display">24,50 &euro;</div>
    </div>
</body>
</html>
"""

CUSTOM_STORE_OPENGRAPH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>SoundMax Portable Speaker</title>
    <meta property="og:title" content="SoundMax Portable Speaker">
    <meta property="og:price:amount" content="79.99">
    <meta property="og:price:currency" content="GBP">
    <meta property="product:price:amount" content="79.99">
    <meta property="product:price:currency" content="GBP">
</head>
<body>
    <div class="pdp-wrapper">
        <h1 class="name">SoundMax Portable Speaker</h1>
        <div class="price-section">
            <span class="amount">&pound;79.99</span>
        </div>
    </div>
</body>
</html>
"""

CUSTOM_STORE_DATA_ATTRS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Eco Friendly Water Bottle</title></head>
<body>
    <div class="card" data-product="bottle-500">
        <h2 class="title">Stainless Steel Insulated Bottle</h2>
        <div class="pricing-info" data-product-price="27.00" data-price-currency="CAD">
            <span class="custom-pricing-tag">C$27.00</span>
        </div>
    </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Platform Detection from HTML Tests
# ---------------------------------------------------------------------------

def test_classify_classic_shopify_store():
    """Verify classic Shopify store HTML is recognized with high confidence."""
    platform, label, confidence, sigs = classify_platform_from_html(SHOPIFY_CLASSIC_HTML)
    assert platform == "shopify"
    assert label == "Shopify"
    assert confidence >= 0.90
    assert any("shopify" in s for s in sigs)


def test_classify_shopify_hydrogen_headless():
    """Verify Shopify Hydrogen headless frontend is identified with transparent label and confidence."""
    platform, label, confidence, sigs = classify_platform_from_html(SHOPIFY_HYDROGEN_HEADLESS_HTML)
    assert platform == "shopify"
    assert label == "Shopify (Headless)"
    assert confidence >= 0.85
    assert "shopify:hydrogen_headless" in sigs


def test_classify_shopify_nextjs_headless():
    """Verify Next.js Shopify headless storefront is recognized."""
    platform, label, confidence, sigs = classify_platform_from_html(SHOPIFY_NEXTJS_HEADLESS_HTML)
    assert platform == "shopify"
    assert label == "Shopify (Headless)"
    assert confidence >= 0.85
    assert any("shopify" in s for s in sigs)


def test_classify_classic_woocommerce_store():
    """Verify classic WooCommerce store HTML is recognized with high confidence."""
    platform, label, confidence, sigs = classify_platform_from_html(WOOCOMMERCE_CLASSIC_HTML)
    assert platform == "woocommerce"
    assert label == "WooCommerce"
    assert confidence >= 0.90
    assert "woocommerce:plugin_assets" in sigs or "woocommerce:meta_generator" in sigs


def test_classify_headless_woocommerce_store():
    """Verify headless WooCommerce store (WPGraphQL / CoCart) is accurately recognized."""
    platform, label, confidence, sigs = classify_platform_from_html(WOOCOMMERCE_HEADLESS_HTML)
    assert platform == "woocommerce"
    assert label == "WooCommerce (Headless)"
    assert confidence >= 0.85
    assert any("woocommerce" in s for s in sigs)


def test_classify_custom_store_with_schema():
    """Verify custom store with Schema.org JSON-LD falls back to custom with heuristics confidence."""
    platform, label, confidence, sigs = classify_platform_from_html(CUSTOM_STORE_SCHEMA_JSONLD_HTML)
    assert platform == "custom"
    assert label == "Custom / Web Heuristics"
    assert confidence >= 0.60
    assert "heuristics:schema_product_or_offer" in sigs


def test_classify_custom_store_with_opengraph():
    """Verify custom store with OpenGraph price tags falls back to custom with heuristics confidence."""
    platform, label, confidence, sigs = classify_platform_from_html(CUSTOM_STORE_OPENGRAPH_HTML)
    assert platform == "custom"
    assert label == "Custom / Web Heuristics"
    assert confidence >= 0.60


def test_classify_minimal_custom_page():
    """Verify arbitrary custom HTML without commerce signatures falls back gracefully."""
    minimal_html = "<html><body><h1>Welcome to our portfolio</h1></body></html>"
    platform, label, confidence, sigs = classify_platform_from_html(minimal_html)
    assert platform == "custom"
    assert label == "Custom / Web Heuristics"
    assert confidence == 0.50


# ---------------------------------------------------------------------------
# URL Pattern & Subdomain Classification Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected_platform,expected_label,min_conf", [
    ("https://brand.myshopify.com", "shopify", "Shopify", 0.95),
    ("https://cool-kicks.myshopify.com/products/sneaker-v1", "shopify", "Shopify", 0.95),
    ("https://shop.outdoorbrand.com/products/camping-tent", "shopify", "Shopify", 0.80),
    ("https://store.fashionbrand.co.uk/collections/autumn/products/coat", "shopify", "Shopify", 0.80),
    ("https://store.teahouse.com/product/matcha-green-tea", "woocommerce", "WooCommerce", 0.80),
    ("https://shop.bakery.com/product-category/pastries/croissant", "woocommerce", "WooCommerce", 0.80),
    ("https://artisan-roast.com/?post_type=product&p=888", "woocommerce", "WooCommerce", 0.70),
    ("https://generic-site.example.com/about-us", None, None, 0.0),
])
def test_classify_platform_from_url(url, expected_platform, expected_label, min_conf):
    """Test URL pattern heuristics across custom subdomains and alternative paths."""
    res = classify_platform_from_url(url)
    if expected_platform is None:
        assert res is None
    else:
        assert res is not None
        platform, label, confidence, sigs = res
        assert platform == expected_platform
        assert label == expected_label
        assert confidence >= min_conf


# ---------------------------------------------------------------------------
# Fallback Web Heuristics Price Extraction Tests
# ---------------------------------------------------------------------------

def test_extract_price_from_schema_jsonld_single_product():
    """Test extracting price from Schema.org JSON-LD Product & Offer."""
    price, currency = extract_price_from_html(CUSTOM_STORE_SCHEMA_JSONLD_HTML, retailer="unknown")
    assert price == Decimal("349.00")
    assert currency == "USD"


def test_extract_price_from_schema_jsonld_graph():
    """Test extracting price from Schema.org JSON-LD @graph with AggregateOffer (lowPrice)."""
    price, currency = extract_price_from_html(CUSTOM_STORE_GRAPH_JSONLD_HTML, retailer="unknown")
    assert price == Decimal("24.50")
    assert currency == "EUR"


def test_extract_price_from_opengraph_meta_tags():
    """Test extracting price from OpenGraph and Twitter meta tags."""
    price, currency = extract_price_from_html(CUSTOM_STORE_OPENGRAPH_HTML, retailer="unknown")
    assert price == Decimal("79.99")
    assert currency == "GBP"


def test_extract_price_from_data_attributes():
    """Test extracting price from modern data-* attributes when CSS classes are non-standard."""
    price, currency = extract_price_from_html(CUSTOM_STORE_DATA_ATTRS_HTML, retailer="unknown")
    assert price == Decimal("27.00")
    assert currency == "CAD"


def test_extract_price_from_contemporary_css_classes():
    """Test price extraction using modern class heuristics (e.g. .current-price, .pdp-price)."""
    html = """
    <div class="pdp-container">
        <h1 class="pdp-heading">Smart Fitness Tracker</h1>
        <div class="pricing-row">
            <span class="old-price" style="text-decoration: line-through;">$129.99</span>
            <span class="current-price">$89.95</span>
        </div>
    </div>
    """
    price, currency = extract_price_from_html(html, retailer="unknown")
    assert price == Decimal("89.95")
    assert currency == "USD"


def test_extract_price_from_itemprop_microdata():
    """Test microdata itemprop='price' attribute extraction."""
    html = """
    <div itemscope itemtype="http://schema.org/Product">
        <h1 itemprop="name">Leather Card Holder</h1>
        <span itemprop="price" content="35.00">$35</span>
        <meta itemprop="priceCurrency" content="USD" />
    </div>
    """
    price, currency = extract_price_from_html(html, retailer="unknown")
    assert price == Decimal("35.00")
    assert currency == "USD"


@pytest.mark.parametrize("price_text,expected_price,expected_currency", [
    ("$49.99", Decimal("49.99"), "USD"),
    ("€ 1.250,50", Decimal("1250.50"), "EUR"),
    ("£19.95", Decimal("19.95"), "GBP"),
    ("C$ 45.00", Decimal("45.00"), "CAD"),
    ("A$ 59.95", Decimal("59.95"), "AUD"),
    ("¥3,500", Decimal("3500"), "JPY"),
    ("₦25,000.00", Decimal("25000.00"), "NGN"),
    ("₹1,499.00", Decimal("1499.00"), "INR"),
])
def test_parse_price_international_currencies(price_text, expected_price, expected_currency):
    """Test price parsing and currency detection across international currency symbols and formats."""
    price, currency = parse_price(price_text)
    assert price == expected_price
    assert currency == expected_currency


# ---------------------------------------------------------------------------
# Scraper Service detect_platform_from_html Alignment Tests
# ---------------------------------------------------------------------------

def test_scraper_detect_platform_from_html_shopify():
    assert detect_platform_from_html(SHOPIFY_CLASSIC_HTML) == "shopify"
    assert detect_platform_from_html(SHOPIFY_HYDROGEN_HEADLESS_HTML) == "shopify"
    assert detect_platform_from_html(SHOPIFY_NEXTJS_HEADLESS_HTML) == "shopify"


def test_scraper_detect_platform_from_html_woocommerce():
    assert detect_platform_from_html(WOOCOMMERCE_CLASSIC_HTML) == "woocommerce"
    assert detect_platform_from_html(WOOCOMMERCE_HEADLESS_HTML) == "woocommerce"


def test_scraper_detect_platform_from_html_custom():
    assert detect_platform_from_html(CUSTOM_STORE_SCHEMA_JSONLD_HTML) is None
    assert detect_platform_from_html(CUSTOM_STORE_OPENGRAPH_HTML) is None


# ---------------------------------------------------------------------------
# detect_platform and detect_platform_details End-to-End Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_platform_with_html_direct():
    """Verify detect_platform directly classifies HTML when supplied."""
    handler = await detect_platform("https://example.com/shoes", html=SHOPIFY_HYDROGEN_HEADLESS_HTML)
    assert isinstance(handler, ShopifyHandler)
    assert handler.platform_name == "shopify"
    assert handler.platform_label == "Shopify (Headless)"
    assert handler.confidence >= 0.85


@pytest.mark.asyncio
async def test_detect_platform_details_woocommerce():
    """Verify detect_platform_details returns structured PlatformDetectionResult."""
    result = await detect_platform_details("https://my-store.com/shop", html=WOOCOMMERCE_CLASSIC_HTML)
    assert isinstance(result, PlatformDetectionResult)
    assert result.platform == "woocommerce"
    assert result.platform_label == "WooCommerce"
    assert result.confidence >= 0.90
    assert isinstance(result.handler, WooCommerceHandler)


@pytest.mark.asyncio
async def test_detect_platform_details_custom_fallback():
    """Verify detect_platform_details falls back to custom web heuristics for generic sites."""
    result = await detect_platform_details("https://custom-shop.com/item", html=CUSTOM_STORE_SCHEMA_JSONLD_HTML)
    assert result.platform == "custom"
    assert result.platform_label == "Custom / Web Heuristics"
    assert result.confidence >= 0.60
    assert isinstance(result.handler, GenericHandler)


@pytest.mark.asyncio
async def test_get_handler_for_platform():
    """Verify get_handler_for_platform sets proper platform_label and confidence."""
    shopify = await get_handler_for_platform("shopify")
    assert isinstance(shopify, ShopifyHandler)
    assert shopify.platform_label == "Shopify"
    assert shopify.confidence == 0.95

    woo = await get_handler_for_platform("woocommerce")
    assert isinstance(woo, WooCommerceHandler)
    assert woo.platform_label == "WooCommerce"
    assert woo.confidence == 0.95

    custom = await get_handler_for_platform("custom")
    assert isinstance(custom, GenericHandler)
    assert custom.platform_label == "Custom / Web Heuristics"
    assert custom.confidence == 0.50


# ---------------------------------------------------------------------------
# GenericHandler Fallback Mode Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generic_handler_parse_schema_products_graph():
    """Verify GenericHandler correctly extracts products from @graph Schema.org JSON-LD."""
    handler = GenericHandler()
    products = handler._parse_products(CUSTOM_STORE_GRAPH_JSONLD_HTML, "https://oliveoil.example")
    assert len(products) >= 1
    prod = products[0]
    assert "Extra Virgin" in prod.name
    assert prod.price == Decimal("24.50")
    assert prod.currency == "EUR"
    assert prod.platform == "custom"
    assert prod.platform_label == "Custom / Web Heuristics"


@pytest.mark.asyncio
async def test_generic_handler_parse_schema_products_single():
    """Verify GenericHandler extracts single product with offer from Schema.org."""
    handler = GenericHandler()
    products = handler._parse_products(CUSTOM_STORE_SCHEMA_JSONLD_HTML, "https://customboutique.com")
    assert len(products) >= 1
    prod = products[0]
    assert prod.name == "Scandinavian Lounge Chair"
    assert prod.price == Decimal("349.00")
    assert prod.currency == "USD"
    assert prod.sku == "CHAIR-OAK-01"


# ---------------------------------------------------------------------------
# Store Discovery Route & Response Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_products_returns_transparent_confidence_and_label():
    """Verify discover_products returns DiscoveryResult with confidence and label."""
    with patch("app.services.store_discovery.detect_platform") as mock_detect:
        mock_handler = MagicMock(spec=ShopifyHandler)
        mock_handler.platform_name = "shopify"
        mock_handler.platform_label = "Shopify (Headless)"
        mock_handler.confidence = 0.92
        mock_handler.signatures = ["shopify:hydrogen_headless"]
        mock_handler.fetch_products = AsyncMock(return_value=[])
        mock_handler.close = AsyncMock()
        mock_detect.return_value = mock_handler

        result = await discover_products("https://headless.example.com")
        assert result.platform == "shopify"
        assert result.platform_label == "Shopify (Headless)"
        assert result.confidence == 0.92
        assert "shopify:hydrogen_headless" in result.signatures
