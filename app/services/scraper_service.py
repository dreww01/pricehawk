import asyncio
import logging
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ScrapeFailureReason(StrEnum):
    """Structured failure categories for price scraping."""
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    LAYOUT_CHANGED = "layout_changed"
    NETWORK_ERROR = "network_error"
    NOT_FOUND = "not_found"
    INVALID_URL = "invalid_url"
    DATABASE_ERROR = "database_error"
    UNKNOWN = "unknown"


class ScrapeException(Exception):
    """Base exception for scraping failures."""
    def __init__(
        self,
        message: str,
        failure_reason: ScrapeFailureReason = ScrapeFailureReason.UNKNOWN,
        retryable: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.failure_reason = failure_reason
        self.retryable = retryable
        self.status_code = status_code


class ScrapeTimeoutError(ScrapeException):
    def __init__(self, message: str = "Site timed out. The server took too long to respond."):
        super().__init__(message, failure_reason=ScrapeFailureReason.TIMEOUT, retryable=True)


class ScrapeNetworkError(ScrapeException):
    def __init__(self, message: str = "Connection error. Unable to connect to the store server."):
        super().__init__(message, failure_reason=ScrapeFailureReason.NETWORK_ERROR, retryable=True)


class ScrapeRateLimitError(ScrapeException):
    def __init__(self, message: str = "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests."):
        super().__init__(message, failure_reason=ScrapeFailureReason.BLOCKED, retryable=True, status_code=429)


class ScrapeAccessDeniedError(ScrapeException):
    def __init__(self, message: str = "Access blocked. The store denied access or requires verification."):
        super().__init__(message, failure_reason=ScrapeFailureReason.BLOCKED, retryable=False, status_code=403)


class ScrapeNotFoundError(ScrapeException):
    def __init__(self, message: str = "Product page not found (HTTP 404)."):
        super().__init__(message, failure_reason=ScrapeFailureReason.NOT_FOUND, retryable=False, status_code=404)


class ScrapeLayoutError(ScrapeException):
    def __init__(self, message: str = "Page layout changed or unsupported. Price could not be located on the product page."):
        super().__init__(message, failure_reason=ScrapeFailureReason.LAYOUT_CHANGED, retryable=False)


class DatabasePersistenceError(ScrapeException):
    def __init__(self, message: str = "Failed to record price history."):
        super().__init__(message, failure_reason=ScrapeFailureReason.DATABASE_ERROR, retryable=True)


@dataclass
class ScrapeResult:
    price: Decimal | None
    currency: str
    status: str  # 'success' or 'failed'
    error_message: str | None = None
    failure_reason: str | None = None
    retry_count: int = 0

    @property
    def failure_status(self) -> str | None:
        """Alias for failure_reason."""
        return self.failure_reason


@dataclass
class FetchResult:
    html: str | None
    status_code: int | None
    retry_count: int = 0
    failure_reason: str | None = None
    error_message: str | None = None


def is_bot_challenge(html: str) -> bool:
    """Detect if HTML response is a bot challenge, captcha, or access denied page."""
    if not html:
        return False

    soup = BeautifulSoup(html[:10000], "lxml")
    title_text = (soup.title.string or "").strip().lower() if soup.title else ""

    blocked_titles = [
        "access denied",
        "access to this page has been denied",
        "just a moment...",
        "attention required! | cloudflare",
        "security check",
        "robot or human?",
        "shieldsquare captcha",
        "block page",
        "pardon our interruption",
        "sorry, we have detected unusual traffic",
        "403 forbidden",
    ]
    if any(bt in title_text for bt in blocked_titles):
        return True

    # Check for technical markers in head/body
    html_lower = html.lower()
    bot_markers = [
        "cf-browser-verification",
        "checking your browser before accessing",
        "challenge-platform",
        "cf_chl_prog",
        "cf_chl_opt",
        "distil_identify_block",
        "px-captcha",
        "incapsula_resource",
        "please verify you are a human",
        "press & hold to confirm you are a human",
    ]
    return any(marker in html_lower for marker in bot_markers)


def classify_scrape_exception(exc: Exception) -> tuple[str, str]:
    """
    Classify an exception into a failure reason and user-friendly error message.
    Returns (failure_reason, user_friendly_message).
    """
    if isinstance(exc, ScrapeException):
        return exc.failure_reason, exc.message

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return (
            ScrapeFailureReason.TIMEOUT,
            "Site timed out. The server took too long to respond.",
        )

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code if exc.response is not None else 0
        if code in (401, 403):
            return (
                ScrapeFailureReason.BLOCKED,
                "Access blocked. The store denied access or requires verification.",
            )
        if code == 429:
            return (
                ScrapeFailureReason.BLOCKED,
                "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests.",
            )
        if code == 404:
            return (
                ScrapeFailureReason.NOT_FOUND,
                "Product page not found (HTTP 404).",
            )
        if code in (502, 503, 504):
            return (
                ScrapeFailureReason.NETWORK_ERROR,
                f"Store server temporarily unavailable (HTTP {code}).",
            )

    if isinstance(exc, (httpx.NetworkError, ConnectionError, OSError)):
        return (
            ScrapeFailureReason.NETWORK_ERROR,
            "Connection error. Unable to connect to the store server.",
        )

    exc_str = str(exc).lower()
    if "timeout" in exc_str or "timed out" in exc_str:
        return (
            ScrapeFailureReason.TIMEOUT,
            "Site timed out. The server took too long to respond.",
        )
    if "block" in exc_str or "forbidden" in exc_str or "access denied" in exc_str:
        return (
            ScrapeFailureReason.BLOCKED,
            "Access blocked. The store denied access or requires verification.",
        )
    if "connection refused" in exc_str or "connection reset" in exc_str or "network" in exc_str:
        return (
            ScrapeFailureReason.NETWORK_ERROR,
            "Connection error. Unable to connect to the store server.",
        )

    return (
        ScrapeFailureReason.UNKNOWN,
        f"Scrape failed: {str(exc)[:150]}",
    )


classify_error_message = classify_scrape_exception


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


async def fetch_page(
    url: str,
    max_retries: int | None = None,
    base_delay: float | None = None,
    max_delay: float | None = None,
    backoff_factor: float | None = None,
    jitter: bool = True,
    client: httpx.AsyncClient | None = None,
    timeout: float | None = None,
) -> FetchResult:
    """
    Fetch a URL using httpx with automatic exponential backoff retries
    for transient connection errors, timeouts, and rate limits.
    """
    from app.core.config import get_settings
    settings = get_settings()

    retries_limit = max_retries if max_retries is not None else settings.scraper_max_retries
    delay_base = base_delay if base_delay is not None else settings.scraper_base_delay
    delay_max = max_delay if max_delay is not None else settings.scraper_max_delay
    factor = backoff_factor if backoff_factor is not None else settings.scraper_backoff_factor
    req_timeout = timeout if timeout is not None else settings.scraper_timeout

    own_client = False
    active_client = client
    if active_client is None:
        active_client = httpx.AsyncClient(
            timeout=req_timeout,
            follow_redirects=True,
            max_redirects=5,
        )
        own_client = True

    try:
        for attempt in range(retries_limit + 1):
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            try:
                response = await active_client.get(url, headers=headers)

                # 200 OK
                if response.status_code == 200:
                    if len(response.content) > 5 * 1024 * 1024:
                        return FetchResult(
                            html=None,
                            status_code=200,
                            retry_count=attempt,
                            failure_reason=ScrapeFailureReason.LAYOUT_CHANGED,
                            error_message="Page content exceeds maximum allowed size (5MB).",
                        )
                    if is_bot_challenge(response.text):
                        return FetchResult(
                            html=response.text,
                            status_code=200,
                            retry_count=attempt,
                            failure_reason=ScrapeFailureReason.BLOCKED,
                            error_message="Access blocked. The store denied access or requires verification.",
                        )
                    return FetchResult(
                        html=response.text,
                        status_code=200,
                        retry_count=attempt,
                    )

                # Transient rate limit (429) or transient server errors (502, 503, 504)
                if response.status_code == 429 or response.status_code in (502, 503, 504):
                    if attempt < retries_limit:
                        delay = min(delay_base * (factor ** attempt), delay_max)
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            if retry_after and retry_after.isdigit():
                                delay = min(float(retry_after), delay_max)
                        if jitter and delay > 0:
                            delay += random.uniform(0, min(0.5, delay * 0.1))
                        logger.warning(
                            f"Transient HTTP {response.status_code} fetching {url} "
                            f"(attempt {attempt + 1}/{retries_limit + 1}). Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        if response.status_code == 429:
                            return FetchResult(
                                html=None,
                                status_code=429,
                                retry_count=attempt,
                                failure_reason=ScrapeFailureReason.BLOCKED,
                                error_message="Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests.",
                            )
                        else:
                            return FetchResult(
                                html=None,
                                status_code=response.status_code,
                                retry_count=attempt,
                                failure_reason=ScrapeFailureReason.NETWORK_ERROR,
                                error_message=f"Store server temporarily unavailable (HTTP {response.status_code}).",
                            )

                # Non-transient blocked (401, 403)
                if response.status_code in (401, 403):
                    return FetchResult(
                        html=response.text,
                        status_code=response.status_code,
                        retry_count=attempt,
                        failure_reason=ScrapeFailureReason.BLOCKED,
                        error_message="Access blocked. The store denied access or requires verification.",
                    )

                # 404 Not Found
                if response.status_code == 404:
                    return FetchResult(
                        html=None,
                        status_code=404,
                        retry_count=attempt,
                        failure_reason=ScrapeFailureReason.NOT_FOUND,
                        error_message="Product page not found (HTTP 404).",
                    )

                # Any other unexpected HTTP status
                return FetchResult(
                    html=None,
                    status_code=response.status_code,
                    retry_count=attempt,
                    failure_reason=ScrapeFailureReason.UNKNOWN,
                    error_message=f"Store returned unexpected HTTP {response.status_code}.",
                )

            except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError) as e:
                if attempt < retries_limit:
                    delay = min(delay_base * (factor ** attempt), delay_max)
                    if jitter and delay > 0:
                        delay += random.uniform(0, min(0.5, delay * 0.1))
                    logger.warning(
                        f"Timeout fetching {url} (attempt {attempt + 1}/{retries_limit + 1}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    return FetchResult(
                        html=None,
                        status_code=None,
                        retry_count=attempt,
                        failure_reason=ScrapeFailureReason.TIMEOUT,
                        error_message="Site timed out. The server took too long to respond.",
                    )

            except (httpx.NetworkError, ConnectionError, OSError) as e:
                if attempt < retries_limit:
                    delay = min(delay_base * (factor ** attempt), delay_max)
                    if jitter and delay > 0:
                        delay += random.uniform(0, min(0.5, delay * 0.1))
                    logger.warning(
                        f"Connection error fetching {url} (attempt {attempt + 1}/{retries_limit + 1}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    return FetchResult(
                        html=None,
                        status_code=None,
                        retry_count=attempt,
                        failure_reason=ScrapeFailureReason.NETWORK_ERROR,
                        error_message="Connection error. Unable to connect to the store server.",
                    )

            except Exception as e:
                logger.error(f"Unexpected error fetching {url}: {e}")
                return FetchResult(
                    html=None,
                    status_code=None,
                    retry_count=attempt,
                    failure_reason=ScrapeFailureReason.UNKNOWN,
                    error_message=f"Scrape failed: {str(e)[:150]}",
                )

        return FetchResult(
            html=None,
            status_code=None,
            retry_count=retries_limit,
            failure_reason=ScrapeFailureReason.UNKNOWN,
            error_message="Retries exhausted.",
        )

    finally:
        if own_client:
            await active_client.aclose()


async def fetch_with_httpx(url: str, **kwargs: Any) -> str | None:
    """Fast fetch using httpx with automatic retries (wrapper)."""
    res = await fetch_page(url, **kwargs)
    return res.html


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


async def scrape_url(
    url: str,
    max_retries: int | None = None,
    base_delay: float | None = None,
    crawl_delay: bool | None = None,
    client: httpx.AsyncClient | None = None,
) -> ScrapeResult:
    """
    Scrape price from URL with automatic exponential backoff retries and structured error reporting.
    Strategy:
    1. Normalize URL (add https:// if needed)
    2. Validate URL for security
    3. Try httpx with automatic backoff retries for transient errors
    4. If no price and site didn't definitively fail (e.g. 404, timeout), try Playwright
    5. Return clear, user-friendly failure statuses (timeout, blocked, layout_changed, etc.)
    """
    # Normalize URL (add https:// if missing)
    normalized_url, norm_error = normalize_url(url)
    if norm_error:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=norm_error,
            failure_reason=ScrapeFailureReason.INVALID_URL,
        )

    url = normalized_url  # Use normalized URL from here

    # Validate URL for security
    is_valid, error = validate_url(url)
    if not is_valid:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=error,
            failure_reason=ScrapeFailureReason.INVALID_URL,
        )

    retailer = get_retailer(url)

    # Respect crawl delay unless in test environment or explicitly disabled
    from app.core.config import get_settings
    settings = get_settings()
    apply_crawl = crawl_delay if crawl_delay is not None else (settings.env != "test")
    if apply_crawl:
        await asyncio.sleep(random.uniform(2, 5))

    # Fast fetch with automatic backoff retries
    fetch_res = await fetch_page(
        url,
        max_retries=max_retries,
        base_delay=base_delay,
        client=client,
    )
    retries_used = fetch_res.retry_count

    # If HTML was successfully fetched and access was not blocked, try extracting price
    if fetch_res.html and fetch_res.failure_reason != ScrapeFailureReason.BLOCKED:
        price, currency = extract_price_from_html(fetch_res.html, retailer)
        if price:
            return ScrapeResult(
                price=price,
                currency=currency,
                status="success",
                retry_count=retries_used,
            )

    # If the page was definitively not found (404), return immediately without slow browser fallback
    if fetch_res.status_code == 404:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=fetch_res.error_message or "Product page not found (HTTP 404).",
            failure_reason=ScrapeFailureReason.NOT_FOUND,
            retry_count=retries_used,
        )

    # If the site timed out across all retries, return timeout status
    if fetch_res.failure_reason == ScrapeFailureReason.TIMEOUT:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=fetch_res.error_message or "Site timed out. The server took too long to respond.",
            failure_reason=ScrapeFailureReason.TIMEOUT,
            retry_count=retries_used,
        )

    # If connection failed across all retries, return network error
    if fetch_res.failure_reason == ScrapeFailureReason.NETWORK_ERROR:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=fetch_res.error_message or "Connection error. Unable to connect to the store server.",
            failure_reason=ScrapeFailureReason.NETWORK_ERROR,
            retry_count=retries_used,
        )

    # If rate limited across all retries, return blocked status
    if fetch_res.status_code == 429:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=fetch_res.error_message or "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests.",
            failure_reason=ScrapeFailureReason.BLOCKED,
            retry_count=retries_used,
        )

    # For JS-rendered pages or potential bot-challenge bypass, try Playwright fallback
    try:
        pw_html = await fetch_with_playwright(url)
        if pw_html:
            if is_bot_challenge(pw_html):
                return ScrapeResult(
                    price=None,
                    currency="USD",
                    status="failed",
                    error_message="Access blocked. The store denied access or requires verification.",
                    failure_reason=ScrapeFailureReason.BLOCKED,
                    retry_count=retries_used,
                )
            price, currency = extract_price_from_html(pw_html, retailer)
            if price:
                return ScrapeResult(
                    price=price,
                    currency=currency,
                    status="success",
                    retry_count=retries_used,
                )
    except Exception as pw_err:
        logger.debug(f"Playwright fallback failed for {url}: {pw_err}")

    # If access was blocked by anti-bot or 403
    if fetch_res.failure_reason == ScrapeFailureReason.BLOCKED:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=fetch_res.error_message or "Access blocked. The store denied access or requires verification.",
            failure_reason=ScrapeFailureReason.BLOCKED,
            retry_count=retries_used,
        )

    # If HTML was loaded (200 OK) but no price could be found by any selector:
    if fetch_res.status_code == 200:
        return ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message="Page layout changed or unsupported. Price could not be located on the product page.",
            failure_reason=ScrapeFailureReason.LAYOUT_CHANGED,
            retry_count=retries_used,
        )

    # Fallback for other failures
    return ScrapeResult(
        price=None,
        currency="USD",
        status="failed",
        error_message=fetch_res.error_message or "Could not extract price. This may not be a supported e-commerce store, or the product page structure is not recognized.",
        failure_reason=fetch_res.failure_reason or ScrapeFailureReason.UNKNOWN,
        retry_count=retries_used,
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
    from app.services.account_service import is_product_deleted, is_user_deleted

    sb = get_supabase_client()  # Use service key

    # Fetch competitor URL
    try:
        comp_response = (
            sb.table("competitors")
            .select("id, url, product_id, products(id, user_id, is_active)")
            .eq("id", competitor_id)
            .single()
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to query competitor {competitor_id}: {e}")
        return {
            "scrape_result": {
                "status": "failed",
                "price": None,
                "currency": "USD",
                "error": f"Failed to retrieve competitor: {str(e)[:150]}",
                "failure_reason": ScrapeFailureReason.UNKNOWN,
                "retry_count": 0,
            },
            "alert_result": None,
        }

    if not comp_response.data:
        return {
            "scrape_result": {
                "status": "failed",
                "price": None,
                "currency": "USD",
                "error": "Competitor not found",
                "failure_reason": ScrapeFailureReason.NOT_FOUND,
                "retry_count": 0,
            },
            "alert_result": None,
        }

    comp_record = comp_response.data
    prod_id = comp_record.get("product_id") if isinstance(comp_record, dict) else None
    if prod_id and is_product_deleted(prod_id):
        return {
            "scrape_result": {
                "status": "cancelled",
                "price": None,
                "currency": "USD",
                "error": "Product deleted",
                "failure_reason": ScrapeFailureReason.NOT_FOUND,
                "retry_count": 0,
            },
            "alert_result": None,
        }

    prod_info = comp_record.get("products") if isinstance(comp_record, dict) else None
    if isinstance(prod_info, dict):
        if prod_info.get("is_active") is False:
            return {
                "scrape_result": {
                    "status": "cancelled",
                    "price": None,
                    "currency": "USD",
                    "error": "Product inactive",
                    "failure_reason": ScrapeFailureReason.NOT_FOUND,
                    "retry_count": 0,
                },
                "alert_result": None,
            }
        owner_id = prod_info.get("user_id")
        if owner_id and is_user_deleted(owner_id):
            return {
                "scrape_result": {
                    "status": "cancelled",
                    "price": None,
                    "currency": "USD",
                    "error": "Account deleted",
                    "failure_reason": ScrapeFailureReason.NOT_FOUND,
                    "retry_count": 0,
                },
                "alert_result": None,
            }

    url = comp_record["url"]

    # Scrape the URL
    try:
        scrape_result = await scrape_url(url)
    except Exception as e:
        logger.error(f"Unexpected exception in scrape_url for {url}: {e}")
        failure_reason, friendly_msg = classify_scrape_exception(e)
        scrape_result = ScrapeResult(
            price=None,
            currency="USD",
            status="failed",
            error_message=friendly_msg,
            failure_reason=failure_reason,
            retry_count=0,
        )

    # Store price history (with retries for transient DB failures)
    db_persistence_error: Exception | None = None
    max_db_retries = 3
    price_data = {
        "competitor_id": competitor_id,
        "price": float(scrape_result.price) if scrape_result.price else None,
        "currency": scrape_result.currency,
        "scrape_status": scrape_result.status,
        "error_message": scrape_result.error_message,
    }

    for db_attempt in range(max_db_retries):
        try:
            sb.table("price_history").insert(price_data).execute()
            db_persistence_error = None
            break
        except Exception as e:
            db_persistence_error = e
            logger.warning(
                f"DB insert attempt {db_attempt + 1}/{max_db_retries} failed for competitor {competitor_id}: {e}"
            )
            if db_attempt < max_db_retries - 1:
                await asyncio.sleep(min(0.05 * (2 ** db_attempt), 0.5))

    if db_persistence_error:
        logger.error(
            f"Failed to record price history for competitor {competitor_id} after {max_db_retries} attempts: {db_persistence_error}"
        )
    else:
        # Invalidate dashboard cache immediately once price history has committed
        try:
            from app.services.dashboard_cache import invalidate_dashboard_cache_for_competitor
            invalidate_dashboard_cache_for_competitor(competitor_id, client=sb)
        except Exception as e:
            logger.debug(f"Failed to invalidate dashboard cache for competitor {competitor_id}: {e}")

    # Check for alerts only if scrape was successful AND database persistence succeeded
    alert_result = None
    if not db_persistence_error and scrape_result.status == "success" and scrape_result.price:
        try:
            alert_service = AlertService()
            alert_result = await alert_service.check_price_change_and_alert(
                competitor_id=competitor_id,
                new_price=scrape_result.price,
                currency=scrape_result.currency,
            )
        except Exception as e:
            logger.error(f"Alert evaluation failed for competitor {competitor_id}: {e}")
        finally:
            try:
                from app.services.dashboard_cache import invalidate_dashboard_cache_for_competitor
                invalidate_dashboard_cache_for_competitor(competitor_id, client=sb)
            except Exception:
                pass

    # If persistence failed, the competitor must not be reported as successfully completed
    if db_persistence_error:
        return {
            "scrape_result": {
                "status": "failed",
                "price": float(scrape_result.price) if scrape_result.price else None,
                "currency": scrape_result.currency,
                "error": f"Failed to record price history: {str(db_persistence_error)[:150]}",
                "failure_reason": ScrapeFailureReason.DATABASE_ERROR,
                "retry_count": scrape_result.retry_count,
            },
            "alert_result": None,
        }

    # Automatically invalidate dashboard cache when new price scrapes are persisted
    try:
        from app.services.dashboard_cache import invalidate_dashboard_cache_for_competitor
        invalidate_dashboard_cache_for_competitor(competitor_id, client=sb)
    except Exception as e:
        logger.debug(f"Failed to invalidate dashboard cache for competitor {competitor_id}: {e}")

    return {
        "scrape_result": {
            "status": scrape_result.status,
            "price": float(scrape_result.price) if scrape_result.price else None,
            "currency": scrape_result.currency,
            "error": scrape_result.error_message,
            "failure_reason": scrape_result.failure_reason,
            "retry_count": scrape_result.retry_count,
        },
        "alert_result": alert_result,
    }
