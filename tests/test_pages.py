"""
Page route tests and authentication fallback verification.
"""

import html
import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlsplit

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import get_safe_redirect_url


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


def test_destination_query_string_preserved(client, valid_token):
    """Test query string on protected page is preserved in next redirect parameter."""
    target = "/tracked?filter=active&sort=desc"
    response = client.get(target, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/login?next=")

    # Verify next is parsed as a single query parameter value with full path and query string
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    assert params["next"] == [target]
    assert "sort" not in params

    # Follow redirect to login page and verify full destination is preserved in form data-next
    login_response = client.get(location)
    assert login_response.status_code == 200
    assert f'data-next="{target}"' in html.unescape(login_response.text)

    # Verify following redirect directly also preserves data-next
    followed_response = client.get(target, follow_redirects=True)
    assert followed_response.status_code == 200
    assert f'data-next="{target}"' in html.unescape(followed_response.text)

    # Verify post-login redirect preserves complete destination
    redirect_response = client.get(location, cookies={"access_token": valid_token}, follow_redirects=False)
    assert redirect_response.status_code == 303
    assert redirect_response.headers["location"] == target


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


def test_expired_session_with_query_params_preserves_full_destination(client, expired_token, valid_token):
    """Test expired cookie on protected URL with query parameters preserves full query string in next."""
    target = "/tracked?filter=active&sort=desc&page=2"
    response = client.get(
        target,
        cookies={"access_token": expired_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert "notice=session_expired" in location

    # Verify next is parsed as a single query parameter value
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    assert params["next"] == [target]
    assert params["notice"] == ["session_expired"]
    assert "sort" not in params
    assert "page" not in params

    # Follow redirect to login page and confirm data-next preserves full path and query parameters
    login_response = client.get(location)
    assert login_response.status_code == 200
    assert f'data-next="{target}"' in html.unescape(login_response.text)

    # Confirm post-login redirect preserves complete destination
    redirect_response = client.get(location, cookies={"access_token": valid_token}, follow_redirects=False)
    assert redirect_response.status_code == 303
    assert redirect_response.headers["location"] == target


@pytest.mark.asyncio
async def test_global_401_handler_preserves_multiple_query_params():
    """Verify global browser 401 exception handler encodes destination with multiple parameters as single next value."""
    from starlette.requests import Request
    from fastapi import HTTPException
    from main import unauthorized_handler

    target = "/tracked?filter=active&sort=desc&page=2"
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/tracked",
        "query_string": b"filter=active&sort=desc&page=2",
        "headers": [(b"accept", b"text/html")],
    }
    request = Request(scope)
    exc = HTTPException(status_code=401, detail="Token expired")
    response = await unauthorized_handler(request, exc)

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    assert params["next"] == [target]
    assert params["notice"] == ["session_expired"]
    assert "sort" not in params
    assert "page" not in params


def test_dashboard_template_contains_query_preserving_auth_error_handler(client, valid_token):
    """Verify dashboard script preserves query params in client-side 401 redirect."""
    response = client.get("/dashboard", cookies={"access_token": valid_token})
    assert response.status_code == 200
    assert "getSafeCurrentDestination" in response.text
    assert "window.location.pathname + (window.location.search || '')" in response.text


def test_insights_template_contains_query_preserving_auth_error_handler(client, valid_token):
    """Verify insights script preserves query params in client-side 401 redirect."""
    response = client.get("/insights", cookies={"access_token": valid_token})
    assert response.status_code == 200
    assert "getSafeCurrentDestination" in response.text
    assert "window.location.pathname + (window.location.search || '')" in response.text


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
    """Test already-authenticated user is redirected to next path if provided and safe."""
    response = client.get("/login?next=/tracked", cookies={"access_token": valid_token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/tracked"


@pytest.mark.parametrize(
    "valid_path",
    [
        "/dashboard",
        "/tracked",
        "/tracked?filter=active&sort=desc",
        "/tracked/prod-abc-123",
        "/insights",
        "/discover",
        "/alerts/settings",
        "/account/settings",
    ],
)
def test_safe_redirect_validator_accepts_valid_local_paths(valid_path):
    """Confirm validator accepts valid local application paths."""
    assert get_safe_redirect_url(valid_path) == valid_path


@pytest.mark.parametrize(
    "unsafe_payload",
    [
        "/\\evil.example",
        "/\\",
        "\\evil.example",
        "/dashboard\\evil",
        "/%5cevil.example",
        "/%5Cevil.example",
        "/%255cevil.example",
        "/dashboard%5cevil",
        "/dashboard%255cevil",
        "/dashboard\r\nevil",
        "/dashboard\x00evil",
        "/dashboard%0d%0a",
        "http://evil.example",
        "https://evil.example/path",
        "//evil.example",
        "///evil.example",
        "javascript:alert(1)",
        "/api/auth/me",
        "/api/dashboard/stats",
        "/static/css/style.css",
        "/login",
        "/logout",
        "/signup",
        "/forgot-password",
        "/reset-password",
        "/dashboard/../api/auth/me",
        "",
        None,
    ],
)
def test_safe_redirect_validator_rejects_unsafe_destinations(unsafe_payload):
    """Confirm validator rejects backslashes, schemes, and sensitive destinations."""
    assert get_safe_redirect_url(unsafe_payload, default="/dashboard") == "/dashboard"


@pytest.mark.parametrize(
    "unsafe_next",
    [
        "/\\evil.example",
        "/%5cevil.example",
        "/%5Cevil.example",
        "//evil.example",
        "https://evil.example",
        "/api/auth/me",
        "/logout",
    ],
)
def test_authenticated_user_unsafe_next_redirects_to_dashboard(client, valid_token, unsafe_next):
    """Test authenticated user with unsafe next parameter is redirected to /dashboard."""
    response = client.get(f"/login?next={unsafe_next}", cookies={"access_token": valid_token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


@pytest.mark.parametrize(
    "unsafe_next",
    [
        "/\\evil.example",
        "/%5cevil.example",
        "/%5Cevil.example",
        "//evil.example",
        "https://evil.example",
    ],
)
def test_login_page_renders_safe_data_next_for_client_redirect(client, unsafe_next):
    """Test login page never embeds unsafe next destinations in form dataset."""
    response = client.get(f"/login?next={unsafe_next}")
    assert response.status_code == 200
    # form data-next should be empty or default, not the unsafe target
    assert f'data-next="{unsafe_next}"' not in response.text
    assert 'data-next=""' in response.text


def test_login_page_renders_valid_data_next_for_client_redirect(client):
    """Test login page embeds valid local next destination in form dataset."""
    response = client.get("/login?next=/tracked?filter=active")
    assert response.status_code == 200
    assert 'data-next="/tracked?filter=active"' in response.text


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


# ============================================================================
# Comprehensive Unified Session Handling & Protected Page Redirection Tests
# ============================================================================

ALL_PROTECTED_PAGES = [
    "/dashboard",
    "/tracked",
    "/tracked/prod-123",
    "/discover",
    "/insights",
    "/alerts/settings",
    "/account/settings",
    "/settings",
]


@pytest.mark.parametrize("page_path", ALL_PROTECTED_PAGES)
def test_protected_pages_accessible_with_cookie(client, valid_token, page_path):
    """Test all protected pages seamlessly recognize authentication from cookies."""
    response = client.get(page_path, cookies={"access_token": valid_token}, follow_redirects=False)
    if page_path == "/settings":
        assert response.status_code in (302, 303)
        assert response.headers["location"] == "/account/settings"
    else:
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


@pytest.mark.parametrize("page_path", ALL_PROTECTED_PAGES)
def test_protected_pages_accessible_with_bearer_header(client, valid_token, page_path):
    """Test all protected pages seamlessly recognize authentication from Authorization headers."""
    response = client.get(page_path, headers={"Authorization": f"Bearer {valid_token}"}, follow_redirects=False)
    if page_path == "/settings":
        assert response.status_code in (302, 303)
        assert response.headers["location"] == "/account/settings"
    else:
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        # When accessed via Bearer header without cookie, response sets access_token cookie
        set_cookie = response.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie


@pytest.mark.parametrize("page_path", ALL_PROTECTED_PAGES)
def test_unauthenticated_page_access_redirects_to_login(client, page_path):
    """Test unauthenticated page access always yields HTTP 302/303 redirect with next param."""
    response = client.get(page_path, follow_redirects=False)
    assert response.status_code in (302, 303)
    location = response.headers["location"]
    assert location.startswith("/login?next=")
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    assert params["next"] == [page_path]


@pytest.mark.parametrize("page_path", ALL_PROTECTED_PAGES)
def test_expired_page_access_redirects_with_notice_and_clears_cookie(client, expired_token, page_path):
    """Test expired user page access yields HTTP 302/303 redirect with notice and clears cookie."""
    response = client.get(page_path, cookies={"access_token": expired_token}, follow_redirects=False)
    assert response.status_code in (302, 303)
    location = response.headers["location"]
    assert "notice=session_expired" in location
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    assert params["next"] == [page_path]
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie


API_ROUTES_RETURNING_401 = [
    ("GET", "/api/dashboard/stats"),
    ("GET", "/api/dashboard/activity"),
    ("GET", "/api/dashboard/products"),
    ("GET", "/api/insights"),
    ("GET", "/api/products"),
    ("GET", "/api/tracked-products"),
    ("GET", "/api/export/prod-uuid-1234/csv"),
    ("GET", "/api/alerts/settings"),
    ("GET", "/api/alerts/pending"),
    ("GET", "/api/alerts/history"),
    ("GET", "/api/account/settings"),
]


@pytest.mark.parametrize("method,endpoint", API_ROUTES_RETURNING_401)
def test_unauthenticated_api_routes_return_401_even_with_html_accept(client, method, endpoint):
    """Verify API routes continue returning HTTP 401 JSON even when Accept: text/html is sent."""
    client_method = getattr(client, method.lower())
    response = client_method(endpoint, headers={"Accept": "text/html,application/xhtml+xml"})
    assert response.status_code == 401
    assert "application/json" in response.headers.get("content-type", "")
    data = response.json()
    assert "detail" in data


def test_login_post_form_data_redirects_to_next_destination(client, valid_token):
    """Test POST /login with form data sets session cookie and redirects to next destination."""
    mock_sb = MagicMock()
    mock_sb.auth.sign_in_with_password.return_value = MagicMock(
        session=MagicMock(access_token=valid_token),
        user=MagicMock(id="user-123", email="user@example.com")
    )

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        response = client.post(
            "/login?next=/alerts/settings",
            data={"email": "user@example.com", "password": "securepassword"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/alerts/settings"
        set_cookie = response.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie
        assert valid_token in set_cookie


def test_login_post_json_data_redirects_to_next_destination(client, valid_token):
    """Test POST /login with JSON payload sets session cookie and redirects to next destination."""
    mock_sb = MagicMock()
    mock_sb.auth.sign_in_with_password.return_value = MagicMock(
        session=MagicMock(access_token=valid_token),
        user=MagicMock(id="user-123", email="user@example.com")
    )

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        response = client.post(
            "/login",
            json={"email": "user@example.com", "password": "securepassword", "next": "/tracked?sort=asc"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/tracked?sort=asc"
        set_cookie = response.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie


def test_login_post_unsafe_next_falls_back_to_dashboard(client, valid_token):
    """Test POST /login with unsafe next parameter safely falls back to /dashboard."""
    mock_sb = MagicMock()
    mock_sb.auth.sign_in_with_password.return_value = MagicMock(
        session=MagicMock(access_token=valid_token),
        user=MagicMock(id="user-123", email="user@example.com")
    )

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        response = client.post(
            "/login?next=//evil.example/phish",
            data={"email": "user@example.com", "password": "securepassword"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"


def test_login_post_invalid_credentials_renders_login_page_with_error(client):
    """Test POST /login with invalid credentials re-renders login page with error flash."""
    mock_sb = MagicMock()
    mock_sb.auth.sign_in_with_password.side_effect = Exception("Invalid login credentials")

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        response = client.post(
            "/login?next=/tracked",
            data={"email": "wrong@example.com", "password": "badpassword"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "Invalid email or password" in response.text
        assert 'data-next="/tracked"' in response.text


def test_api_auth_login_sets_cookie_header(client, valid_token):
    """Test POST /api/auth/login sets access_token cookie on the response."""
    mock_sb = MagicMock()
    mock_sb.auth.sign_in_with_password.return_value = MagicMock(
        session=MagicMock(access_token=valid_token),
        user=MagicMock(id="user-123", email="user@example.com")
    )

    with patch("app.api.routes.auth.get_supabase_client", return_value=mock_sb):
        response = client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "securepassword"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == valid_token
        set_cookie = response.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie
        assert valid_token in set_cookie
