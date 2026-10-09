"""Network execution, Playwright browser automation, and retry handling."""
import asyncio
import logging
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.scraper.models import (
    DatabasePersistenceError, FetchResult, ScrapeAccessDeniedError, ScrapeException,
    ScrapeFailureReason, ScrapeLayoutError, ScrapeNetworkError, ScrapeNotFoundError,
    ScrapeRateLimitError, ScrapeResult, ScrapeTimeoutError,
)

logger = logging.getLogger(__name__)

BLOCKED_DOMAIN_SUFFIXES = {".local", ".localhost", ".internal", ".corp", ".lan", ".home", ".intranet"}
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
BLOCKED_TITLES = (
    "access denied", "access to this page has been denied", "just a moment...",
    "attention required! | cloudflare", "security check", "robot or human?", "shieldsquare captcha",
    "block page", "pardon our interruption", "sorry, we have detected unusual traffic", "403 forbidden",
)
BOT_MARKERS = (
    "cf-browser-verification", "checking your browser before accessing", "challenge-platform",
    "cf_chl_prog", "cf_chl_opt", "distil_identify_block", "px-captcha", "incapsula_resource",
    "please verify you are a human", "press & hold to confirm you are a human",
)
PRIVATE_PATTERNS = [
    r"^localhost$", r"^127\.", r"^10\.", r"^172\.(1[6-9]|2[0-9]|3[01])\.",
    r"^192\.168\.", r"^0\.", r"^169\.254\.", r"^::1$", r"^fc[0-9a-f]{2}:", r"^fd[0-9a-f]{2}:", r"^fe80:",
]


def is_bot_challenge(html: str) -> bool:
    """Detect if HTML response is a bot challenge, captcha, or access denied page."""
    if not html:
        return False
    soup = BeautifulSoup(html[:10000], "lxml")
    title_text = (soup.title.string or "").strip().lower() if soup.title else ""
    if any(bt in title_text for bt in BLOCKED_TITLES):
        return True
    return any(marker in html.lower() for marker in BOT_MARKERS)


def classify_scrape_exception(exc: Exception) -> tuple[str, str]:
    """Classify exception into (failure_reason, user_friendly_message)."""
    if isinstance(exc, ScrapeException):
        return exc.failure_reason, exc.message
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return ScrapeFailureReason.TIMEOUT, "Site timed out. The server took too long to respond."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code if exc.response is not None else 0
        if code in (401, 403):
            return ScrapeFailureReason.BLOCKED, "Access blocked. The store denied access or requires verification."
        if code == 429:
            return ScrapeFailureReason.BLOCKED, "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests."
        if code == 404:
            return ScrapeFailureReason.NOT_FOUND, "Product page not found (HTTP 404)."
        if code in (502, 503, 504):
            return ScrapeFailureReason.NETWORK_ERROR, f"Store server temporarily unavailable (HTTP {code})."
    if isinstance(exc, (httpx.NetworkError, ConnectionError, OSError)):
        return ScrapeFailureReason.NETWORK_ERROR, "Connection error. Unable to connect to the store server."

    s = str(exc).lower()
    if "timeout" in s or "timed out" in s:
        return ScrapeFailureReason.TIMEOUT, "Site timed out. The server took too long to respond."
    if any(k in s for k in ("block", "forbidden", "access denied")):
        return ScrapeFailureReason.BLOCKED, "Access blocked. The store denied access or requires verification."
    if any(k in s for k in ("connection refused", "connection reset", "network")):
        return ScrapeFailureReason.NETWORK_ERROR, "Connection error. Unable to connect to the store server."
    return ScrapeFailureReason.UNKNOWN, f"Scrape failed: {str(exc)[:150]}"


classify_error_message = classify_scrape_exception


def normalize_url(url: str) -> tuple[str | None, str | None]:
    """Normalize URL: add https:// if missing, reject http://."""
    url = url.strip()
    if not url:
        return None, "URL cannot be empty"
    if url.lower().startswith("http://"):
        return None, "HTTP is not secure. Please use the HTTPS version of this URL (replace http:// with https://)"
    if not url.startswith("https://"):
        return (f"https://{url}", None) if ("." in url and " " not in url) else (None, "Invalid URL format. Please enter a valid product URL")
    return url, None


def validate_url(url: str) -> tuple[bool, str | None]:
    """Validate URL for security (SSRF protection)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False, "Only HTTPS URLs are allowed"
        host = (parsed.hostname or "").lower()
        if any(re.match(p, host) for p in PRIVATE_PATTERNS) or host in {"169.254.169.254", "metadata.google.internal"} or any(host.endswith(s) for s in BLOCKED_DOMAIN_SUFFIXES):
            return False, "Private or internal URLs are not allowed"
        return True, None
    except Exception as e:
        return False, f"Invalid URL: {str(e)}"


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
    """Fetch URL with httpx and exponential backoff retries for transient errors."""
    from app.core.config import get_settings
    settings = get_settings()
    retries = settings.scraper_max_retries if max_retries is None else max_retries
    delay_base = settings.scraper_base_delay if base_delay is None else base_delay
    delay_max = settings.scraper_max_delay if max_delay is None else max_delay
    factor = settings.scraper_backoff_factor if backoff_factor is None else backoff_factor
    req_timeout = settings.scraper_timeout if timeout is None else timeout

    own_client = client is None
    cli = client or httpx.AsyncClient(timeout=req_timeout, follow_redirects=True, max_redirects=5)
    try:
        for attempt in range(retries + 1):
            try:
                resp = await cli.get(url, headers={"User-Agent": random.choice(USER_AGENTS)})
                if resp.status_code == 200:
                    if len(resp.content) > 5 * 1024 * 1024:
                        return FetchResult(None, 200, attempt, ScrapeFailureReason.LAYOUT_CHANGED, "Page content exceeds maximum allowed size (5MB).")
                    if is_bot_challenge(resp.text):
                        return FetchResult(resp.text, 200, attempt, ScrapeFailureReason.BLOCKED, "Access blocked. The store denied access or requires verification.")
                    return FetchResult(resp.text, 200, attempt)

                if resp.status_code == 429 or resp.status_code in (502, 503, 504):
                    if attempt < retries:
                        delay = min(delay_base * (factor ** attempt), delay_max)
                        if resp.status_code == 429 and (ra := resp.headers.get("Retry-After")) and ra.isdigit():
                            delay = min(float(ra), delay_max)
                        if jitter and delay > 0:
                            delay += random.uniform(0, min(0.5, delay * 0.1))
                        logger.warning(f"Transient HTTP {resp.status_code} fetching {url} (attempt {attempt + 1}/{retries + 1}). Retrying in {delay:.2f}s...")
                        await asyncio.sleep(delay)
                        continue
                    reason = ScrapeFailureReason.BLOCKED if resp.status_code == 429 else ScrapeFailureReason.NETWORK_ERROR
                    msg = "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests." if resp.status_code == 429 else f"Store server temporarily unavailable (HTTP {resp.status_code})."
                    return FetchResult(None, resp.status_code, attempt, reason, msg)

                if resp.status_code in (401, 403):
                    return FetchResult(resp.text, resp.status_code, attempt, ScrapeFailureReason.BLOCKED, "Access blocked. The store denied access or requires verification.")
                if resp.status_code == 404:
                    return FetchResult(None, 404, attempt, ScrapeFailureReason.NOT_FOUND, "Product page not found (HTTP 404).")
                return FetchResult(None, resp.status_code, attempt, ScrapeFailureReason.UNKNOWN, f"Store returned unexpected HTTP {resp.status_code}.")

            except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError, httpx.NetworkError, ConnectionError, OSError) as e:
                if attempt < retries:
                    delay = min(delay_base * (factor ** attempt), delay_max)
                    if jitter and delay > 0:
                        delay += random.uniform(0, min(0.5, delay * 0.1))
                    await asyncio.sleep(delay)
                    continue
                is_timeout = isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError))
                reason = ScrapeFailureReason.TIMEOUT if is_timeout else ScrapeFailureReason.NETWORK_ERROR
                msg = "Site timed out. The server took too long to respond." if is_timeout else "Connection error. Unable to connect to the store server."
                return FetchResult(None, None, attempt, reason, msg)

            except Exception as e:
                return FetchResult(None, None, attempt, ScrapeFailureReason.UNKNOWN, f"Scrape failed: {str(e)[:150]}")

        return FetchResult(None, None, retries, ScrapeFailureReason.UNKNOWN, "Retries exhausted.")
    finally:
        if own_client:
            await cli.aclose()


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
        context = browser.new_context(user_agent=user_agent)
        page = context.new_page()
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            return page.content()
        finally:
            browser.close()


async def _playwright_async(url: str, user_agent: str) -> str | None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            return await page.content()
        finally:
            await browser.close()


async def fetch_with_playwright(url: str) -> str | None:
    """Fetch using Playwright for JS-heavy sites."""
    ua = random.choice(USER_AGENTS)
    if sys.platform == "win32":
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_get_playwright_executor(), _playwright_sync, url, ua)
    return await _playwright_async(url, ua)


class ScraperEngine:
    """Class engine facade for network execution and browser lifecycle."""
    fetch_page = staticmethod(fetch_page)
    fetch_with_httpx = staticmethod(fetch_with_httpx)
    fetch_with_playwright = staticmethod(fetch_with_playwright)
    normalize_url = staticmethod(normalize_url)
    validate_url = staticmethod(validate_url)
    classify_exception = staticmethod(classify_scrape_exception)
