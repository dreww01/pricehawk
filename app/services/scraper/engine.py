"""Network execution, Playwright browser lifecycle, and transient retry handling."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import random
import re
import sys
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import httpx

from app.services.scraper.exceptions import (
    FetchResult,
    ScrapeFailureReason,
    ScrapeResult,
    classify_scrape_exception,
)

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

BLOCKED_DOMAIN_SUFFIXES = {".local", ".localhost", ".internal", ".corp", ".lan", ".home", ".intranet"}


def normalize_url(url: str) -> tuple[str | None, str | None]:
    """Normalize URL: ensure HTTPS, reject HTTP and invalid formats."""
    url = url.strip()
    if not url:
        return None, "URL cannot be empty"
    if url.lower().startswith("http://"):
        return None, "HTTP is not secure. Please use the HTTPS version of this URL (replace http:// with https://)"
    if not url.startswith("https://"):
        if "." in url and " " not in url:
            url = f"https://{url}"
        else:
            return None, "Invalid URL format. Please enter a valid product URL"
    return url, None


def validate_url(url: str) -> tuple[bool, str | None]:
    """Validate URL for security (SSRF and internal subnet protection)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False, "Only HTTPS URLs are allowed"
        host = (parsed.hostname or "").lower()
        private_patterns = [
            r"^localhost$", r"^127\.", r"^10\.", r"^172\.(1[6-9]|2[0-9]|3[01])\.",
            r"^192\.168\.", r"^0\.", r"^169\.254\.", r"^::1$", r"^fc[0-9a-f]{2}:",
            r"^fd[0-9a-f]{2}:", r"^fe80:",
        ]
        if any(re.match(pat, host) for pat in private_patterns):
            return False, "Private or internal URLs are not allowed"
        if host in {"169.254.169.254", "metadata.google.internal"} or any(host.endswith(s) for s in BLOCKED_DOMAIN_SUFFIXES):
            return False, "Private or internal URLs are not allowed"
        return True, None
    except Exception as e:
        return False, f"Invalid URL: {str(e)}"


def get_retailer(url: str) -> str:
    """Extract retailer name from URL (legacy helper returning 'unknown')."""
    return "unknown"


def is_bot_challenge(html: str) -> bool:
    """Detect if HTML response is a bot challenge, captcha, or access denied page."""
    if not html:
        return False
    soup = BeautifulSoup(html[:10000], "lxml")
    title_text = (soup.title.string or "").strip().lower() if soup.title else ""
    blocked_titles = [
        "access denied", "access to this page has been denied", "just a moment...",
        "attention required! | cloudflare", "security check", "robot or human?",
        "shieldsquare captcha", "block page", "pardon our interruption",
        "sorry, we have detected unusual traffic", "403 forbidden",
    ]
    if any(bt in title_text for bt in blocked_titles):
        return True
    html_lower = html.lower()
    bot_markers = [
        "cf-browser-verification", "checking your browser before accessing",
        "challenge-platform", "cf_chl_prog", "cf_chl_opt", "distil_identify_block",
        "px-captcha", "incapsula_resource", "please verify you are a human",
        "press & hold to confirm you are a human",
    ]
    return any(marker in html_lower for marker in bot_markers)


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
    """Fetch URL using httpx with exponential backoff for transient errors."""
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
        active_client = httpx.AsyncClient(timeout=req_timeout, follow_redirects=True, max_redirects=5)
        own_client = True

    try:
        for attempt in range(retries_limit + 1):
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            try:
                response = await active_client.get(url, headers=headers)
                if response.status_code == 200:
                    if len(response.content) > 5 * 1024 * 1024:
                        return FetchResult(html=None, status_code=200, retry_count=attempt, failure_reason=ScrapeFailureReason.LAYOUT_CHANGED, error_message="Page content exceeds maximum allowed size (5MB).")
                    if is_bot_challenge(response.text):
                        return FetchResult(html=response.text, status_code=200, retry_count=attempt, failure_reason=ScrapeFailureReason.BLOCKED, error_message="Access blocked. The store denied access or requires verification.")
                    return FetchResult(html=response.text, status_code=200, retry_count=attempt)

                if response.status_code == 429 or response.status_code in (502, 503, 504):
                    if attempt < retries_limit:
                        delay = min(delay_base * (factor ** attempt), delay_max)
                        if response.status_code == 429 and response.headers.get("Retry-After", "").isdigit():
                            delay = min(float(response.headers["Retry-After"]), delay_max)
                        if jitter and delay > 0:
                            delay += random.uniform(0, min(0.5, delay * 0.1))
                        logger.warning(f"Transient HTTP {response.status_code} fetching {url} (attempt {attempt + 1}/{retries_limit + 1}). Retrying in {delay:.2f}s...")
                        await asyncio.sleep(delay)
                        continue
                    msg = "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests." if response.status_code == 429 else f"Store server temporarily unavailable (HTTP {response.status_code})."
                    reason = ScrapeFailureReason.BLOCKED if response.status_code == 429 else ScrapeFailureReason.NETWORK_ERROR
                    return FetchResult(html=None, status_code=response.status_code, retry_count=attempt, failure_reason=reason, error_message=msg)

                if response.status_code in (401, 403):
                    return FetchResult(html=response.text, status_code=response.status_code, retry_count=attempt, failure_reason=ScrapeFailureReason.BLOCKED, error_message="Access blocked. The store denied access or requires verification.")
                if response.status_code == 404:
                    return FetchResult(html=None, status_code=404, retry_count=attempt, failure_reason=ScrapeFailureReason.NOT_FOUND, error_message="Product page not found (HTTP 404).")

                return FetchResult(html=None, status_code=response.status_code, retry_count=attempt, failure_reason=ScrapeFailureReason.UNKNOWN, error_message=f"Store returned unexpected HTTP {response.status_code}.")

            except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError) as e:
                if attempt < retries_limit:
                    delay = min(delay_base * (factor ** attempt), delay_max)
                    if jitter and delay > 0:
                        delay += random.uniform(0, min(0.5, delay * 0.1))
                    logger.warning(f"Timeout fetching {url} (attempt {attempt + 1}/{retries_limit + 1}): {e}. Retrying in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    continue
                return FetchResult(html=None, status_code=None, retry_count=attempt, failure_reason=ScrapeFailureReason.TIMEOUT, error_message="Site timed out. The server took too long to respond.")

            except (httpx.NetworkError, ConnectionError, OSError) as e:
                if attempt < retries_limit:
                    delay = min(delay_base * (factor ** attempt), delay_max)
                    if jitter and delay > 0:
                        delay += random.uniform(0, min(0.5, delay * 0.1))
                    logger.warning(f"Connection error fetching {url} (attempt {attempt + 1}/{retries_limit + 1}): {e}. Retrying in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    continue
                return FetchResult(html=None, status_code=None, retry_count=attempt, failure_reason=ScrapeFailureReason.NETWORK_ERROR, error_message="Connection error. Unable to connect to the store server.")

            except Exception as e:
                logger.error(f"Unexpected error fetching {url}: {e}")
                return FetchResult(html=None, status_code=None, retry_count=attempt, failure_reason=ScrapeFailureReason.UNKNOWN, error_message=f"Scrape failed: {str(e)[:150]}")

        return FetchResult(html=None, status_code=None, retry_count=retries_limit, failure_reason=ScrapeFailureReason.UNKNOWN, error_message="Retries exhausted.")
    finally:
        if own_client:
            await active_client.aclose()


async def fetch_with_httpx(url: str, **kwargs: Any) -> str | None:
    """Fast fetch using httpx with automatic retries."""
    return (await fetch_page(url, **kwargs)).html


_playwright_executor: ThreadPoolExecutor | None = None


def _get_playwright_executor() -> ThreadPoolExecutor:
    global _playwright_executor
    if _playwright_executor is None:
        _playwright_executor = ThreadPoolExecutor(max_workers=3)
    return _playwright_executor


def _playwright_sync(url: str, user_agent: str) -> str | None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context(user_agent=user_agent).new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            return page.content()
        finally:
            browser.close()


async def _playwright_async(url: str, user_agent: str) -> str | None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context(user_agent=user_agent)).new_page()
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            return await page.content()
        finally:
            await browser.close()


async def fetch_with_playwright(url: str) -> str | None:
    """Fetch using Playwright for JS-heavy sites (thread pool on Win32, async on POSIX)."""
    user_agent = random.choice(USER_AGENTS)
    if sys.platform == "win32":
        return await asyncio.get_event_loop().run_in_executor(_get_playwright_executor(), _playwright_sync, url, user_agent)
    return await _playwright_async(url, user_agent)


class ScraperEngine:
    """Engine component interface for network and browser automation."""
    fetch_page = staticmethod(fetch_page)
    fetch_with_httpx = staticmethod(fetch_with_httpx)
    fetch_with_playwright = staticmethod(fetch_with_playwright)
