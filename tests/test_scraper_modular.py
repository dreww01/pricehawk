"""Tests for modular scraper package decomposition and line limits."""
from decimal import Decimal
import inspect
from pathlib import Path
import pytest

from app.services.scraper.models import (
    ScrapeFailureReason,
    ScrapeResult,
    FetchResult,
    ScrapeException,
    ScrapeTimeoutError,
    ScrapeNetworkError,
    ScrapeRateLimitError,
    ScrapeAccessDeniedError,
    ScrapeNotFoundError,
    ScrapeLayoutError,
    DatabasePersistenceError,
)
from app.services.scraper.engine import (
    ScraperEngine,
    fetch_page,
    fetch_with_httpx,
    fetch_with_playwright,
    normalize_url,
    validate_url,
    classify_scrape_exception,
    is_bot_challenge,
)
from app.services.scraper.parser import (
    ScraperParser,
    parse_price,
    extract_price_from_html,
    PRICE_SELECTORS,
)
from app.services.scraper.detector import (
    PlatformDetector,
    PlatformDetectionResult,
    classify_platform_from_url,
    classify_platform_from_html,
    detect_platform,
    detect_platform_details,
    detect_platform_from_html,
)
from app.services.scraper_service import (
    ScraperService,
    scrape_url,
    scrape_and_check_alerts,
)
import app.services.scraper_service as scraper_service_mod
import app.services.store_detector as store_detector_mod
import app.tasks.scraper_tasks as scraper_tasks_mod


def test_file_line_limits():
    """Verify all decomposed scraper and task files remain strictly under specified line limits."""
    root = Path(__file__).resolve().parent.parent

    engine_path = root / "app" / "services" / "scraper" / "engine.py"
    parser_path = root / "app" / "services" / "scraper" / "parser.py"
    tasks_path = root / "app" / "tasks" / "scraper_tasks.py"
    service_path = root / "app" / "services" / "scraper_service.py"
    detector_path = root / "app" / "services" / "scraper" / "detector.py"

    assert len(engine_path.read_text(encoding="utf-8").splitlines()) < 250, "engine.py must be < 250 lines"
    assert len(parser_path.read_text(encoding="utf-8").splitlines()) < 200, "parser.py must be < 200 lines"
    assert len(tasks_path.read_text(encoding="utf-8").splitlines()) < 250, "scraper_tasks.py must be < 250 lines"
    assert len(service_path.read_text(encoding="utf-8").splitlines()) < 300, "scraper_service.py must be < 300 lines"
    assert len(detector_path.read_text(encoding="utf-8").splitlines()) < 300, "detector.py must be < 300 lines"


def test_scraper_service_facade_backward_compatibility():
    """Ensure ScraperService facade exposes all expected methods and models."""
    # Check top-level module exports
    for name in [
        "scrape_url",
        "scrape_and_check_alerts",
        "fetch_page",
        "fetch_with_httpx",
        "fetch_with_playwright",
        "extract_price_from_html",
        "parse_price",
        "detect_platform_from_html",
        "normalize_url",
        "validate_url",
        "classify_scrape_exception",
        "ScrapeResult",
        "FetchResult",
        "ScrapeFailureReason",
        "ScrapeException",
        "ScrapeTimeoutError",
        "ScrapeNetworkError",
        "ScrapeRateLimitError",
        "ScrapeAccessDeniedError",
        "ScrapeNotFoundError",
        "ScrapeLayoutError",
        "DatabasePersistenceError",
    ]:
        assert hasattr(scraper_service_mod, name), f"scraper_service missing {name}"

    # Check ScraperService class methods
    assert hasattr(ScraperService, "scrape_url")
    assert hasattr(ScraperService, "scrape_and_check_alerts")
    assert hasattr(ScraperService, "fetch_page")
    assert hasattr(ScraperService, "fetch_with_httpx")
    assert hasattr(ScraperService, "fetch_with_playwright")
    assert hasattr(ScraperService, "extract_price")
    assert hasattr(ScraperService, "parse_price")


def test_store_detector_facade_backward_compatibility():
    """Ensure store_detector.py facade retains all legacy exports cleanly."""
    for name in [
        "classify_platform_from_url",
        "classify_platform_from_html",
        "detect_platform_details",
        "detect_platform",
        "detect_platform_from_html",
        "get_handler_for_platform",
        "PlatformDetectionResult",
        "PlatformDetector",
        "HANDLER_CLASSES",
    ]:
        assert hasattr(store_detector_mod, name), f"store_detector missing {name}"


def test_parser_unit():
    """Verify parser component correctly parses currencies and prices."""
    price, cur = parse_price("$49.99")
    assert price == Decimal("49.99")
    assert cur == "USD"

    price, cur = parse_price("£120.50")
    assert price == Decimal("120.50")
    assert cur == "GBP"

    price, cur = parse_price("€1.299,00")
    assert price == Decimal("1299.00")
    assert cur == "EUR"

    html = '<div class="product-price"><span class="price">$89.99</span></div>'
    extracted_price, extracted_cur = extract_price_from_html(html, "generic")
    assert extracted_price == Decimal("89.99")
    assert extracted_cur == "USD"


def test_extract_price_from_html_signature():
    """Verify extract_price_from_html signature requires html and retailer without defaults."""
    sig = inspect.signature(extract_price_from_html)
    assert list(sig.parameters.keys()) == ["html", "retailer"]
    assert sig.parameters["retailer"].default is inspect.Parameter.empty

    with pytest.raises(TypeError):
        extract_price_from_html("<div>$10</div>")  # type: ignore[call-arg]


def test_engine_url_validation():
    """Verify engine component validates URLs and blocks private addresses."""
    valid, err = validate_url("https://example.com/item/1")
    assert valid is True
    assert err is None

    valid, err = validate_url("http://example.com/item/1")
    assert valid is False

    valid, err = validate_url("https://localhost/item")
    assert valid is False

    valid, err = validate_url("https://169.254.169.254/latest")
    assert valid is False


def test_detector_classification():
    """Verify detector classifies shopify and woocommerce platforms."""
    res = classify_platform_from_url("https://mystore.myshopify.com/products/widget")
    assert res is not None
    assert res[0] == "shopify"

    res_wc = classify_platform_from_url("https://example.com/product/widget")
    assert res_wc is not None
    assert res_wc[0] == "woocommerce"
