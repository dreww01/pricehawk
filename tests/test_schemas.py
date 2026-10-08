"""Tests for modular Pydantic schemas and backward-compatible exports."""

from decimal import Decimal
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.db.models as db_models
import app.schemas as schemas
import app.schemas.alert as alert_schemas
import app.schemas.auth as auth_schemas
import app.schemas.product as product_schemas
import app.schemas.scraper as scraper_schemas


def test_schema_file_line_limits():
    """Verify all schema files and db.models stay strictly under 200 lines."""
    schemas_dir = Path("app/schemas")
    assert schemas_dir.is_dir(), "app/schemas package must exist"

    schema_files = list(schemas_dir.glob("*.py"))
    assert len(schema_files) >= 5, "Must include __init__, auth, product, alert, scraper"

    for file_path in schema_files:
        line_count = len(file_path.read_text(encoding="utf-8").splitlines())
        assert line_count < 200, f"{file_path} exceeds 200 lines: {line_count}"

    models_path = Path("app/db/models.py")
    models_line_count = len(models_path.read_text(encoding="utf-8").splitlines())
    assert models_line_count < 200, f"{models_path} exceeds 200 lines: {models_line_count}"


def test_module_docstrings():
    """Verify all schema modules have proper docstrings following Google style."""
    for mod in [schemas, auth_schemas, product_schemas, alert_schemas, scraper_schemas, db_models]:
        assert mod.__doc__, f"Module {mod.__name__} missing docstring"
        assert len(mod.__doc__.strip()) > 0


def test_backward_compatible_identity():
    """Verify that models imported from app.db.models match those in app.schemas."""
    # Auth schemas
    assert db_models.AccountSettingsResponse is auth_schemas.AccountSettingsResponse
    assert db_models.AccountSettingsResponse is schemas.AccountSettingsResponse
    assert db_models.SignupResponse is auth_schemas.SignupResponse
    assert db_models.ForgotPasswordResponse is auth_schemas.ForgotPasswordResponse
    assert db_models.VerifyResetOTPResponse is auth_schemas.VerifyResetOTPResponse
    assert db_models.ResetPasswordResponse is auth_schemas.ResetPasswordResponse
    assert db_models.AccountDeleteResponse is auth_schemas.AccountDeleteResponse
    assert db_models.AccountDeletionDetails is auth_schemas.AccountDeletionDetails

    # Product schemas
    assert db_models.ProductResponse is product_schemas.ProductResponse
    assert db_models.ProductResponse is schemas.ProductResponse
    assert db_models.ProductUpdate is product_schemas.ProductUpdate
    assert db_models.ProductListResponse is product_schemas.ProductListResponse
    assert db_models.CompetitorResponse is product_schemas.CompetitorResponse
    assert db_models.PriceHistoryResponse is product_schemas.PriceHistoryResponse
    assert db_models.PriceHistoryListResponse is product_schemas.PriceHistoryListResponse
    assert db_models.InsightResponse is product_schemas.InsightResponse
    assert db_models.ChartDataResponse is product_schemas.ChartDataResponse

    # Alert schemas
    assert db_models.AlertSettingsResponse is alert_schemas.AlertSettingsResponse
    assert db_models.AlertSettingsResponse is schemas.AlertSettingsResponse
    assert db_models.AlertSettingsUpdate is alert_schemas.AlertSettingsUpdate
    assert db_models.PendingAlertResponse is alert_schemas.PendingAlertResponse
    assert db_models.AlertHistoryResponse is alert_schemas.AlertHistoryResponse
    assert db_models.PriceAlertEvent is alert_schemas.PriceAlertEvent
    assert db_models.WebhookRegisterRequest is alert_schemas.WebhookRegisterRequest

    # Scraper schemas
    assert db_models.ScrapeTaskResponse is scraper_schemas.ScrapeTaskResponse
    assert db_models.ScrapeTaskResponse is schemas.ScrapeTaskResponse
    assert db_models.ScrapeProgressResponse is scraper_schemas.ScrapeProgressResponse
    assert db_models.ScrapeResultResponse is scraper_schemas.ScrapeResultResponse
    assert db_models.WorkerHealthResponse is scraper_schemas.WorkerHealthResponse
    assert db_models.StoreDiscoveryRequest is scraper_schemas.StoreDiscoveryRequest
    assert db_models.DiscoveredProductResponse is scraper_schemas.DiscoveredProductResponse
    assert db_models.MAX_PRODUCTS_LIMIT == 5000


def test_product_update_sanitization():
    """Verify ProductUpdate sanitizes HTML entities."""
    update = product_schemas.ProductUpdate(product_name="<script>alert(1)</script>")
    assert update.product_name == "&lt;script&gt;alert(1)&lt;/script&gt;"

    update_none = product_schemas.ProductUpdate(product_name=None)
    assert update_none.product_name is None


def test_store_discovery_request_validation():
    """Verify StoreDiscoveryRequest URL validation and normalization."""
    # Rejects plain http
    with pytest.raises(ValidationError):
        scraper_schemas.StoreDiscoveryRequest(url="http://example.com/store")

    # Rejects invalid format
    with pytest.raises(ValidationError):
        scraper_schemas.StoreDiscoveryRequest(url="invalid format with spaces")

    # Normalizes domain to https
    req = scraper_schemas.StoreDiscoveryRequest(url="example.com")
    assert req.url == "https://example.com"

    # Preserves valid https URL
    req_https = scraper_schemas.StoreDiscoveryRequest(url="https://mystore.com/catalog")
    assert req_https.url == "https://mystore.com/catalog"


def test_webhook_url_validation():
    """Verify WebhookRegisterRequest and AlertSettingsUpdate enforce HTTPS."""
    # WebhookRegisterRequest requires HTTPS
    with pytest.raises(ValidationError):
        alert_schemas.WebhookRegisterRequest(webhook_url="http://insecure.com/hook")

    valid_hook = alert_schemas.WebhookRegisterRequest(webhook_url="https://secure.com/hook")
    assert valid_hook.webhook_url == "https://secure.com/hook"

    # AlertSettingsUpdate allows None or HTTPS
    update_none = alert_schemas.AlertSettingsUpdate(webhook_url=None)
    assert update_none.webhook_url is None

    # AlertSettingsUpdate normalizes whitespace-only and empty strings to None
    update_whitespace = alert_schemas.AlertSettingsUpdate(webhook_url="   ")
    assert update_whitespace.webhook_url is None

    with pytest.raises(ValidationError):
        alert_schemas.AlertSettingsUpdate(webhook_url="http://insecure.com/hook")

    update_valid = alert_schemas.AlertSettingsUpdate(webhook_url="https://secure.com/hook")
    assert update_valid.webhook_url == "https://secure.com/hook"


def test_check_price_drop_request_validation():
    """Verify CheckPriceDropRequest boundary rules."""
    # Price must be positive gt 0
    with pytest.raises(ValidationError):
        alert_schemas.CheckPriceDropRequest(price=Decimal("0"))

    with pytest.raises(ValidationError):
        alert_schemas.CheckPriceDropRequest(price=Decimal("-10.00"))

    # Valid request
    req = alert_schemas.CheckPriceDropRequest(price=Decimal("49.99"), currency="USD", threshold_percent=Decimal("15"))
    assert req.price == Decimal("49.99")
    assert req.currency == "USD"
    assert req.threshold_percent == Decimal("15")


def test_account_deletion_details_extra_fields():
    """Verify AccountDeletionDetails allows extra audit fields."""
    details = auth_schemas.AccountDeletionDetails(
        user_id="usr_test_123",
        products_found=2,
        custom_audit_metric=99,
    )
    assert details.user_id == "usr_test_123"
    assert details.products_found == 2
    assert getattr(details, "custom_audit_metric") == 99


def test_error_envelope_re_export():
    """Verify error envelope and error code are properly re-exported."""
    assert db_models.ErrorEnvelope is schemas.ErrorEnvelope
    assert db_models.ErrorCode is schemas.ErrorCode
    assert db_models.STANDARD_ERROR_RESPONSES is schemas.STANDARD_ERROR_RESPONSES
    assert db_models.create_error_response is schemas.create_error_response


def test_digest_run_response_required_fields():
    """Verify DigestRunResponse requires price_drops, price_increases, and currency_changes."""
    with pytest.raises(ValidationError):
        alert_schemas.DigestRunResponse(
            user_id="usr_123",
            status="sent",
            alerts_count=3,
        )

