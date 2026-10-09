"""Price scraping service facade orchestrating engine, parser, and alerts."""
import asyncio
import logging
import random
from decimal import Decimal
from typing import Any

import httpx

from app.core.logging import correlation_context, get_correlation_id
from app.services.scraper.engine import (
    BLOCKED_DOMAIN_SUFFIXES,
    USER_AGENTS,
    ScraperEngine,
    classify_error_message,
    classify_scrape_exception,
    fetch_page,
    fetch_with_httpx,
    fetch_with_playwright,
    is_bot_challenge,
    normalize_url,
    validate_url,
)
from app.services.scraper.models import (
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
)
from app.services.scraper.parser import (
    PRICE_SELECTORS,
    ScraperParser,
    extract_price_from_html,
    parse_price,
)
from app.services.scraper.detector import (
    PlatformDetector,
    detect_platform,
    detect_platform_details,
    detect_platform_from_html,
)

logger = logging.getLogger(__name__)


def get_retailer(url: str) -> str:
    """Extract retailer name from URL (legacy, returns 'unknown' for platform detection)."""
    return "unknown"


async def scrape_url(
    url: str,
    max_retries: int | None = None,
    base_delay: float | None = None,
    crawl_delay: bool | None = None,
    client: httpx.AsyncClient | None = None,
    correlation_id: str | None = None,
) -> ScrapeResult:
    """Scrape price from URL with automatic backoff retries and structured reporting."""
    if correlation_id:
        with correlation_context(correlation_id):
            return await _scrape_url_impl(url, max_retries, base_delay, crawl_delay, client)
    return await _scrape_url_impl(url, max_retries, base_delay, crawl_delay, client)


async def _scrape_url_impl(
    url: str,
    max_retries: int | None = None,
    base_delay: float | None = None,
    crawl_delay: bool | None = None,
    client: httpx.AsyncClient | None = None,
) -> ScrapeResult:
    normalized_url, norm_error = normalize_url(url)
    if norm_error:
        return ScrapeResult(None, "USD", "failed", norm_error, ScrapeFailureReason.INVALID_URL)

    url = normalized_url
    is_valid, error = validate_url(url)
    if not is_valid:
        return ScrapeResult(None, "USD", "failed", error, ScrapeFailureReason.INVALID_URL)

    retailer = get_retailer(url)
    from app.core.config import get_settings
    apply_crawl = crawl_delay if crawl_delay is not None else (get_settings().env != "test")
    if apply_crawl:
        await asyncio.sleep(random.uniform(2, 5))

    fetch_res = await fetch_page(url, max_retries=max_retries, base_delay=base_delay, client=client)
    retries_used = fetch_res.retry_count

    if fetch_res.html and fetch_res.failure_reason != ScrapeFailureReason.BLOCKED:
        price, currency = extract_price_from_html(fetch_res.html, retailer)
        if price:
            return ScrapeResult(price, currency, "success", retry_count=retries_used)

    if fetch_res.status_code == 404:
        return ScrapeResult(None, "USD", "failed", fetch_res.error_message or "Product page not found (HTTP 404).", ScrapeFailureReason.NOT_FOUND, retries_used)
    if fetch_res.failure_reason == ScrapeFailureReason.TIMEOUT:
        return ScrapeResult(None, "USD", "failed", fetch_res.error_message or "Site timed out. The server took too long to respond.", ScrapeFailureReason.TIMEOUT, retries_used)
    if fetch_res.failure_reason == ScrapeFailureReason.NETWORK_ERROR:
        return ScrapeResult(None, "USD", "failed", fetch_res.error_message or "Connection error. Unable to connect to the store server.", ScrapeFailureReason.NETWORK_ERROR, retries_used)
    if fetch_res.status_code == 429:
        return ScrapeResult(None, "USD", "failed", fetch_res.error_message or "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests.", ScrapeFailureReason.BLOCKED, retries_used)

    try:
        pw_html = await fetch_with_playwright(url)
        if pw_html:
            if is_bot_challenge(pw_html):
                return ScrapeResult(None, "USD", "failed", "Access blocked. The store denied access or requires verification.", ScrapeFailureReason.BLOCKED, retries_used)
            price, currency = extract_price_from_html(pw_html, retailer)
            if price:
                return ScrapeResult(price, currency, "success", retry_count=retries_used)
    except Exception as pw_err:
        logger.debug(f"Playwright fallback failed for {url}: {pw_err}")

    if fetch_res.failure_reason == ScrapeFailureReason.BLOCKED:
        return ScrapeResult(None, "USD", "failed", fetch_res.error_message or "Access blocked. The store denied access or requires verification.", ScrapeFailureReason.BLOCKED, retries_used)
    if fetch_res.status_code == 200:
        return ScrapeResult(None, "USD", "failed", "Page layout changed or unsupported. Price could not be located on the product page.", ScrapeFailureReason.LAYOUT_CHANGED, retries_used)

    return ScrapeResult(None, "USD", "failed", fetch_res.error_message or "Could not extract price. This may not be a supported e-commerce store, or the product page structure is not recognized.", fetch_res.failure_reason or ScrapeFailureReason.UNKNOWN, retries_used)


async def scrape_and_check_alerts(competitor_id: str, correlation_id: str | None = None) -> dict[str, Any]:
    """Scrape a competitor URL, store the price, and check for alert triggers."""
    if correlation_id:
        with correlation_context(correlation_id):
            return await _scrape_and_check_alerts_impl(competitor_id)
    return await _scrape_and_check_alerts_impl(competitor_id)


async def _scrape_and_check_alerts_impl(competitor_id: str) -> dict[str, Any]:
    from app.db.database import get_supabase_client
    from app.services.alert_service import AlertService
    from app.services.account_service import is_product_deleted, is_user_deleted

    sb = get_supabase_client()
    cid = get_correlation_id()
    try:
        comp_response = sb.table("competitors").select("id, url, product_id, products(id, user_id, is_active)").eq("id", competitor_id).single().execute()
    except Exception as e:
        logger.error(f"Failed to query competitor {competitor_id}: {e}")
        return {"scrape_result": {"status": "failed", "price": None, "currency": "USD", "error": f"Failed to retrieve competitor: {str(e)[:150]}", "failure_reason": ScrapeFailureReason.UNKNOWN, "retry_count": 0}, "alert_result": None}

    if not comp_response.data:
        return {"scrape_result": {"status": "failed", "price": None, "currency": "USD", "error": "Competitor not found", "failure_reason": ScrapeFailureReason.NOT_FOUND, "retry_count": 0}, "alert_result": None}

    comp_record = comp_response.data
    prod_id = comp_record.get("product_id") if isinstance(comp_record, dict) else None
    if prod_id and is_product_deleted(prod_id):
        return {"scrape_result": {"status": "cancelled", "price": None, "currency": "USD", "error": "Product deleted", "failure_reason": ScrapeFailureReason.NOT_FOUND, "retry_count": 0}, "alert_result": None}

    prod_info = comp_record.get("products") if isinstance(comp_record, dict) else None
    if isinstance(prod_info, dict):
        if prod_info.get("is_active") is False:
            return {"scrape_result": {"status": "cancelled", "price": None, "currency": "USD", "error": "Product inactive", "failure_reason": ScrapeFailureReason.NOT_FOUND, "retry_count": 0}, "alert_result": None}
        owner_id = prod_info.get("user_id")
        if owner_id and is_user_deleted(owner_id):
            return {"scrape_result": {"status": "cancelled", "price": None, "currency": "USD", "error": "Account deleted", "failure_reason": ScrapeFailureReason.NOT_FOUND, "retry_count": 0}, "alert_result": None}

    url = comp_record["url"]
    try:
        scrape_result = await scrape_url(url, correlation_id=cid)
    except Exception as e:
        logger.error(f"Unexpected exception in scrape_url for {url}: {e}")
        failure_reason, friendly_msg = classify_scrape_exception(e)
        scrape_result = ScrapeResult(None, "USD", "failed", friendly_msg, failure_reason, 0)

    db_persistence_error: Exception | None = None
    price_data = {
        "competitor_id": competitor_id,
        "price": float(scrape_result.price) if scrape_result.price else None,
        "currency": scrape_result.currency,
        "scrape_status": scrape_result.status,
        "error_message": scrape_result.error_message,
    }
    for db_attempt in range(3):
        try:
            sb.table("price_history").insert(price_data).execute()
            db_persistence_error = None
            break
        except Exception as e:
            db_persistence_error = e
            if db_attempt < 2:
                await asyncio.sleep(min(0.05 * (2 ** db_attempt), 0.5))

    if not db_persistence_error:
        try:
            from app.services.dashboard_cache import invalidate_dashboard_cache_for_competitor
            invalidate_dashboard_cache_for_competitor(competitor_id, client=sb)
        except Exception:
            pass

    alert_result = None
    if not db_persistence_error and scrape_result.status == "success" and scrape_result.price:
        try:
            alert_result = await AlertService().check_price_change_and_alert(
                competitor_id=competitor_id, new_price=scrape_result.price,
                currency=scrape_result.currency, correlation_id=cid,
            )
        except Exception as e:
            logger.error(f"Alert evaluation failed for competitor {competitor_id}: {e}")
        finally:
            try:
                from app.services.dashboard_cache import invalidate_dashboard_cache_for_competitor
                invalidate_dashboard_cache_for_competitor(competitor_id, client=sb)
            except Exception:
                pass

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
            "correlation_id": cid,
        }

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
        "correlation_id": cid,
    }


class ScraperService:
    """Public facade delegating scraping, extraction, and alert orchestration."""
    scrape_url = staticmethod(scrape_url)
    scrape_and_check_alerts = staticmethod(scrape_and_check_alerts)
    fetch_page = staticmethod(fetch_page)
    fetch_with_httpx = staticmethod(fetch_with_httpx)
    fetch_with_playwright = staticmethod(fetch_with_playwright)
    extract_price = staticmethod(extract_price_from_html)
    parse_price = staticmethod(parse_price)
    detect_platform = staticmethod(detect_platform)
