"""Tests for modularized schemas, line count constraints, and backward compatibility."""

from pathlib import Path
import pytest

import app.db.models as db_models
import app.schemas as schemas_pkg
import app.schemas.alert as schema_alert
import app.schemas.auth as schema_auth
import app.schemas.product as schema_product
import app.schemas.scraper as schema_scraper


def test_schema_files_under_200_lines():
    """Verify that every file under app/schemas/ stays under 200 lines."""
    schemas_dir = Path("app/schemas")
    schema_files = list(schemas_dir.glob("*.py"))
    assert len(schema_files) >= 5, "Expected at least __init__, alert, auth, product, scraper"

    for file_path in schema_files:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) < 200, f"{file_path} exceeds 200 lines: {len(lines)} lines"


def test_models_file_under_200_lines():
    """Verify app/db/models.py re-export facade stays under 200 lines."""
    with open("app/db/models.py", "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) < 200, f"app/db/models.py exceeds 200 lines: {len(lines)} lines"


def test_backward_compatibility_reexports_match_schemas():
    """Verify all __all__ exports in app.schemas are identically re-exported in app.db.models."""
    for symbol_name in schemas_pkg.__all__:
        assert hasattr(db_models, symbol_name), (
            f"Symbol {symbol_name} missing from app.db.models"
        )
        assert getattr(db_models, symbol_name) is getattr(schemas_pkg, symbol_name), (
            f"Symbol {symbol_name} in app.db.models is not identical to app.schemas.{symbol_name}"
        )


def test_auth_schema_domain_models():
    """Verify domain models in app.schemas.auth."""
    expected_auth_models = [
        "LoginRequest",
        "SignupRequest",
        "AuthResponse",
        "ForgotPasswordRequest",
        "VerifyResetOTPRequest",
        "ResetPasswordRequest",
        "ChangePasswordRequest",
        "ChangeEmailRequest",
        "VerifyEmailChangeRequest",
        "AccountSettingsResponse",
        "ChangePasswordResponse",
        "ChangeEmailResponse",
        "AccountDeletionDetails",
        "AccountDeleteResponse",
        "SignupResponse",
        "ForgotPasswordResponse",
        "VerifyResetOTPResponse",
        "ResetPasswordResponse",
        "DashboardStatsResponse",
        "DashboardActivityItem",
        "DashboardActivityResponse",
    ]
    for model_name in expected_auth_models:
        assert hasattr(schema_auth, model_name), f"Missing {model_name} in auth schemas"
        assert getattr(schema_auth, model_name) is getattr(db_models, model_name)


def test_product_schema_domain_models():
    """Verify domain models in app.schemas.product."""
    expected_product_models = [
        "MAX_PRODUCTS_LIMIT",
        "DiscoveredProductResponse",
        "StoreDiscoveryRequest",
        "StoreDiscoveryResponse",
        "TrackProductItem",
        "TrackProductsRequest",
        "TrackProductsResponse",
        "CompetitorResponse",
        "ProductUpdate",
        "ProductResponse",
        "ProductListResponse",
        "PriceHistoryResponse",
        "PriceHistoryListResponse",
        "AcceptCurrencyResponse",
        "AcceptAllCurrenciesResponse",
        "DashboardProductItem",
        "DashboardProductsResponse",
    ]
    for model_name in expected_product_models:
        assert hasattr(schema_product, model_name), f"Missing {model_name} in product schemas"
        assert getattr(schema_product, model_name) is getattr(db_models, model_name)


def test_alert_schema_domain_models():
    """Verify domain models in app.schemas.alert."""
    expected_alert_models = [
        "AlertSettingsResponse",
        "AlertSettingsUpdate",
        "DigestRunRequest",
        "DigestRunResponse",
        "PendingAlertResponse",
        "PendingAlertsListResponse",
        "AlertHistoryResponse",
        "AlertHistoryListResponse",
        "WebhookRegisterRequest",
        "WebhookConfigResponse",
        "PriceAlertEvent",
        "CheckPriceDropRequest",
        "CheckPriceDropResponse",
    ]
    for model_name in expected_alert_models:
        assert hasattr(schema_alert, model_name), f"Missing {model_name} in alert schemas"
        assert getattr(schema_alert, model_name) is getattr(db_models, model_name)


def test_scraper_schema_domain_models():
    """Verify domain models in app.schemas.scraper."""
    expected_scraper_models = [
        "ScrapeTaskResponse",
        "ScrapeProgressResponse",
        "WorkerHealthResponse",
        "InitialPriceResult",
        "ScrapeResultResponse",
        "HealthCheckResponse",
        "MessageResponse",
        "DashboardCacheMetricsResponse",
        "TestWebhookRequest",
        "TestWebhookResponse",
        "TestEmailRequest",
        "TestEmailResponse",
        "ChartDataPoint",
        "CompetitorChartData",
        "ChartDataResponse",
        "InsightResponse",
        "InsightListResponse",
        "GenerateInsightRequest",
        "DashboardInsightItem",
        "DashboardInsightsResponse",
    ]
    for model_name in expected_scraper_models:
        assert hasattr(schema_scraper, model_name), f"Missing {model_name} in scraper schemas"
        assert getattr(schema_scraper, model_name) is getattr(db_models, model_name)
