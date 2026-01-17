import asyncio
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


@dataclass
class ScrapeResult:
    price: Decimal | None
    currency: str
    status: str  # 'success' or 'failed'
    error_message: str | None = None


# Blocked internal domains (SSRF protection)
BLOCKED_DOMAIN_SUFFIXES = {
    ".local", ".localhost", ".internal", ".corp", ".lan", ".home", ".intranet"
}

# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# CSS selectors per platform (ordered by priority)
PRICE_SELECTORS = {
    "shopify": [
        ".price__current .money",
        ".product__price .money",
        ".product-price .money",
        "[data-product-price]",
        ".price-item--regular",
        ".price-item--sale",
        ".ProductMeta__Price",
        ".product-single__price",
    ],
    "woocommerce": [
        ".woocommerce-Price-amount bdi",
        ".woocommerce-Price-amount",
        ".price ins .amount",
        ".price .amount",
        ".summary .price",
        "p.price span.amount",
    ],
    "generic": [
        "[itemprop='price']",
        "[data-price]",
        "[data-product-price]",
        "meta[property='product:price:amount']",
        ".price",
        ".product-price",
        ".current-price",
        ".sale-price",
        ".regular-price",
        "#product-price",
        ".price-value",
        ".amount",
    ],
}


def normalize_url(url: str) -> tuple[str | None, str | None]:
    """
    Normalize URL: add https:// if missing, reject http://.
    Returns (normalized_url, error_message).
    """
    url = url.strip()

    if not url:
        return None, "URL cannot be empty"

    # Check for http:// (insecure)
    if url.lower().startswith("http://"):
        return None, "HTTP is not secure. Please use the HTTPS version of this URL (replace http:// with https://)"

    # Add https:// if no scheme
    if not url.startswith("https://"):
        # Check if it looks like a domain (has a dot, no spaces)
        if "." in url and " " not in url:
            url = f"https://{url}"
        else:
            return None, "Invalid URL format. Please enter a valid product URL"

    return url, None


def validate_url(url: str) -> tuple[bool, str | None]:
    """Validate URL for security (SSRF protection)."""
    try:
        parsed = urlparse(url)

        if parsed.scheme != "https":
            return False, "Only HTTPS URLs are allowed"

        hostname = parsed.hostname or ""
        hostname_lower = hostname.lower()

        # Block private/internal IPs (SSRF protection)
        private_patterns = [
            r"^localhost$",
            r"^127\.",                          # Loopback
            r"^10\.",                           # Private class A
            r"^172\.(1[6-9]|2[0-9]|3[01])\.",   # Private class B
            r"^192\.168\.",                     # Private class C
            r"^0\.",                            # "This" network
            r"^169\.254\.",                     # Link-local
            r"^::1$",                           # IPv6 loopback
            r"^fc[0-9a-f]{2}:",                 # IPv6 private
            r"^fd[0-9a-f]{2}:",                 # IPv6 private
            r"^fe80:",                          # IPv6 link-local
        ]
        for pattern in private_patterns:
            if re.match(pattern, hostname_lower):
                return False, "Private or internal URLs are not allowed"

        # Block cloud metadata endpoints (AWS, GCP, Azure)
        if hostname_lower in {"169.254.169.254", "metadata.google.internal"}:
            return False, "Private or internal URLs are not allowed"

        # Block internal domain suffixes
        for suffix in BLOCKED_DOMAIN_SUFFIXES:
            if hostname_lower.endswith(suffix):
                return False, "Private or internal URLs are not allowed"

        return True, None
    except Exception as e:
        return False, f"Invalid URL: {str(e)}"


def get_retailer(url: str) -> str:
    """Extract retailer name from URL (legacy, returns 'unknown' for platform detection)."""
    return "unknown"


def detect_platform_from_html(html: str) -> str | None:
    """Detect e-commerce platform from HTML content."""
    html_lower = html.lower()
    if "shopify" in html_lower or "cdn.shopify" in html_lower:
        return "shopify"
    if "woocommerce" in html_lower or "wc-block" in html_lower:
        return "woocommerce"
    return None


def parse_price(text: str) -> tuple[Decimal | None, str]:
    """Extract price and currency from text."""
    if not text:
        return None, "USD"

    text = text.strip()

    # Detect currency (check NGN/₦ first since Nigerian stores are common)
    currency = "USD"
    if "₦" in text or "NGN" in text.upper():
        currency = "NGN"
    elif "£" in text:
        currency = "GBP"
    elif "€" in text:
        currency = "EUR"
    elif "CAD" in text or "C$" in text:
        currency = "CAD"

    # Remove currency symbols and clean
    cleaned = re.sub(r"[£€$₦,\s]", "", text)
    cleaned = re.sub(r"[A-Za-z]", "", cleaned)

    # Handle European format (1.234,56 → 1234.56)
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        # Could be 1,234 or 1,23 - check decimal places
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        return Decimal(cleaned), currency
    except Exception:
        return None, currency


def extract_price_from_html(html: str, retailer: str) -> tuple[Decimal | None, str]:
    """Extract price using BeautifulSoup with retailer-specific selectors."""
    soup = BeautifulSoup(html, "lxml")

    # Build selector priority: retailer-specific → platform-detected → generic
    selector_groups = []

    # 1. Try retailer-specific selectors first
    if retailer in PRICE_SELECTORS:
        selector_groups.append(PRICE_SELECTORS[retailer])

    # 2. Detect platform from HTML (Shopify, WooCommerce)
    if retailer == "unknown":
        detected_platform = detect_platform_from_html(html)
        if detected_platform and detected_platform in PRICE_SELECTORS:
            selector_groups.append(PRICE_SELECTORS[detected_platform])

    # 3. Always add generic selectors as final fallback
    selector_groups.append(PRICE_SELECTORS["generic"])

    # Try each selector group
    for selectors in selector_groups:
        for selector in selectors:
            try:
                # Handle meta tags differently
                if selector.startswith("meta["):
                    elements = soup.select(selector)
                    for el in elements:
                        content = el.get("content", "")
                        if content:
                            price, currency = parse_price(str(content))
                            if price and price > 0:
                                return price, currency
                else:
                    elements = soup.select(selector)
                    for el in elements:
                        text = el.get_text(strip=True)
                        price, currency = parse_price(text)
                        if price and price > 0:
                            return price, currency
            except Exception:
                continue

    return None, "USD"


async def fetch_with_httpx(url: str) -> str | None:
    """Fast fetch using httpx."""
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = await client.get(url, headers=headers)

        # Check content length (5MB limit)
        if len(response.content) > 5 * 1024 * 1024:
            return None

        return response.text


# Thread pool for Windows sync Playwright fallback (lazy init)
_playwright_executor: ThreadPoolExecutor | None = None


def _get_playwright_executor() -> ThreadPoolExecutor:
    """Get or create thread pool for sync Playwright."""
    global _playwright_executor
    if _playwright_executor is None:
        _playwright_executor = ThreadPoolExecutor(max_workers=3)
    return _playwright_executor


def _playwright_sync(url: str, user_agent: str) -> str | None:
    """Sync Playwright fetch - used on Windows via thread pool."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=user_agent)
        page = context.new_page()

        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # Wait for JS
            return page.content()
        finally:
            browser.close()


async def _playwright_async(url: str, user_agent: str) -> str | None:
    """Async Playwright fetch - used on Linux/macOS."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(2)  # Wait for JS
            return await page.content()
        finally:
            await browser.close()


async def fetch_with_playwright(url: str) -> str | None:
    """
    Fallback fetch using Playwright for JS-heavy sites.
    Uses sync Playwright on Windows (asyncio subprocess limitation),
    async Playwright on Linux/macOS.
    """
    user_agent = random.choice(USER_AGENTS)

    if sys.platform == "win32":
        # Windows: run sync Playwright in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _get_playwright_executor(),
            _playwright_sync,
            url, user_agent
        )
    else:
        # Linux/macOS: use async Playwright directly
        return await _playwright_async(url, user_agent)


async def scrape_url(url: str) -> ScrapeResult:
    """
    Scrape price from URL.
    Strategy:
    1. Normalize URL (add https:// if needed)
    2. Validate URL for security
    3. Try httpx first (fast)
    4. If no price, try Playwright (for JS-rendered pages)
    """
    # Normalize URL (add https:// if missing)
    normalized_url, norm_error = normalize_url(url)
    if norm_error:
        return ScrapeResult(price=None, currency="USD", status="failed", error_message=norm_error)

    url = normalized_url  # Use normalized URL from here

    # Validate URL for security
    is_valid, error = validate_url(url)
    if not is_valid:
        return ScrapeResult(price=None, currency="USD", status="failed", error_message=error)

    retailer = get_retailer(url)
    last_error = None

    # Random delay (2-5s)
    await asyncio.sleep(random.uniform(2, 5))

    # Try httpx first (fast)
    try:
        html = await fetch_with_httpx(url)
        if html:
            price, currency = extract_price_from_html(html, retailer)
            if price:
                return ScrapeResult(price=price, currency=currency, status="success")
    except Exception as e:
        last_error = str(e)[:200]

    # Try Playwright for JS-rendered pages
    try:
        html = await fetch_with_playwright(url)
        if html:
            price, currency = extract_price_from_html(html, retailer)
            if price:
                return ScrapeResult(price=price, currency=currency, status="success")
    except Exception as e:
        last_error = str(e)[:200]

    return ScrapeResult(
        price=None, currency="USD", status="failed",
        error_message=last_error or "Could not extract price. This may not be a supported e-commerce store, or the product page structure is not recognized."
    )


async def scrape_and_check_alerts(competitor_id: str) -> dict[str, Any]:
    """
    Scrape a competitor URL, store the price, and check for alert triggers.

    This function combines scraping with alert detection.
    Called by Celery tasks and manual scrape endpoints.

    Args:
        competitor_id: UUID of competitor to scrape

    Returns:
        dict with keys: scrape_result, alert_result
    """
    from app.db.database import get_supabase_client
    from app.services.alert_service import AlertService

    sb = get_supabase_client()  # Use service key

    # Fetch competitor URL
    comp_response = (
        sb.table("competitors")
        .select("id, url")
        .eq("id", competitor_id)
        .single()
        .execute()
    )

    if not comp_response.data:
        return {
            "scrape_result": {"status": "failed", "error": "Competitor not found"},
            "alert_result": None
        }

    url = comp_response.data["url"]

    # Scrape the URL
    scrape_result = await scrape_url(url)

    # Store price history
    price_data = {
        "competitor_id": competitor_id,
        "price": float(scrape_result.price) if scrape_result.price else None,
        "currency": scrape_result.currency,
        "scrape_status": scrape_result.status,
        "error_message": scrape_result.error_message
    }
    sb.table("price_history").insert(price_data).execute()

    # Check for alerts if scrape was successful
    alert_result = None
    if scrape_result.status == "success" and scrape_result.price:
        alert_service = AlertService()
        alert_result = await alert_service.check_price_change_and_alert(
            competitor_id=competitor_id,
            new_price=scrape_result.price,
            currency=scrape_result.currency
        )

    return {
        "scrape_result": {
            "status": scrape_result.status,
            "price": float(scrape_result.price) if scrape_result.price else None,
            "currency": scrape_result.currency,
            "error": scrape_result.error_message
        },
        "alert_result": alert_result
    }
