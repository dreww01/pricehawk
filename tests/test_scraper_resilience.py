"""
Hermetic regression tests for scraper resilience:
1. Automatic exponential backoff retries for transient errors and timeouts
2. Structured user-friendly failure statuses (timeout, blocked, layout_changed, not_found, etc.)
3. Partial failure isolation across competitor products
4. Graceful error status propagation to price history and alert detection
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.scraper_service import (
    FetchResult,
    ScrapeFailureReason,
    ScrapeResult,
    classify_scrape_exception,
    fetch_page,
    is_bot_challenge,
    scrape_and_check_alerts,
    scrape_url,
)
from app.tasks.scraper_tasks import scrape_product_manual, scrape_single_competitor


SAMPLE_PRICE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Store - Cool Widget</title></head>
<body>
    <div class="product-single">
        <h1 class="product-title">Cool Widget</h1>
        <div class="price">
            <span class="sale-price">$49.99</span>
        </div>
    </div>
</body>
</html>
"""

LAYOUT_CHANGED_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Store - Cool Widget</title></head>
<body>
    <div class="product-container">
        <h1>Cool Widget</h1>
        <p>Available at local retail partners only.</p>
    </div>
</body>
</html>
"""

CLOUDFLARE_BLOCKED_HTML = """
<!DOCTYPE html>
<html>
<head><title>Just a moment...</title></head>
<body>
    <div class="cf-browser-verification">
        <span>Checking your browser before accessing the store.</span>
    </div>
</body>
</html>
"""

ACCESS_DENIED_HTML = """
<!DOCTYPE html>
<html>
<head><title>Access Denied</title></head>
<body>
    <h1>403 Forbidden</h1>
    <p>You do not have permission to access this resource.</p>
</body>
</html>
"""


# ===========================================================================
# 1. Automatic Exponential Backoff Retries Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_exponential_backoff_transient_timeout_recovery():
    """Transient timeout on first 2 calls recovers on 3rd call with exponential backoff."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ReadTimeout("Server read timeout", request=request)
        return httpx.Response(200, text=SAMPLE_PRICE_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=3,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    assert call_count == 3
    assert result.status == "success"
    assert result.price == Decimal("49.99")
    assert result.currency == "USD"
    assert result.retry_count == 2
    assert result.error_message is None
    assert result.failure_reason is None


@pytest.mark.asyncio
async def test_exponential_backoff_persistent_timeout_failure():
    """Persistent timeout exhausts retries and returns user-friendly timeout status."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        raise httpx.ReadTimeout("Read timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=2,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    # 1 initial + 2 retries = 3 calls
    assert call_count == 3
    assert result.status == "failed"
    assert result.price is None
    assert result.failure_reason == ScrapeFailureReason.TIMEOUT
    assert result.failure_status == "timeout"
    assert "Site timed out" in result.error_message
    assert result.retry_count == 2


@pytest.mark.asyncio
async def test_exponential_backoff_transient_network_error_recovery():
    """Transient connection error on 1st call recovers on 2nd call."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused by host", request=request)
        return httpx.Response(200, text=SAMPLE_PRICE_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=3,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    assert call_count == 2
    assert result.status == "success"
    assert result.price == Decimal("49.99")
    assert result.retry_count == 1


@pytest.mark.asyncio
async def test_exponential_backoff_persistent_network_error_failure():
    """Persistent network error exhausts retries and reports clean connection error."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("Name resolution failed", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=2,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    assert call_count == 3
    assert result.status == "failed"
    assert result.failure_reason == ScrapeFailureReason.NETWORK_ERROR
    assert "Connection error" in result.error_message
    assert result.retry_count == 2


@pytest.mark.asyncio
async def test_exponential_backoff_rate_limit_retry_and_recovery():
    """HTTP 429 rate limit is treated as transient and retried with backoff."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="Rate limited", request=request)
        return httpx.Response(200, text=SAMPLE_PRICE_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=2,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    assert call_count == 2
    assert result.status == "success"
    assert result.price == Decimal("49.99")
    assert result.retry_count == 1


@pytest.mark.asyncio
async def test_exponential_backoff_persistent_rate_limit_failure():
    """Persistent HTTP 429 exhausts retries and reports clear rate limit status."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, text="Rate limited", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=2,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    assert call_count == 3
    assert result.status == "failed"
    assert result.failure_reason == ScrapeFailureReason.BLOCKED
    assert "429" in result.error_message
    assert result.retry_count == 2


@pytest.mark.asyncio
async def test_exponential_backoff_transient_503_retry_and_recovery():
    """Transient 503 Service Unavailable retries with backoff and succeeds."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, text="Service Unavailable", request=request)
        return httpx.Response(200, text=SAMPLE_PRICE_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape_url(
            "https://store.example.com/products/widget",
            max_retries=2,
            base_delay=0.001,
            crawl_delay=False,
            client=client,
        )

    assert call_count == 2
    assert result.status == "success"
    assert result.price == Decimal("49.99")


@pytest.mark.asyncio
async def test_exponential_backoff_delay_timing_progression():
    """Verify exponential backoff progression formula: base * factor^attempt."""
    sleep_calls: list[float] = []

    async def mock_sleep(delay: float):
        sleep_calls.append(delay)

    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        raise httpx.ReadTimeout("Timeout", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("asyncio.sleep", side_effect=mock_sleep):
            await fetch_page(
                "https://store.example.com/products/widget",
                max_retries=3,
                base_delay=1.0,
                max_delay=10.0,
                backoff_factor=2.0,
                jitter=False,
                client=client,
            )

    # 3 retries -> 3 sleeps with delays: 1.0 * 2^0 = 1.0, 1.0 * 2^1 = 2.0, 1.0 * 2^2 = 4.0
    assert len(sleep_calls) == 3
    assert pytest.approx(sleep_calls[0], 0.01) == 1.0
    assert pytest.approx(sleep_calls[1], 0.01) == 2.0
    assert pytest.approx(sleep_calls[2], 0.01) == 4.0


# ===========================================================================
# 2. Structured Failure Statuses & Error Reporting Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_failure_status_blocked_access_403():
    """HTTP 403 Forbidden is recognized immediately as blocked access."""
    def handler(request: httpx.Request):
        return httpx.Response(403, text=ACCESS_DENIED_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("app.services.scraper_service.fetch_with_playwright", return_value=None):
            result = await scrape_url(
                "https://store.example.com/products/widget",
                max_retries=2,
                base_delay=0.001,
                crawl_delay=False,
                client=client,
            )

    assert result.status == "failed"
    assert result.failure_reason == ScrapeFailureReason.BLOCKED
    assert "Access blocked" in result.error_message


@pytest.mark.asyncio
async def test_failure_status_bot_challenge_detection():
    """Cloudflare / bot challenge HTML signature is categorized as blocked."""
    def handler(request: httpx.Request):
        return httpx.Response(200, text=CLOUDFLARE_BLOCKED_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("app.services.scraper_service.fetch_with_playwright", return_value=None):
            result = await scrape_url(
                "https://store.example.com/products/widget",
                max_retries=1,
                base_delay=0.001,
                crawl_delay=False,
                client=client,
            )

    assert result.status == "failed"
    assert result.failure_reason == ScrapeFailureReason.BLOCKED
    assert "Access blocked" in result.error_message


@pytest.mark.asyncio
async def test_failure_status_layout_changed():
    """HTTP 200 returned HTML without any price matching selectors is categorized as layout_changed."""
    def handler(request: httpx.Request):
        return httpx.Response(200, text=LAYOUT_CHANGED_HTML, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("app.services.scraper_service.fetch_with_playwright", return_value=None):
            result = await scrape_url(
                "https://store.example.com/products/widget",
                max_retries=1,
                base_delay=0.001,
                crawl_delay=False,
                client=client,
            )

    assert result.status == "failed"
    assert result.failure_reason == ScrapeFailureReason.LAYOUT_CHANGED
    assert "Page layout changed" in result.error_message


@pytest.mark.asyncio
async def test_failure_status_not_found_404():
    """HTTP 404 is categorized as not_found without running slow browser fallback."""
    pw_mock = AsyncMock()

    def handler(request: httpx.Request):
        return httpx.Response(404, text="Not Found", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("app.services.scraper_service.fetch_with_playwright", pw_mock):
            result = await scrape_url(
                "https://store.example.com/products/widget",
                max_retries=1,
                base_delay=0.001,
                crawl_delay=False,
                client=client,
            )

    assert result.status == "failed"
    assert result.failure_reason == ScrapeFailureReason.NOT_FOUND
    assert "404" in result.error_message
    # Playwright should NOT be invoked for 404
    pw_mock.assert_not_called()


@pytest.mark.asyncio
async def test_failure_status_invalid_url_schemes_and_ssrf():
    """Invalid schemes and private IP ranges fail fast with invalid_url."""
    result_http = await scrape_url("http://insecure-store.com/item")
    assert result_http.status == "failed"
    assert result_http.failure_reason == ScrapeFailureReason.INVALID_URL
    assert "HTTP is not secure" in result_http.error_message

    result_ssrf = await scrape_url("https://127.0.0.1/admin/price")
    assert result_ssrf.status == "failed"
    assert result_ssrf.failure_reason == ScrapeFailureReason.INVALID_URL
    assert "Private or internal" in result_ssrf.error_message


def test_classify_scrape_exception_helper():
    """Verify exception classification mapping produces user-friendly descriptions."""
    reason, msg = classify_scrape_exception(httpx.ReadTimeout("Timeout occurred"))
    assert reason == ScrapeFailureReason.TIMEOUT
    assert "Site timed out" in msg

    reason, msg = classify_scrape_exception(httpx.ConnectError("Connection refused"))
    assert reason == ScrapeFailureReason.NETWORK_ERROR
    assert "Connection error" in msg

    req = httpx.Request("GET", "https://store.com")
    resp_403 = httpx.Response(403, request=req)
    reason, msg = classify_scrape_exception(httpx.HTTPStatusError("403", request=req, response=resp_403))
    assert reason == ScrapeFailureReason.BLOCKED
    assert "Access blocked" in msg

    resp_429 = httpx.Response(429, request=req)
    reason, msg = classify_scrape_exception(httpx.HTTPStatusError("429", request=req, response=resp_429))
    assert reason == ScrapeFailureReason.BLOCKED
    assert "429" in msg


# ===========================================================================
# 3. Partial Failure Isolation Tests (Competitor Runs Do Not Abort)
# ===========================================================================

def test_partial_failure_isolation_in_manual_scrape():
    """Partial failure on one competitor does not abort scraping other competitors."""
    product_id = "prod-test-uuid-123"

    # 3 Competitors:
    # 1. Times out
    # 2. Succeeds ($39.99)
    # 3. Blocked (403)
    competitors = [
        {"id": "comp-1", "url": "https://store-timeout.com/item", "retailer_name": "Timeout Store"},
        {"id": "comp-2", "url": "https://store-ok.com/item", "retailer_name": "Success Store"},
        {"id": "comp-3", "url": "https://store-blocked.com/item", "retailer_name": "Blocked Store"},
    ]

    mock_db = MagicMock()
    # Mock competitors query
    mock_comp_query = MagicMock()
    mock_comp_query.select.return_value = mock_comp_query
    mock_comp_query.eq.return_value = mock_comp_query
    mock_comp_query.execute.return_value = MagicMock(data=competitors, count=3)

    # Mock price_history insert
    inserted_records: list[dict] = []
    mock_ph_query = MagicMock()
    def fake_insert(data):
        inserted_records.append(data)
        return MagicMock(execute=MagicMock())
    mock_ph_query.insert.side_effect = fake_insert

    def table_router(name):
        if name == "competitors":
            return mock_comp_query
        elif name == "price_history":
            return mock_ph_query
        return MagicMock()

    mock_db.table.side_effect = table_router

    # Mock scrape_url results for each competitor URL
    async def fake_scrape_url(url, **kwargs):
        if "timeout" in url:
            return ScrapeResult(
                price=None,
                currency="USD",
                status="failed",
                error_message="Site timed out. The server took too long to respond.",
                failure_reason=ScrapeFailureReason.TIMEOUT,
                retry_count=3,
            )
        elif "ok" in url:
            return ScrapeResult(
                price=Decimal("39.99"),
                currency="USD",
                status="success",
                retry_count=0,
            )
        elif "blocked" in url:
            return ScrapeResult(
                price=None,
                currency="USD",
                status="failed",
                error_message="Access blocked. The store denied access or requires verification.",
                failure_reason=ScrapeFailureReason.BLOCKED,
                retry_count=0,
            )
        return ScrapeResult(price=None, currency="USD", status="failed")

    with patch("app.tasks.scraper_tasks.get_supabase_client", return_value=mock_db), \
         patch("app.tasks.scraper_tasks.scrape_url", side_effect=fake_scrape_url), \
         patch("app.tasks.scraper_tasks.set_scrape_progress") as mock_set_progress:

        result = scrape_product_manual(product_id)

    # 1. Overall task completed
    assert result["status"] == "completed"
    results = result["results"]
    assert len(results) == 3

    # 2. Competitor 1 failed gracefully with timeout
    assert results[0]["competitor_id"] == "comp-1"
    assert results[0]["status"] == "failed"
    assert results[0]["failure_reason"] == "timeout"
    assert "timed out" in results[0]["error_message"].lower()

    # 3. Competitor 2 succeeded despite competitor 1's failure
    assert results[1]["competitor_id"] == "comp-2"
    assert results[1]["status"] == "success"
    assert results[1]["price"] == "39.99"
    assert results[1]["error_message"] is None

    # 4. Competitor 3 failed gracefully with blocked status
    assert results[2]["competitor_id"] == "comp-3"
    assert results[2]["status"] == "failed"
    assert results[2]["failure_reason"] == "blocked"
    assert "blocked" in results[2]["error_message"].lower()

    # 5. Verify price_history persistence
    assert len(inserted_records) == 3
    # Schema check constraints: scrape_status IN ('success', 'failed')
    for rec in inserted_records:
        assert rec["scrape_status"] in ("success", "failed")
    assert inserted_records[0]["scrape_status"] == "failed"
    assert inserted_records[1]["scrape_status"] == "success"
    assert inserted_records[2]["scrape_status"] == "failed"


def test_partial_failure_with_unexpected_runtime_exception():
    """An unexpected exception during one competitor scrape does not crash the loop."""
    competitors = [
        {"id": "comp-crash", "url": "https://crash.com/item", "retailer_name": "Crash Store"},
        {"id": "comp-ok", "url": "https://ok.com/item", "retailer_name": "OK Store"},
    ]

    mock_db = MagicMock()
    mock_comp = MagicMock()
    mock_comp.select.return_value = mock_comp
    mock_comp.eq.return_value = mock_comp
    mock_comp.execute.return_value = MagicMock(data=competitors)

    mock_ph = MagicMock()
    mock_ph.insert.return_value = mock_ph
    mock_ph.execute.return_value = MagicMock()

    mock_db.table.side_effect = lambda name: mock_comp if name == "competitors" else mock_ph

    call_index = 0
    async def buggy_scrape(url, **kwargs):
        nonlocal call_index
        call_index += 1
        if "crash" in url:
            raise RuntimeError("Unexpected internal crash in parser library")
        return ScrapeResult(price=Decimal("15.50"), currency="USD", status="success")

    with patch("app.tasks.scraper_tasks.get_supabase_client", return_value=mock_db), \
         patch("app.tasks.scraper_tasks.scrape_url", side_effect=buggy_scrape), \
         patch("app.tasks.scraper_tasks.set_scrape_progress"):

        result = scrape_product_manual("prod-uuid")

    assert result["status"] == "completed"
    assert len(result["results"]) == 2
    assert result["results"][0]["status"] == "failed"
    assert "crash" in result["results"][0]["error_message"].lower() or "scrape failed" in result["results"][0]["error_message"].lower()
    # Second competitor still completed successfully
    assert result["results"][1]["status"] == "success"
    assert result["results"][1]["price"] == "15.50"


# ===========================================================================
# 4. Graceful Error Propagation in scrape_and_check_alerts
# ===========================================================================

@pytest.mark.asyncio
async def test_scrape_and_check_alerts_timeout_graceful_handling():
    """scrape_and_check_alerts gracefully records timeout failure without raising."""
    mock_db = MagicMock()
    mock_comp = MagicMock()
    mock_comp.select.return_value = mock_comp
    mock_comp.eq.return_value = mock_comp
    mock_comp.single.return_value = mock_comp
    mock_comp.execute.return_value = MagicMock(data={"id": "comp-1", "url": "https://timeout-store.com/item"})

    inserted: list[dict] = []
    mock_ph = MagicMock()
    def fake_insert(data):
        inserted.append(data)
        return MagicMock(execute=MagicMock())
    mock_ph.insert.side_effect = fake_insert

    mock_db.table.side_effect = lambda name: mock_comp if name == "competitors" else mock_ph

    timeout_res = ScrapeResult(
        price=None,
        currency="USD",
        status="failed",
        error_message="Site timed out. The server took too long to respond.",
        failure_reason=ScrapeFailureReason.TIMEOUT,
        retry_count=3,
    )

    with patch("app.db.database.get_supabase_client", return_value=mock_db), \
         patch("app.services.scraper_service.scrape_url", AsyncMock(return_value=timeout_res)):

        result = await scrape_and_check_alerts("comp-1")

    assert result["scrape_result"]["status"] == "failed"
    assert result["scrape_result"]["failure_reason"] == "timeout"
    assert "timed out" in result["scrape_result"]["error"].lower()
    assert result["alert_result"] is None
    assert len(inserted) == 1
    assert inserted[0]["scrape_status"] == "failed"


@pytest.mark.asyncio
async def test_scrape_and_check_alerts_competitor_not_found():
    """scrape_and_check_alerts returns clean error when competitor does not exist."""
    mock_db = MagicMock()
    mock_comp = MagicMock()
    mock_comp.select.return_value = mock_comp
    mock_comp.eq.return_value = mock_comp
    mock_comp.single.return_value = mock_comp
    mock_comp.execute.return_value = MagicMock(data=None)
    mock_db.table.return_value = mock_comp

    with patch("app.db.database.get_supabase_client", return_value=mock_db):
        result = await scrape_and_check_alerts("nonexistent-id")

    assert result["scrape_result"]["status"] == "failed"
    assert result["scrape_result"]["error"] == "Competitor not found"
    assert result["alert_result"] is None
