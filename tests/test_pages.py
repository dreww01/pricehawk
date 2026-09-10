"""
Page route tests and authentication fallback verification.
"""

import time
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import jwt
import pytest

from app.core.config import get_settings


def create_token(
    sub: str = "test-user-1234",
    email: str = "test@example.com",
    expires_in: int = 3600,
) -> str:
    """Helper to generate HS256 JWT tokens with the test secret."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, settings.sb_jwt_secret, algorithm="HS256")


@pytest.fixture
def valid_token() -> str:
    return create_token(expires_in=3600)


@pytest.fixture
def expired_token() -> str:
    return create_token(expires_in=-3600)


# ============================================================================
# Public Page Rendering
# ============================================================================

def test_login_page_renders(client):
    """Test login page returns HTML."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PriceHawk" in response.text
    # Plain login should not show rendered flash notices
    assert 'id="flash-notices"' not in response.text


def test_signup_page_renders(client):
    """Test signup page returns HTML."""
    response = client.get("/signup")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_forgot_password_page_renders(client):
    """Test forgot password page returns HTML."""
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Forgot Password" in response.text


def test_reset_password_page_renders(client):
    """Test reset password page returns HTML."""
    response = client.get("/reset-password")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ============================================================================
# Unauthenticated Navigation & Redirects with Next URL
# ============================================================================

def test_dashboard_requires_auth(client):
    """Test dashboard redirects unauthenticated users with next parameter."""
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/dashboard"


def test_tracked_requires_auth(client):
    """Test tracked page redirects unauthenticated users with next parameter."""
    response = client.get("/tracked", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/tracked"


def test_tracked_detail_requires_auth(client):
    """Test tracked product detail redirects unauthenticated users with next parameter."""
    response = client.get("/tracked/prod-abc-123", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/tracked/prod-abc-123"


def test_discover_requires_auth(client):
    """Test discover page redirects unauthenticated users with next parameter."""
    response = client.get("/discover", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/discover"


def test_insights_requires_auth(client):
    """Test insights page redirects unauthenticated users with next parameter."""
    response = client.get("/insights", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/insights"


def test_alerts_settings_requires_auth(client):
    """Test alerts settings page redirects unauthenticated users with next parameter."""
    response = client.get("/alerts/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/alerts/settings"


def test_account_settings_requires_auth(client):
    """Test account settings page redirects unauthenticated users with next parameter."""
    response = client.get("/account/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/account/settings"


def test_destination_query_string_preserved(client):
    """Test query string on protected page is preserved in next redirect parameter."""
    response = client.get("/tracked?filter=active&sort=desc", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/login?next=")
    decoded_location = unquote(location)
    assert "next=/tracked?filter=active&sort=desc" in decoded_location


# ============================================================================
# Session Expiration & Invalid Token Handling
# ============================================================================

def test_expired_session_redirects_with_notice_and_clears_cookie(client, expired_token):
    """Test expired cookie on protected page redirects to login with notice and clears cookie."""
    response = client.get(
        "/dashboard",
        cookies={"access_token": expired_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location == "/login?next=/dashboard&notice=session_expired"
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie


def test_invalid_token_redirects_with_notice_and_clears_cookie(client):
    """Test malformed/tampered cookie on protected page redirects and clears cookie."""
    response = client.get(
        "/dashboard",
        cookies={"access_token": "malformed.invalid.token"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/dashboard&notice=session_expired"
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie


def test_expired_bearer_token_redirects_on_protected_page(client, expired_token):
    """Test expired Bearer token on HTML page route redirects gracefully."""
    response = client.get(
        "/dashboard",
        headers={"Authorization": f"Bearer {expired_token}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/dashboard&notice=session_expired"


# ============================================================================
# Flash Notice Display on Login Page
# ============================================================================

def test_login_page_renders_session_expired_notice(client):
    """Test login page renders intuitive notice when notice=session_expired."""
    response = client.get("/login?notice=session_expired")
    assert response.status_code == 200
    assert "Your session has expired. Please log in again." in response.text


def test_login_page_renders_expired_param_notice(client):
    """Test login page renders notice when expired=1 query param is set."""
    response = client.get("/login?expired=1")
    assert response.status_code == 200
    assert "Your session has expired. Please log in again." in response.text


def test_login_page_renders_login_required_notice_when_next_present(client):
    """Test login page informs user login is required when next parameter is present."""
    response = client.get("/login?next=/dashboard")
    assert response.status_code == 200
    assert "Please log in to access this page." in response.text


def test_login_page_renders_explicit_login_required_notice(client):
    """Test login page informs user login is required when notice=login_required."""
    response = client.get("/login?notice=login_required")
    assert response.status_code == 200
    assert "Please log in to access this page." in response.text


def test_login_page_renders_custom_message(client):
    """Test login page displays custom message passed in query params."""
    response = client.get("/login?message=Custom+Security+Notice")
    assert response.status_code == 200
    assert "Custom Security Notice" in response.text


def test_unauthenticated_user_followed_redirect_shows_notice(client):
    """Integration: following redirect from protected page displays login required notice."""
    response = client.get("/dashboard", follow_redirects=True)
    assert response.status_code == 200
    assert "Please log in to access this page." in response.text


def test_expired_user_followed_redirect_shows_notice(client, expired_token):
    """Integration: following redirect from expired cookie displays session expired notice."""
    response = client.get("/dashboard", cookies={"access_token": expired_token}, follow_redirects=True)
    assert response.status_code == 200
    assert "Your session has expired. Please log in again." in response.text


# ============================================================================
# Authenticated Access via Cookies and Headers
# ============================================================================

def test_dashboard_accessible_with_valid_cookie(client, valid_token):
    """Test dashboard is accessible when valid session cookie is provided."""
    response = client.get("/dashboard", cookies={"access_token": valid_token})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PriceHawk" in response.text


def test_dashboard_accessible_with_valid_bearer_token(client, valid_token):
    """Test dashboard is accessible when valid Bearer header is provided."""
    response = client.get("/dashboard", headers={"Authorization": f"Bearer {valid_token}"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PriceHawk" in response.text


def test_tracked_accessible_with_valid_cookie(client, valid_token):
    """Test tracked products page is accessible with valid session cookie."""
    response = client.get("/tracked", cookies={"access_token": valid_token})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_authenticated_user_redirected_away_from_login(client, valid_token):
    """Test already-authenticated user is redirected from login to dashboard."""
    response = client.get("/login", cookies={"access_token": valid_token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_authenticated_user_redirected_to_next_destination_from_login(client, valid_token):
    """Test already-authenticated user is redirected to next path if provided."""
    response = client.get("/login?next=/tracked", cookies={"access_token": valid_token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/tracked"


def test_logout_clears_cookie(client):
    """Test logout clears access_token cookie with path=/."""
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie


# ============================================================================
# Dashboard API Endpoints with Unified Auth & Fallback
# ============================================================================

@pytest.fixture
def mock_supabase():
    mock_sb = MagicMock()
    mock_sb.table().select().eq().execute.return_value = MagicMock(count=7, data=[])
    mock_sb.table().select().eq().gte().execute.return_value = MagicMock(count=3, data=[])
    mock_sb.table().select().eq().order().limit().execute.return_value = MagicMock(data=[])
    return mock_sb


def test_dashboard_stats_api_cookie_auth(client, valid_token, mock_supabase):
    """Test dashboard stats API authenticates via access_token cookie."""
    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_supabase):
        response = client.get("/api/dashboard/stats", cookies={"access_token": valid_token})
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "competitors" in data
        assert "alerts" in data
        assert "insights" in data


def test_dashboard_stats_api_bearer_auth(client, valid_token, mock_supabase):
    """Test dashboard stats API authenticates via Bearer header."""
    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_supabase):
        response = client.get(
            "/api/dashboard/stats",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert response.status_code == 200
        assert "products" in response.json()


def test_dashboard_stats_api_undefined_bearer_falls_back_to_cookie(client, valid_token, mock_supabase):
    """Test client-side 'Bearer undefined' header gracefully falls back to cookie."""
    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_supabase):
        response = client.get(
            "/api/dashboard/stats",
            headers={"Authorization": "Bearer undefined"},
            cookies={"access_token": valid_token},
        )
        assert response.status_code == 200
        assert "products" in response.json()


def test_dashboard_stats_api_unauthenticated_returns_401(client):
    """Test dashboard stats API returns 401 JSON when unauthenticated."""
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_dashboard_stats_api_expired_returns_401(client, expired_token):
    """Test dashboard stats API returns 401 JSON when token is expired."""
    response = client.get("/api/dashboard/stats", cookies={"access_token": expired_token})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_dashboard_activity_api_cookie_auth(client, valid_token, mock_supabase):
    """Test dashboard activity API authenticates via cookie."""
    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_supabase):
        response = client.get("/api/dashboard/activity", cookies={"access_token": valid_token})
        assert response.status_code == 200
        assert "activity" in response.json()


def test_dashboard_products_api_cookie_auth(client, valid_token, mock_supabase):
    """Test dashboard products API authenticates via cookie."""
    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_supabase):
        response = client.get("/api/dashboard/products", cookies={"access_token": valid_token})
        assert response.status_code == 200
        assert "products" in response.json()


def test_insights_api_cookie_auth(client, valid_token, mock_supabase):
    """Test insights API authenticates via cookie."""
    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_supabase):
        response = client.get("/api/insights", cookies={"access_token": valid_token})
        assert response.status_code == 200
        assert "insights" in response.json()
