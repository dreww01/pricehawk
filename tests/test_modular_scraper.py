"""Unit tests for modular scraper engine, parser, detector, facade, and tasks."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.scraper.detector import (
    HANDLER_CLASSES,
    PlatformDetectionResult,
    classify_platform_from_html,
    classify_platform_from_url,
    detect_platform,
    detect_platform_details,
    detect_platform_from_html,
    get_handler_for_platform,
)
from app.services.scraper.engine import (
    BLOCKED_DOMAIN_SUFFIXES,
    USER_AGENTS,
    ScraperEngine,
    fetch_page,
    fetch_with_httpx,
    fetch_with_playwright,
    get_retailer,
    is_bot_challenge,
    normalize_url,
    validate_url,
)
from app.services.scraper.exceptions import (
    DatabasePersistenceError,
    FetchResult,
    ScrapeAccessDeniedError,
    ScrapeException,
    ScrapeFailureReason,
    ScrapeLayoutError,
    ScrapeNetworkError,
    ScrapeNotFoundError,
    ScrapeRateLimitError,
    ScrapeResult,
    ScrapeTimeoutError,
    classify_error_message,
    classify_scrape_exception,
)
from app.services.scraper.parser import (
    PRICE_SELECTORS,
    ScraperParser,
    _detect_meta_currency,
    _extract_price_from_json_ld,
    extract_price_from_html,
    parse_price,
)
from app.services.scraper_service import ScraperService
import app.services.scraper_service as scraper_service_mod
import app.services.store_detector as store_detector_mod
from app.tasks.scraper_tasks import (
    check_worker_health,
    cleanup_old_alerts,
    dispatch_webhook_alert,
    get_scrape_progress,
    scrape_all_products,
    scrape_product_manual,
    scrape_single_competitor,
    send_alert_digests,
    set_scrape_progress,
)


def test_scraper_file_line_limits():
    """Verify all decomposed scraper and task files remain strictly under specified line limits."""
    # engine.py < 250 lines
    engine_lines = len(Path("app/services/scraper/engine.py").read_text(encoding="utf-8").splitlines())
    assert engine_lines < 250, f"engine.py exceeds 250 lines: {engine_lines}"

    # parser.py < 200 lines
    parser_lines = len(Path("app/services/scraper/parser.py").read_text(encoding="utf-8").splitlines())
    assert parser_lines < 200, f"parser.py exceeds 200 lines: {parser_lines}"

    # detector.py < 300 lines
    detector_lines = len(Path("app/services/scraper/detector.py").read_text(encoding="utf-8").splitlines())
    assert detector_lines < 300, f"detector.py exceeds 300 lines: {detector_lines}"

    # scraper_service.py < 300 lines
    service_lines = len(Path("app/services/scraper_service.py").read_text(encoding="utf-8").splitlines())
    assert service_lines < 300, f"scraper_service.py exceeds 300 lines: {service_lines}"

    # scraper_tasks.py < 250 lines
    tasks_lines = len(Path("app/tasks/scraper_tasks.py").read_text(encoding="utf-8").splitlines())
    assert tasks_lines < 250, f"scraper_tasks.py exceeds 250 lines: {tasks_lines}"


def test_backward_compatibility_re_exports():
    """Verify that scraper_service and store_detector maintain 100% backward compatibility."""
    assert scraper_service_mod.ScrapeResult is ScrapeResult
    assert scraper_service_mod.FetchResult is FetchResult
    assert scraper_service_mod.ScrapeFailureReason is ScrapeFailureReason
    assert scraper_service_mod.ScrapeException is ScrapeException
    assert scraper_service_mod.DatabasePersistenceError is DatabasePersistenceError
    assert scraper_service_mod.parse_price is parse_price
    assert scraper_service_mod.extract_price_from_html is extract_price_from_html
    assert scraper_service_mod.detect_platform_from_html is detect_platform_from_html
    assert scraper_service_mod.is_bot_challenge is is_bot_challenge
    assert scraper_service_mod.normalize_url is normalize_url
    assert scraper_service_mod.validate_url is validate_url
    assert scraper_service_mod.get_retailer is get_retailer

    assert store_detector_mod.classify_platform_from_url is classify_platform_from_url
    assert store_detector_mod.classify_platform_from_html is classify_platform_from_html
    assert store_detector_mod.detect_platform is detect_platform
    assert store_detector_mod.detect_platform_details is detect_platform_details
    assert store_detector_mod.get_handler_for_platform is get_handler_for_platform
    assert store_detector_mod.PlatformDetectionResult is PlatformDetectionResult
    assert store_detector_mod.HANDLER_CLASSES is HANDLER_CLASSES


def test_engine_url_normalization_and_validation():
    """Test URL normalization and security validation heuristics in engine."""
    # Normalization
    norm, err = normalize_url("store.example.com/item")
    assert err is None
    assert norm == "https://store.example.com/item"

    norm, err = normalize_url("http://insecure.example.com")
    assert norm is None
    assert "HTTP is not secure" in err

    norm, err = normalize_url("")
    assert norm is None
    assert "cannot be empty" in err

    # Validation
    valid, err = validate_url("https://validstore.com/p/1")
    assert valid is True
    assert err is None

    valid, err = validate_url("http://validstore.com/p/1")
    assert valid is False
    assert "Only HTTPS" in err

    valid, err = validate_url("https://localhost:8000")
    assert valid is False
    assert "Private or internal" in err

    valid, err = validate_url("https://10.0.0.1/admin")
    assert valid is False
    assert "Private or internal" in err

    valid, err = validate_url("https://corp.internal/p")
    assert valid is False
    assert "Private or internal" in err


def test_engine_bot_challenge_detection():
    """Test bot challenge detection handles both titles and technical tokens."""
    assert is_bot_challenge("<html><head><title>Just a moment...</title></head></html>")
    assert is_bot_challenge("<html><head><title>Attention Required! | Cloudflare</title></head></html>")
    assert is_bot_challenge("<html><div class='cf-browser-verification'>Checking browser</div></html>")
    assert is_bot_challenge("<html><div class='px-captcha'>Human verification</div></html>")
    assert not is_bot_challenge("<html><head><title>My Store - Product</title></head><body><h1>Shoes</h1></body></html>")
    assert not is_bot_challenge("")


def test_parser_price_and_currency_parsing():
    """Test price parsing and currency normalization in parser."""
    assert parse_price("$19.99") == (Decimal("19.99"), "USD")
    assert parse_price("£12.50") == (Decimal("12.50"), "GBP")
    assert parse_price("€49,90") == (Decimal("49.90"), "EUR")
    assert parse_price("CAD 25.00") == (Decimal("25.00"), "CAD")
    assert parse_price("A$ 35.00") == (Decimal("35.00"), "AUD")
    assert parse_price("¥1,200") == (Decimal("1200"), "JPY")
    assert parse_price("₦15,000.00") == (Decimal("15000.00"), "NGN")
    assert parse_price("₹999.00") == (Decimal("999.00"), "INR")
    assert parse_price("") == (None, "USD")
    assert parse_price("Not a price") == (None, "USD")


def test_parser_extract_price_from_html():
    """Test HTML extraction across schema, meta tags, and class selectors."""
    html_schema = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Product", "name": "Shirt", "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD"}}
    </script>
    </head><body></body></html>
    """
    price, currency = extract_price_from_html(html_schema)
    assert price == Decimal("29.99")
    assert currency == "USD"

    html_meta = """
    <html><head>
    <meta property="product:price:amount" content="89.50">
    <meta property="product:price:currency" content="EUR">
    </head><body></body></html>
    """
    price, currency = extract_price_from_html(html_meta)
    assert price == Decimal("89.50")
    assert currency == "EUR"

    html_class = """
    <html><body>
    <span class="sale-price">$45.00</span>
    </body></html>
    """
    price, currency = extract_price_from_html(html_class)
    assert price == Decimal("45.00")
    assert currency == "USD"


def test_detector_classification():
    """Test platform detector classifies URLs and HTML correctly."""
    assert classify_platform_from_url("https://mystore.myshopify.com") == ("shopify", "Shopify", 0.95, ["url:myshopify_domain"])
    assert classify_platform_from_url("https://mystore.com/product/chair")[0] == "woocommerce"

    shopify_html = "<html><head><script src='https://cdn.shopify.com/theme.js'></script></head><body>window.Shopify = {}</body></html>"
    plat, label, conf, sigs = classify_platform_from_html(shopify_html)
    assert plat == "shopify"
    assert conf >= 0.70

    wc_html = "<html><body><div class='woocommerce-Price-amount'>$10.00</div><div class='woocommerce-page'></div></body></html>"
    plat, label, conf, sigs = classify_platform_from_html(wc_html)
    assert plat == "woocommerce"
    assert conf >= 0.70


@pytest.mark.asyncio
async def test_scraper_service_facade_methods():
    """Test ScraperService class facade exposes all core scraper operations."""
    assert callable(ScraperService.scrape_url)
    assert callable(ScraperService.scrape_and_check_alerts)
    assert callable(ScraperService.fetch_page)
    assert callable(ScraperService.fetch_with_httpx)
    assert callable(ScraperService.fetch_with_playwright)
    assert callable(ScraperService.parse_price)
    assert callable(ScraperService.extract_price_from_html)
    assert callable(ScraperService.detect_platform_from_html)

    # Calling facade static methods
    assert ScraperService.parse_price("$10.00") == (Decimal("10.00"), "USD")
    assert ScraperService.get_retailer("https://store.com") == "unknown"


def test_check_worker_health_task():
    """Test check_worker_health returns expected healthy response."""
    res = check_worker_health()
    assert res["status"] == "healthy"
    assert "timestamp" in res
