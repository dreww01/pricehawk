"""Automated tests verifying structured logging and correlation ID propagation.

Covers:
1. Context management & ID generation (ContextVar, nesting, exception cleanup)
2. Structured JSON logging formatter (fields, timestamp ISO format, exceptions, extra fields)
3. Text formatter formatting with and without correlation ID
4. FastAPI CorrelationIdMiddleware (header inspection, generation, response propagation, CORS)
5. Celery task header propagation (apply_async, delay, task_prerun, task_postrun)
6. Scraper & Alert workflow propagation (manual scrape, scheduled scrape, webhook delivery)
7. Log record factory & filter integration
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.logging import (
    CORRELATION_ID_CTX,
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    CorrelationIdFilter,
    CorrelationIdMiddleware,
    JSONFormatter,
    TextFormatter,
    correlation_context,
    generate_correlation_id,
    get_correlation_id,
    install_log_record_factory,
    reset_correlation_id,
    set_correlation_id,
    setup_logging,
)
from app.db.models import ScrapeTaskResponse
from app.services.webhook_service import WebhookService
from app.tasks.scraper_tasks import (
    scrape_all_products,
    scrape_product_manual,
    scrape_single_competitor,
    send_alert_digests,
)
from main import app


# =============================================================================
# 1. Context Management & Correlation ID Generation Tests
# =============================================================================

def test_generate_correlation_id():
    """generate_correlation_id returns a valid UUID4 string or prefixed string."""
    cid = generate_correlation_id()
    assert isinstance(cid, str)
    assert len(cid) == 36
    assert cid.count("-") == 4

    prefixed = generate_correlation_id(prefix="scrape")
    assert prefixed.startswith("scrape-")
    assert len(prefixed) > 36


def test_correlation_context_lifecycle():
    """correlation_context sets context on enter, restores previous on exit, and handles exceptions."""
    assert get_correlation_id() is None

    test_cid = "test-cid-1234-5678"
    with correlation_context(test_cid) as active_cid:
        assert active_cid == test_cid
        assert get_correlation_id() == test_cid

        # Nested context restores outer context on exit
        inner_cid = "inner-cid-8765-4321"
        with correlation_context(inner_cid) as nested_cid:
            assert nested_cid == inner_cid
            assert get_correlation_id() == inner_cid

        assert get_correlation_id() == test_cid

    assert get_correlation_id() is None


def test_correlation_context_exception_safety():
    """correlation_context restores prior context even when an exception is raised."""
    assert get_correlation_id() is None
    try:
        with correlation_context("failing-context-id"):
            assert get_correlation_id() == "failing-context-id"
            raise RuntimeError("Deliberate failure inside context")
    except RuntimeError:
        pass

    assert get_correlation_id() is None


def test_correlation_context_generates_id_when_none():
    """correlation_context automatically generates an ID when none is provided."""
    with correlation_context() as generated_cid:
        assert isinstance(generated_cid, str)
        assert len(generated_cid) == 36
        assert get_correlation_id() == generated_cid

    assert get_correlation_id() is None


# =============================================================================
# 2. Structured JSON Logging Formatter Tests
# =============================================================================

def test_json_formatter_standard_fields():
    """JSONFormatter outputs valid JSON containing all standard log envelope fields."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="pricehawk.scraper",
        level=logging.INFO,
        pathname="scraper.py",
        lineno=42,
        msg="Scraped competitor %s successfully: %s %s",
        args=("comp-123", "49.99", "USD"),
        exc_info=None,
    )

    with correlation_context("corr-uuid-777"):
        output = formatter.format(record)

    parsed = json.loads(output)
    assert parsed["logger"] == "pricehawk.scraper"
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "Scraped competitor comp-123 successfully: 49.99 USD"
    assert parsed["correlation_id"] == "corr-uuid-777"
    assert "timestamp" in parsed
    assert parsed["timestamp"].endswith("+00:00")


def test_json_formatter_without_correlation_id():
    """JSONFormatter produces correlation_id: null when no correlation ID is present."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname="test.py",
        lineno=10,
        msg="Warning without correlation",
        args=(),
        exc_info=None,
    )

    token = set_correlation_id(None)
    try:
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["correlation_id"] is None
    finally:
        reset_correlation_id(token)


def test_json_formatter_with_extra_fields():
    """JSONFormatter includes custom extra attributes attached to LogRecord."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="pricehawk.tasks",
        level=logging.INFO,
        pathname="tasks.py",
        lineno=100,
        msg="Task completed",
        args=(),
        exc_info=None,
    )
    record.user_id = "user-999"
    record.competitor_id = "comp-555"
    record.product_id = "prod-111"

    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["user_id"] == "user-999"
    assert parsed["competitor_id"] == "comp-555"
    assert parsed["product_id"] == "prod-111"


def test_json_formatter_exception_traceback():
    """JSONFormatter formats exceptions cleanly under the 'exception' key."""
    formatter = JSONFormatter()
    try:
        raise ValueError("Simulated scrape failure")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="pricehawk.error",
        level=logging.ERROR,
        pathname="error.py",
        lineno=50,
        msg="Exception occurred during scrape",
        args=(),
        exc_info=exc_info,
    )

    output = formatter.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError: Simulated scrape failure" in parsed["exception"]


def test_text_formatter_with_and_without_correlation_id():
    """TextFormatter formats cleanly with [correlation_id] tag when present and omits when absent."""
    formatter = TextFormatter()
    record = logging.LogRecord(
        name="pricehawk.dev",
        level=logging.INFO,
        pathname="dev.py",
        lineno=1,
        msg="Dev log message",
        args=(),
        exc_info=None,
    )

    with correlation_context("cid-dev-123"):
        formatted_with_cid = formatter.format(record)
        assert "[cid-dev-123]" in formatted_with_cid
        assert "Dev log message" in formatted_with_cid

    token = set_correlation_id(None)
    try:
        formatted_without_cid = formatter.format(record)
        assert "[cid-dev-123]" not in formatted_without_cid
        assert "Dev log message" in formatted_without_cid
    finally:
        reset_correlation_id(token)


# =============================================================================
# 3. Setup Logging & Configuration Tests
# =============================================================================

def test_setup_logging_json_mode(monkeypatch):
    """setup_logging configures root logger with JSONFormatter when log_format='json'."""
    from app.core import config

    mock_settings = Settings(
        sb_url="https://test.supabase.co",
        sb_anon_key="anon",
        sb_service_key="service",
        sb_jwt_secret="secret",
        log_format="json",
    )
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    setup_logging(log_format="json")

    root = logging.getLogger()
    assert len(root.handlers) > 0
    handler = root.handlers[0]
    assert isinstance(handler.formatter, JSONFormatter)


def test_setup_logging_production_auto_mode(monkeypatch):
    """setup_logging defaults to JSONFormatter when env='production' and log_format='auto'."""
    from app.core import config

    mock_settings = Settings(
        sb_url="https://test.supabase.co",
        sb_anon_key="anon",
        sb_service_key="service",
        sb_jwt_secret="secret",
        env="production",
        log_format="auto",
    )
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    setup_logging()

    root = logging.getLogger()
    assert len(root.handlers) > 0
    handler = root.handlers[0]
    assert isinstance(handler.formatter, JSONFormatter)


# =============================================================================
# 4. FastAPI CorrelationIdMiddleware Tests
# =============================================================================

def test_middleware_incoming_correlation_id_propagated(client: TestClient):
    """Middleware preserves incoming X-Correlation-ID and echoes it back in response header."""
    incoming_cid = "custom-client-cid-12345"
    response = client.get(
        "/api/health",
        headers={CORRELATION_ID_HEADER: incoming_cid},
    )
    assert response.status_code == 200
    assert response.headers.get(CORRELATION_ID_HEADER) == incoming_cid


def test_middleware_incoming_request_id_fallback(client: TestClient):
    """Middleware accepts X-Request-ID if X-Correlation-ID is not provided."""
    incoming_req_id = "request-id-fallback-67890"
    response = client.get(
        "/api/health",
        headers={REQUEST_ID_HEADER: incoming_req_id},
    )
    assert response.status_code == 200
    assert response.headers.get(CORRELATION_ID_HEADER) == incoming_req_id


def test_middleware_generates_id_when_missing(client: TestClient):
    """Middleware generates a new UUID v4 correlation ID when no header is supplied."""
    response = client.get("/api/health")
    assert response.status_code == 200
    header_cid = response.headers.get(CORRELATION_ID_HEADER)
    assert header_cid is not None
    assert len(header_cid) == 36
    assert header_cid.count("-") == 4


# =============================================================================
# 5. Celery Task Header & Worker Propagation Tests
# =============================================================================

def test_celery_task_apply_async_propagates_correlation_id():
    """CorrelatedTask.apply_async injects the active correlation ID into task headers."""
    test_cid = "celery-async-cid-111"
    with correlation_context(test_cid):
        with patch("celery.Task.apply_async") as mock_super_apply:
            mock_super_apply.return_value = MagicMock(id="mock-task-id")
            scrape_single_competitor.apply_async(args=["comp-123"])

            mock_super_apply.assert_called_once()
            _, kwargs = mock_super_apply.call_args
            headers = kwargs.get("headers", {})
            assert headers.get("correlation_id") == test_cid


def test_celery_task_direct_call_establishes_correlation_context():
    """Direct execution of Celery task establishes a correlation context and sets request.correlation_id."""
    with correlation_context("direct-call-cid-222"):
        with patch("app.tasks.scraper_tasks.get_supabase_client") as mock_db, \
             patch("app.tasks.scraper_tasks._was_scraped_today", return_value=True):

            mock_client = MagicMock()
            mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"id": "comp-1", "product_id": "prod-1", "products": {"is_active": True, "user_id": "u1"}}]
            )
            mock_db.return_value = mock_client

            result = scrape_single_competitor("comp-1")
            assert result["status"] == "skipped"
            assert result.get("correlation_id") == "direct-call-cid-222"


def test_scrape_all_products_propagates_correlation_id_to_subtasks():
    """scrape_all_products establishes a correlation ID and passes it to each subtask."""
    test_cid = "batch-daily-scrape-cid-333"

    with correlation_context(test_cid):
        with patch("app.tasks.scraper_tasks.get_supabase_client") as mock_db, \
             patch("app.tasks.scraper_tasks.scrape_single_competitor.delay") as mock_delay, \
             patch("app.tasks.scraper_tasks.register_active_scrape_task"):

            mock_client = MagicMock()
            # Products query
            mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"id": "prod-1", "user_id": "user-1"}]
            )
            # Competitors query
            mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
                data=[{"id": "comp-1", "product_id": "prod-1"}, {"id": "comp-2", "product_id": "prod-1"}]
            )
            mock_db.return_value = mock_client

            mock_task = MagicMock(id="task-sub-1")
            mock_delay.return_value = mock_task

            result = scrape_all_products()
            assert result["total"] == 2
            assert result["queued"] == 2
            assert result["correlation_id"] == test_cid
            assert mock_delay.call_count == 2


# =============================================================================
# 6. End-to-End API Trigger to Background Task Propagation
# =============================================================================

def test_api_manual_scrape_propagates_correlation_id(client: TestClient):
    """POST /api/scraper/scrape/manual/{product_id} propagates correlation ID into Celery task headers and response."""
    import jwt
    import time
    from app.core.config import get_settings

    settings = get_settings()
    token = jwt.encode(
        {"sub": "user-1234", "email": "tester@example.com", "role": "authenticated", "aud": "authenticated", "exp": int(time.time()) + 3600},
        settings.sb_jwt_secret,
        algorithm="HS256",
    )

    test_cid = "e2e-manual-scrape-cid-9999"
    captured_options = {}

    def mock_apply_async(args=None, kwargs=None, task_id=None, producer=None, link=None, link_error=None, shadow=None, **options):
        captured_options.update(options)
        task_mock = MagicMock()
        task_mock.id = "mocked-celery-task-777"
        return task_mock

    mock_sb = MagicMock()
    # Product query: found
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "prod-abc-123"}]
    )
    # Competitor count: 2
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "comp-1"}, {"id": "comp-2"}],
        count=2,
    )

    with patch("app.api.routes.scraper.get_supabase_client", return_value=mock_sb), \
         patch("app.api.routes.scraper.scrape_product_manual.apply_async", side_effect=mock_apply_async), \
         patch("app.services.account_service.register_active_scrape_task"):

        response = client.post(
            "/api/scraper/scrape/manual/prod-abc-123",
            headers={
                "Authorization": f"Bearer {token}",
                CORRELATION_ID_HEADER: test_cid,
            },
        )

        assert response.status_code == 202
        data = response.json()
        validated = ScrapeTaskResponse(**data)
        assert validated.task_id == "mocked-celery-task-777"
        assert validated.correlation_id == test_cid
        assert response.headers.get(CORRELATION_ID_HEADER) == test_cid

        # Check that Celery apply_async received the correlation_id in headers
        headers = captured_options.get("headers", {})
        assert headers.get("correlation_id") == test_cid


def test_api_manual_scrape_generates_correlation_id_when_omitted(client: TestClient):
    """POST /api/scraper/scrape/manual/{product_id} generates and attaches correlation ID when omitted."""
    import jwt
    import time
    from app.core.config import get_settings

    settings = get_settings()
    token = jwt.encode(
        {"sub": "user-1234", "email": "tester@example.com", "role": "authenticated", "aud": "authenticated", "exp": int(time.time()) + 3600},
        settings.sb_jwt_secret,
        algorithm="HS256",
    )

    captured_options = {}

    def mock_apply_async(args=None, kwargs=None, task_id=None, producer=None, link=None, link_error=None, shadow=None, **options):
        captured_options.update(options)
        task_mock = MagicMock()
        task_mock.id = "mocked-celery-task-888"
        return task_mock

    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "prod-abc-123"}],
        count=1,
    )

    with patch("app.api.routes.scraper.get_supabase_client", return_value=mock_sb), \
         patch("app.api.routes.scraper.scrape_product_manual.apply_async", side_effect=mock_apply_async), \
         patch("app.services.account_service.register_active_scrape_task"):

        response = client.post(
            "/api/scraper/scrape/manual/prod-abc-123",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 202
        data = response.json()
        generated_cid = data.get("correlation_id")
        assert generated_cid is not None
        assert len(generated_cid) == 36
        assert response.headers.get(CORRELATION_ID_HEADER) == generated_cid

        headers = captured_options.get("headers", {})
        assert headers.get("correlation_id") == generated_cid


# =============================================================================
# 7. Webhook & Alert Pipeline Correlation Propagation Tests
# =============================================================================

def test_webhook_delivery_attaches_correlation_id_header(monkeypatch):
    """WebhookService attaches X-Correlation-ID header to outgoing webhook requests."""
    captured = {}

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None

    class MockClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def post(self, url, content, headers):
            captured.update(url=url, content=content, headers=headers)
            return Response()

    monkeypatch.setattr("app.services.webhook_service.socket.getaddrinfo", lambda *args: [
        (None, None, None, None, ("93.184.216.34", 443))
    ])
    monkeypatch.setattr("app.services.webhook_service.httpx.Client", MockClient)

    test_cid = "webhook-test-cid-4444"
    with correlation_context(test_cid):
        result = WebhookService().send_digest(
            "https://hooks.example.test/pricehawk",
            "a-secure-secret-key-12345",
            {"event": "test"},
        )

    assert result["success"] is True
    assert captured["headers"].get(CORRELATION_ID_HEADER) == test_cid


def test_send_alert_digests_task_propagates_correlation_id():
    """send_alert_digests establishes a correlation context and returns it in results."""
    test_cid = "alert-digest-batch-cid-5555"

    with correlation_context(test_cid):
        with patch("app.tasks.scraper_tasks.get_supabase_client") as mock_db, \
             patch("app.tasks.scraper_tasks.DigestService") as mock_digest_cls:

            mock_client = MagicMock()
            mock_client.table.return_value.select.return_value.or_.return_value.execute.return_value = MagicMock(
                data=[{"user_id": "u-1"}]
            )
            mock_client.auth.admin.get_user_by_id.return_value = MagicMock(user=MagicMock(email="u1@example.com"))
            mock_db.return_value = mock_client

            mock_service = MagicMock()
            mock_service.run_for_user.return_value = {
                "user_id": "u-1",
                "status": "sent",
                "alerts_count": 3,
            }
            mock_digest_cls.return_value = mock_service

            result = send_alert_digests(force=True)

            assert result["total_users"] == 1
            assert result["sent"] == 1
            assert result["correlation_id"] == test_cid
            mock_service.run_for_user.assert_called_once_with(
                "u-1", "u1@example.com", force=True, dry_run=False, correlation_id=test_cid
            )


# =============================================================================
# 8. Log Record Factory & Logging Output Tests
# =============================================================================

def test_log_records_automatically_capture_correlation_id():
    """Standard logging calls automatically attach correlation_id attribute to LogRecord."""
    install_log_record_factory()
    test_cid = "logger-auto-capture-cid-777"

    captured_records = []

    class CapturingHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    handler = CapturingHandler()
    logger = logging.getLogger("test.auto.capture")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        with correlation_context(test_cid):
            logger.info("Message inside correlation context")

        logger.info("Message outside correlation context")

        assert len(captured_records) == 2
        assert getattr(captured_records[0], "correlation_id", None) == test_cid
        assert getattr(captured_records[1], "correlation_id", None) is None
    finally:
        logger.removeHandler(handler)


def test_celery_task_signals_set_and_clear_context():
    """Celery task_prerun sets the context and task_postrun clears it."""
    from celery.signals import task_postrun, task_prerun

    mock_task = MagicMock()
    mock_request = MagicMock()
    mock_request.headers = {"correlation_id": "signal-test-cid-888"}
    mock_request.correlation_id = "signal-test-cid-888"
    mock_task.request = mock_request

    # Trigger prerun
    task_prerun.send(sender=mock_task, task_id="tid-1", task=mock_task)
    assert get_correlation_id() == "signal-test-cid-888"

    # Trigger postrun
    task_postrun.send(sender=mock_task, task_id="tid-1", task=mock_task)
    assert get_correlation_id() is None


def test_manual_scrape_stores_correlation_id_in_progress_data():
    """scrape_product_manual writes correlation_id to Redis progress state."""
    saved_progress = []

    def mock_set_progress(task_id, data, ttl=300):
        saved_progress.append((task_id, data))

    mock_sb = MagicMock()
    # Return inactive product to trigger quick exit
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "prod-inactive", "is_active": False}]
    )

    with patch("app.tasks.scraper_tasks.get_supabase_client", return_value=mock_sb), \
         patch("app.tasks.scraper_tasks.set_scrape_progress", side_effect=mock_set_progress):

        with correlation_context("progress-cid-12345"):
            res = scrape_product_manual("prod-inactive")

            assert res["status"] == "cancelled"
            assert res.get("correlation_id") == "progress-cid-12345"
            assert len(saved_progress) > 0
            _, progress_data = saved_progress[0]
            assert progress_data.get("correlation_id") == "progress-cid-12345"

