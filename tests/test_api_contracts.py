"""
Comprehensive tests for standardized API response contracts, error envelopes,
OpenAPI schema documentation, and backward compatibility across all modernized endpoints.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.errors import ErrorCode, ErrorEnvelope
from app.core.security import CurrentUser, get_current_user
from app.db.database import get_supabase_client, get_user_supabase_client
from app.db.models import (
    AcceptAllCurrenciesResponse,
    AcceptCurrencyResponse,
    AccountDeleteResponse,
    AccountSettingsResponse,
    ChangeEmailResponse,
    ChangePasswordResponse,
    DashboardActivityResponse,
    DashboardCacheMetricsResponse,
    DashboardInsightsResponse,
    DashboardProductsResponse,
    DashboardStatsResponse,
    ForgotPasswordResponse,
    HealthCheckResponse,
    ResetPasswordResponse,
    SignupResponse,
    TestEmailResponse,
    VerifyResetOTPResponse,
)
from main import app
from tests.test_auth import _create_test_token


# ============================================================================
# 1. Health & Status Endpoints
# ============================================================================

def test_health_check_contract(client: TestClient):
    """GET /api/health returns standardized HealthCheckResponse."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    validated = HealthCheckResponse(**data)
    assert validated.status == "healthy"


# ============================================================================
# 2. Account Management Endpoints
# ============================================================================

def test_account_settings_contract(client: TestClient):
    """GET /api/account/settings returns standardized AccountSettingsResponse."""
    token = _create_test_token()
    response = client.get(
        "/api/account/settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    validated = AccountSettingsResponse(**data)
    assert validated.user_id == "test-user-1234"
    assert validated.email == "test@example.com"


def test_change_password_success_contract(client: TestClient):
    """POST /api/account/change-password returns standardized ChangePasswordResponse."""
    token = _create_test_token()
    mock_client = MagicMock()
    mock_client.auth.update_user.return_value = {}

    with patch("app.api.routes.account.get_supabase_client_with_session", return_value=mock_client):
        response = client.post(
            "/api/account/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "OldPassword123", "new_password": "NewPassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = ChangePasswordResponse(**data)
        assert validated.message == "Password updated successfully"


def test_change_password_weak_error_envelope(client: TestClient):
    """POST /api/account/change-password returns ErrorEnvelope on weak password."""
    token = _create_test_token()
    mock_client = MagicMock()
    mock_client.auth.update_user.side_effect = Exception("weak password: min 6 chars")

    with patch("app.api.routes.account.get_supabase_client_with_session", return_value=mock_client):
        response = client.post(
            "/api/account/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "OldPassword123", "new_password": "123"},
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert data["error_code"] == ErrorCode.BAD_REQUEST.value
        assert "Password does not meet requirements" in data["message"]


def test_change_email_success_contract(client: TestClient):
    """POST /api/account/change-email returns standardized ChangeEmailResponse."""
    token = _create_test_token()
    mock_client = MagicMock()
    mock_client.auth.update_user.return_value = {}

    with patch("app.api.routes.account.get_supabase_client_with_session", return_value=mock_client):
        response = client.post(
            "/api/account/change-email",
            headers={"Authorization": f"Bearer {token}"},
            json={"new_email": "new-user@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = ChangeEmailResponse(**data)
        assert "Verification email sent" in validated.message


def test_change_email_conflict_error_envelope(client: TestClient):
    """POST /api/account/change-email returns CONFLICT ErrorEnvelope if already in use."""
    token = _create_test_token()
    mock_client = MagicMock()
    mock_client.auth.update_user.side_effect = Exception("email already registered")

    with patch("app.api.routes.account.get_supabase_client_with_session", return_value=mock_client):
        response = client.post(
            "/api/account/change-email",
            headers={"Authorization": f"Bearer {token}"},
            json={"new_email": "existing@example.com"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == ErrorCode.CONFLICT.value
        assert "already in use" in data["detail"]
        assert "already in use" in data["message"]


def test_account_delete_contract(client: TestClient):
    """DELETE /api/account/delete returns standardized AccountDeleteResponse."""
    token = _create_test_token()
    cleanup_mock = {
        "user_id": "test-user-1234",
        "products_found": 2,
        "competitors_found": 4,
        "tasks_revoked": 1,
        "tables_cleaned": ["products", "competitors"],
    }

    with patch("app.api.routes.account.delete_user_account", return_value=cleanup_mock):
        response = client.delete(
            "/api/account/delete",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = AccountDeleteResponse(**data)
        assert "Account data deleted successfully" in validated.message
        assert validated.details.products_found == 2
        assert validated.details.competitors_found == 4


# ============================================================================
# 3. Authentication & Password Reset Endpoints
# ============================================================================

def test_signup_success_contract(client: TestClient):
    """POST /api/auth/signup returns standardized SignupResponse."""
    mock_client = MagicMock()
    mock_user = MagicMock()
    mock_user.id = "new-user-1"
    mock_user.email = "brandnew@example.com"
    mock_user.email_confirmed_at = None

    mock_resp = MagicMock()
    mock_resp.user = mock_user
    mock_client.auth.sign_up.return_value = mock_resp

    with patch("app.api.routes.auth.get_supabase_client", return_value=mock_client):
        response = client.post(
            "/api/auth/signup",
            json={"email": "brandnew@example.com", "password": "securepassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = SignupResponse(**data)
        assert validated.message == "Account created successfully"
        assert validated.user_id == "new-user-1"
        assert validated.email == "brandnew@example.com"
        assert validated.email_confirmed is False


def test_forgot_password_success_contract(client: TestClient):
    """POST /api/auth/forgot-password returns standardized ForgotPasswordResponse."""
    mock_client = MagicMock()
    mock_client.auth.reset_password_email.return_value = {}

    with patch("app.api.routes.auth.get_supabase_client", return_value=mock_client):
        response = client.post(
            "/api/auth/forgot-password",
            json={"email": "forgot@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = ForgotPasswordResponse(**data)
        assert "reset code has been sent" in validated.message


def test_verify_reset_otp_success_contract(client: TestClient):
    """POST /api/auth/verify-reset-otp returns standardized VerifyResetOTPResponse."""
    mock_client = MagicMock()
    mock_session = MagicMock()
    mock_session.access_token = "token-xyz-789"
    mock_resp = MagicMock()
    mock_resp.session = mock_session
    mock_client.auth.verify_otp.return_value = mock_resp

    with patch("app.api.routes.auth.get_supabase_client", return_value=mock_client):
        response = client.post(
            "/api/auth/verify-reset-otp",
            json={"email": "verify@example.com", "otp": "123456"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = VerifyResetOTPResponse(**data)
        assert validated.message == "Code verified successfully"
        assert validated.reset_token == "token-xyz-789"


def test_verify_reset_otp_error_envelope(client: TestClient):
    """POST /api/auth/verify-reset-otp returns ErrorEnvelope on invalid OTP."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.session = None
    mock_client.auth.verify_otp.return_value = mock_resp

    with patch("app.api.routes.auth.get_supabase_client", return_value=mock_client):
        response = client.post(
            "/api/auth/verify-reset-otp",
            json={"email": "verify@example.com", "otp": "000000"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == ErrorCode.BAD_REQUEST.value
        assert "Invalid or expired code" in data["detail"]
        assert "Invalid or expired code" in data["message"]


def test_reset_password_success_contract(client: TestClient):
    """POST /api/auth/reset-password returns standardized ResetPasswordResponse."""
    mock_client = MagicMock()
    mock_client.auth.update_user.return_value = {}

    with patch("app.db.database.get_supabase_client_with_session", return_value=mock_client):
        response = client.post(
            "/api/auth/reset-password",
            json={"reset_token": "valid-token", "new_password": "NewValidPassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = ResetPasswordResponse(**data)
        assert "Password has been reset successfully" in validated.message


# ============================================================================
# 4. Alert Testing & Currency Acceptance Endpoints
# ============================================================================

def test_send_test_email_contract(client: TestClient):
    """POST /api/alerts/test returns standardized TestEmailResponse."""
    token = _create_test_token()
    mock_email_service = MagicMock()
    mock_email_service.send_test_email.return_value = {"success": True}

    with patch("app.api.routes.alerts.EmailService", return_value=mock_email_service):
        response = client.post(
            "/api/alerts/test",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": "custom@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = TestEmailResponse(**data)
        assert validated.success is True
        assert validated.message == "Test email sent successfully"
        assert validated.email == "custom@example.com"


def test_accept_currency_contract(client: TestClient):
    """PATCH /api/alerts/competitors/{competitor_id}/accept-currency returns AcceptCurrencyResponse."""
    token = _create_test_token()
    mock_sb = MagicMock()
    mock_comp = {
        "id": "comp-123",
        "product_id": "prod-1",
        "products": {"user_id": "test-user-1234"},
    }
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[mock_comp]
    )
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "comp-123"}]
    )
    mock_sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "alt-1"}]
    )

    app.dependency_overrides[get_user_supabase_client] = lambda: mock_sb
    try:
        with patch("app.api.routes.alerts.get_supabase_client", return_value=mock_sb):
            response = client.patch(
                "/api/alerts/competitors/comp-123/accept-currency",
                headers={"Authorization": f"Bearer {token}"},
                json={"currency": "EUR"},
            )
            assert response.status_code == 200
            data = response.json()
            validated = AcceptCurrencyResponse(**data)
            assert validated.success is True
            assert validated.competitor_id == "comp-123"
            assert validated.new_currency == "EUR"
            assert "EUR" in validated.message
    finally:
        app.dependency_overrides.pop(get_user_supabase_client, None)


def test_accept_all_currencies_contract(client: TestClient):
    """POST /api/alerts/accept-all-currencies returns AcceptAllCurrenciesResponse."""
    token = _create_test_token()
    mock_sb = MagicMock()
    mock_alerts = [
        {"id": "alt-1", "competitor_id": "comp-1", "new_currency": "GBP"},
        {"id": "alt-2", "competitor_id": "comp-2", "new_currency": "CAD"},
    ]
    # Chain for select pending alerts
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=mock_alerts
    )
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "updated"}]
    )

    app.dependency_overrides[get_user_supabase_client] = lambda: mock_sb
    try:
        with patch("app.api.routes.alerts.get_supabase_client", return_value=mock_sb):
            response = client.post(
                "/api/alerts/accept-all-currencies",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            data = response.json()
            validated = AcceptAllCurrenciesResponse(**data)
            assert validated.success is True
            assert validated.updated_count == 2
            assert "Accepted 2 currency changes" in validated.message
    finally:
        app.dependency_overrides.pop(get_user_supabase_client, None)


# ============================================================================
# 5. Dashboard Helper Endpoints Contracts
# ============================================================================

def test_dashboard_stats_contract(client: TestClient):
    """GET /api/dashboard/stats returns standardized DashboardStatsResponse."""
    token = _create_test_token()
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        count=5, data=[]
    )
    mock_sb.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(
        count=2, data=[]
    )

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb), \
         patch("app.api.routes.pages.get_dashboard_cache") as mock_cache:
        mock_cache.return_value.get_stats_data.return_value = None
        response = client.get(
            "/api/dashboard/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        validated = DashboardStatsResponse(**data)
        assert isinstance(validated.products, int)
        assert isinstance(validated.competitors, int)
        assert isinstance(validated.alerts, int)
        assert isinstance(validated.insights, int)


def test_dashboard_cache_metrics_contract(client: TestClient):
    """GET /api/dashboard/cache/metrics returns standardized DashboardCacheMetricsResponse."""
    token = _create_test_token()
    response = client.get(
        "/api/dashboard/cache/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    validated = DashboardCacheMetricsResponse(**data)
    assert validated.hits >= 0
    assert validated.misses >= 0
    assert validated.total_requests >= 0


# ============================================================================
# 6. Error Envelope Structure & ErrorCode Mapping Tests
# ============================================================================

def test_error_envelope_403_unauthenticated(client: TestClient):
    """Missing bearer token returns ErrorEnvelope with FORBIDDEN and Not authenticated."""
    response = client.get("/api/auth/me")
    assert response.status_code == 403
    data = response.json()
    assert "detail" in data
    assert data["error_code"] == ErrorCode.FORBIDDEN.value
    assert data["message"] == "Not authenticated"


def test_error_envelope_401_expired_token(client: TestClient):
    """Expired bearer token returns 401 ErrorEnvelope with SESSION_EXPIRED."""
    import time
    import jwt
    from app.core.config import get_settings

    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": "test-user-1234",
        "email": "test@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now - 7200,
        "exp": now - 3600,
    }
    expired_token = jwt.encode(payload, settings.sb_jwt_secret, algorithm="HS256")

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert data["error_code"] == ErrorCode.SESSION_EXPIRED.value
    assert "expired" in data["message"].lower()


def test_error_envelope_404_not_found(client: TestClient):
    """404 response contains ErrorEnvelope with NOT_FOUND."""
    token = _create_test_token()
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )

    with patch("app.api.routes.products.get_supabase_client", return_value=mock_sb):
        response = client.get(
            "/api/products/non-existent-id",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Product not found"
        assert data["error_code"] == ErrorCode.NOT_FOUND.value
        assert data["message"] == "Product not found"


def test_error_envelope_422_validation_error(client: TestClient):
    """422 validation failure contains ErrorEnvelope with VALIDATION_ERROR and readable message."""
    response = client.post("/api/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    data = response.json()
    assert isinstance(data["detail"], list)
    assert data["error_code"] == ErrorCode.VALIDATION_ERROR.value
    assert "Validation failed" in data["message"]


def test_error_envelope_500_unhandled_exception():
    """500 unhandled exception returns ErrorEnvelope with INTERNAL_ERROR and error_id."""
    no_raise_client = TestClient(app, raise_server_exceptions=False)
    token = _create_test_token()
    with patch("app.api.routes.system.check_database", side_effect=Exception("Database panic!")):
        response = no_raise_client.get(
            "/api/system/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error_code"] == ErrorCode.INTERNAL_ERROR.value
        assert "unexpected error" in data["message"].lower()
        assert "error_id" in data
        assert len(data["error_id"]) > 0


# ============================================================================
# 7. OpenAPI Documentation Enhancement Verification
# ============================================================================

def test_openapi_contains_all_modernized_schemas(client: TestClient):
    """Verify OpenAPI document registers all modernized response contracts and ErrorCode enum."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    schemas = schema.get("components", {}).get("schemas", {})

    expected_schemas = [
        "HealthCheckResponse",
        "AccountSettingsResponse",
        "ChangePasswordResponse",
        "ChangeEmailResponse",
        "AccountDeleteResponse",
        "AccountDeletionDetails",
        "SignupResponse",
        "ForgotPasswordResponse",
        "VerifyResetOTPResponse",
        "ResetPasswordResponse",
        "TestEmailResponse",
        "AcceptCurrencyResponse",
        "AcceptAllCurrenciesResponse",
        "DashboardStatsResponse",
        "DashboardActivityResponse",
        "DashboardProductsResponse",
        "DashboardCacheMetricsResponse",
        "DashboardInsightsResponse",
        "ErrorEnvelope",
        "ErrorCode",
    ]

    for name in expected_schemas:
        assert name in schemas, f"Schema {name} is missing from OpenAPI schema!"

    # Validate ErrorCode enum options
    error_code_schema = schemas["ErrorCode"]
    assert "enum" in error_code_schema
    assert "VALIDATION_ERROR" in error_code_schema["enum"]
    assert "NOT_AUTHENTICATED" in error_code_schema["enum"]
    assert "SESSION_EXPIRED" in error_code_schema["enum"]
    assert "FORBIDDEN" in error_code_schema["enum"]
    assert "BAD_REQUEST" in error_code_schema["enum"]
    assert "NOT_FOUND" in error_code_schema["enum"]
    assert "CONFLICT" in error_code_schema["enum"]
    assert "RATE_LIMIT_EXCEEDED" in error_code_schema["enum"]
    assert "INTERNAL_ERROR" in error_code_schema["enum"]
