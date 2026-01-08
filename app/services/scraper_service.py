import asyncio
import random
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings


@dataclass
class ScrapeResult:
    price: Decimal | None
    currency: str
    status: str  # 'success' or 'failed'
    error_message: str | None = None


# Whitelisted domains
ALLOWED_DOMAINS = {
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.ca",
    "ebay.com", "ebay.co.uk", "ebay.de",
    "walmart.com","neostore.ng"
}

# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# CSS selectors per retailer (ordered by priority)
PRICE_SELECTORS = {
    "amazon": [
        ".a-price .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price-whole",
        "[data-a-color='price'] .a-offscreen",
    ],
    "ebay": [
        ".x-price-primary span",
        "#prcIsum",
        ".display-price",
        "[itemprop='price']",
    ],
    "walmart": [
        "[itemprop='price']",
        ".price-characteristic",
        "[data-automation='buybox-price']",
    ],
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


def validate_url(url: str) -> tuple[bool, str | None]:
    """Validate URL against whitelist and block private IPs."""
    try:
        parsed = urlparse(url)

        if parsed.scheme != "https":
            return False, "Only HTTPS URLs allowed"

        hostname = parsed.hostname or ""

        # Block private IPs
        private_patterns = [
            r"^localhost$",
            r"^127\.",
            r"^10\.",
            r"^172\.(1[6-9]|2[0-9]|3[01])\.",
            r"^192\.168\.",
            r"^0\.",
        ]
        for pattern in private_patterns:
            if re.match(pattern, hostname):
                return False, "Private/local URLs not allowed"

        # Check whitelist
        domain_match = any(
            hostname == d or hostname.endswith(f".{d}")
            for d in ALLOWED_DOMAINS
        )
        if not domain_match:
            return False, f"Domain not in whitelist. Allowed: {', '.join(ALLOWED_DOMAINS)}"

        return True, None
    except Exception as e:
        return False, str(e)


def get_retailer(url: str) -> str:
    """Extract retailer name from URL."""
    hostname = urlparse(url).hostname or ""
    if "amazon" in hostname:
        return "amazon"
    if "ebay" in hostname:
        return "ebay"
    if "walmart" in hostname:
        return "walmart"
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


_cached_proxies: list[str] = []
_proxy_cache_time: float = 0


async def fetch_webshare_proxies() -> list[str]:
    """Fetch proxy list from Webshare API."""
    global _cached_proxies, _proxy_cache_time
    import time

    # Cache proxies for 5 minutes
    if _cached_proxies and (time.time() - _proxy_cache_time) < 300:
        return _cached_proxies

    settings = get_settings()
    if not settings.webshare_api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size=10",
                headers={"Authorization": f"Token {settings.webshare_api_key}"}
            )
            response.raise_for_status()
            data = response.json()

            proxies = []
            for p in data.get("results", []):
                if p.get("valid"):
                    proxy_url = f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                    proxies.append(proxy_url)

            _cached_proxies = proxies
            _proxy_cache_time = time.time()
            return proxies
    except Exception:
        return _cached_proxies  # Return stale cache on error


async def get_proxy_list() -> list[str | None]:
    """Get list of proxies, including None for direct connection."""
    proxies: list[str | None] = await fetch_webshare_proxies()

    # Always add None as fallback (direct connection)
    proxies_with_fallback: list[str | None] = list(proxies)
    proxies_with_fallback.append(None)
    return proxies_with_fallback


async def fetch_with_httpx(url: str, proxy: str | None = None) -> str | None:
    """Fast fetch using httpx with optional proxy."""
    async with httpx.AsyncClient(
        proxy=proxy,
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


async def fetch_with_playwright(url: str, proxy: str | None = None) -> str | None:
    """Fallback fetch using Playwright for JS-heavy sites."""
    from playwright.async_api import async_playwright

    # Parse proxy URL
    proxy_config = None
    if proxy:
        parsed = urlparse(proxy)
        proxy_config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password,
        }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            proxy=proxy_config,
        )
        page = await context.new_page()

        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(2)  # Wait for JS
            html = await page.content()
            return html
        finally:
            await browser.close()


async def scrape_url(url: str) -> ScrapeResult:
    """
    Scrape price from URL.
    Strategy:
    1. Try each proxy with httpx
    2. If no price, try each proxy with Playwright
    3. Final fallback: direct connection (no proxy)
    """
    # Validate URL
    is_valid, error = validate_url(url)
    if not is_valid:
        return ScrapeResult(price=None, currency="USD", status="failed", error_message=error)

    retailer = get_retailer(url)
    proxies = await get_proxy_list()
    last_error = None

    # Random delay (2-5s)
    await asyncio.sleep(random.uniform(2, 5))

    # Try httpx with each proxy
    for proxy in proxies:
        try:
            html = await fetch_with_httpx(url, proxy)
            if html:
                price, currency = extract_price_from_html(html, retailer)
                if price:
                    return ScrapeResult(price=price, currency=currency, status="success")
        except Exception as e:
            last_error = str(e)[:200]
            continue

    # Try Playwright with each proxy (for JS-rendered pages)
    for proxy in proxies:
        try:
            html = await fetch_with_playwright(url, proxy)
            if html:
                price, currency = extract_price_from_html(html, retailer)
                if price:
                    return ScrapeResult(price=price, currency=currency, status="success")
        except Exception as e:
            last_error = str(e)[:200]
            continue

    return ScrapeResult(
        price=None, currency="USD", status="failed",
        error_message=last_error or "Could not extract price from page"
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
